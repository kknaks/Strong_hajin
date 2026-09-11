import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  cancelConversation,
  createConversation,
  decideAction,
  getConversation,
  getConversations,
  retryConversationTurn,
  sendConversationMessage,
} from "../api";
import { createIdempotencyKey } from "../idempotency";
import type { Conversation, ConversationContextReference } from "../viewModels";

/**
 * Conversation state for the AX chat: server projections stay the source of truth; this hook only guards
 * against stale overlapping responses (per-persona generations), polls while a turn is active, keeps optimistic
 * local fragments until the server projection contains the same idempotency identity, and holds one unsent draft
 * per conversation so switching sessions never mixes or loses text.
 */

export type LocalFragment = {
  local_id: string;
  conversation_id: string;
  body: string;
  state: "sending" | "accepted" | "failed";
  error?: string;
  context: ConversationContextReference[];
  idempotency_key: string;
  follow_up_candidate_id?: string;
};

export type ListStatus = "idle" | "loading" | "ready" | "error";

/** Draft key used before any conversation exists; moved onto the conversation when one is created. */
const DRAFT_STORAGE_KEY = "scax.ax.drafts";

/** Unsent drafts survive a reload for the person who typed them; anything unreadable is simply no drafts. */
function readStoredDrafts(): Record<string, string> {
  try {
    const stored = window.localStorage.getItem(DRAFT_STORAGE_KEY);
    const parsed = stored ? (JSON.parse(stored) as unknown) : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(([, value]) => typeof value === "string"),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}

function writeStoredDrafts(drafts: Record<string, string>): void {
  try {
    const kept = Object.fromEntries(Object.entries(drafts).filter(([, value]) => value.trim() !== ""));
    window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // A browser that refuses storage keeps the draft for this page only; nothing else changes.
  }
}

export const NEW_DRAFT_KEY = "__new__";

export { createIdempotencyKey };

/** Legacy empty rows are not meaningful sessions and must not replace the local new-chat state. */
function conversationHasActivity(conversation: Conversation): boolean {
  return conversation.messages.length > 0
    || conversation.turns.length > 0
    || conversation.tool_invocations.length > 0
    || (conversation.actions?.length ?? 0) > 0;
}

export function useConversations({ personaId, isOpen, onError }: { personaId: string; isOpen: boolean; onError: (message: string) => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [listStatus, setListStatus] = useState<ListStatus>("idle");
  const [localFragments, setLocalFragments] = useState<LocalFragment[]>([]);
  // What someone typed and has not sent yet, per conversation. It is a convenience for this browser only: it never
  // reaches the server, and a browser that refuses storage simply loses it rather than breaking the chat.
  const [drafts, setDrafts] = useState<Record<string, string>>(readStoredDrafts);
  const activeConversationRef = useRef<Conversation | null>(null);
  const listRequestGeneration = useRef(0);
  const detailRequestGeneration = useRef(0);
  const newConversationGeneration = useRef(0);
  // A deliberate new-chat choice is not the same as the initial `null` before the list arrives. Late list reads may
  // still populate history, but must not silently replace the blank composer the person just chose.
  const explicitNewConversationRef = useRef(false);
  const firstSendRequestRef = useRef<Promise<boolean> | null>(null);
  const activeSendRequestsRef = useRef(new Map<string, Promise<boolean>>());

  const reset = useCallback(() => {
    listRequestGeneration.current += 1;
    detailRequestGeneration.current += 1;
    newConversationGeneration.current += 1;
    firstSendRequestRef.current = null;
    activeSendRequestsRef.current.clear();
    explicitNewConversationRef.current = false;
    setConversations([]);
    activeConversationRef.current = null;
    setActiveConversation(null);
    setLocalFragments([]);
    setDrafts({});
    writeStoredDrafts({});
    setListStatus("idle");
  }, []);

  const refreshConversations = useCallback(async () => {
    const requestGeneration = ++listRequestGeneration.current;
    setListStatus((current) => (current === "ready" ? current : "loading"));
    let items: Conversation[];
    try {
      items = await getConversations();
    } catch (reason) {
      if (requestGeneration === listRequestGeneration.current) setListStatus((current) => (current === "ready" ? current : "error"));
      throw reason;
    }
    if (requestGeneration !== listRequestGeneration.current) return;
    const current = activeConversationRef.current;
    const matchingItem = current ? items.find((item) => item.conversation_id === current.conversation_id) : undefined;
    const fallback = items.find(conversationHasActivity) ?? null;
    const nextActive = !current
      ? explicitNewConversationRef.current ? null : fallback
      : !matchingItem
        ? fallback
        : matchingItem.version > current.version
          ? matchingItem
          : current;
    if (current && !matchingItem) detailRequestGeneration.current += 1;
    activeConversationRef.current = nextActive;
    setActiveConversation(nextActive);
    setConversations(items.map((item) => (current && item.conversation_id === current.conversation_id && item.version <= current.version ? current : item)));
    setListStatus("ready");
  }, [personaId]);

  const refreshActiveConversation = useCallback(async () => {
    const active = activeConversationRef.current;
    if (!active) return;
    const conversationId = active.conversation_id;
    const requestGeneration = ++detailRequestGeneration.current;
    const next = await getConversation(conversationId);
    if (requestGeneration !== detailRequestGeneration.current) return;
    const current = activeConversationRef.current;
    if (current?.conversation_id !== conversationId) return;
    const projection = next.version >= current.version ? next : current;
    activeConversationRef.current = projection;
    setActiveConversation(projection);
    setConversations((items) => items.map((item) => (item.conversation_id === conversationId && item.version <= projection.version ? projection : item)));
  }, [personaId]);

  useEffect(() => {
    if (!isOpen) return;
    void refreshConversations().catch(() => onError("AX 대화를 불러오지 못했습니다."));
  }, [isOpen, refreshConversations, onError]);

  const isProcessing = useMemo(
    () =>
      Boolean(
        activeConversation?.turns.some((turn) => turn.state === "pending" || turn.state === "running") ||
          activeConversation?.messages.some((item) => item.state === "queued"),
      ),
    [activeConversation],
  );

  useEffect(() => {
    // A started Turn keeps refreshing after the drawer closes, so `답변 도착` can only follow a real completion.
    if (!isProcessing) return;
    const timer = window.setInterval(() => {
      void refreshActiveConversation().catch(() => onError("AX 상태를 갱신하지 못했습니다."));
    }, 800);
    return () => window.clearInterval(timer);
  }, [isProcessing, refreshActiveConversation, onError]);

  // Convergence: a local fragment disappears only once the server projection carries its idempotency key.
  // The visible list is derived at render time (no one-frame duplicate); the effect only trims stored state.
  const projectedKeys = useMemo(
    () => new Set((activeConversation?.messages ?? []).map((item) => item.idempotency_key).filter(Boolean)),
    [activeConversation],
  );
  useEffect(() => {
    if (!activeConversation) return;
    setLocalFragments((items) => items.filter((item) => !(item.conversation_id === activeConversation.conversation_id && projectedKeys.has(item.idempotency_key))));
  }, [activeConversation, projectedKeys]);
  const visibleLocalFragments = useMemo(
    () => localFragments.filter((item) => item.conversation_id === activeConversation?.conversation_id && !projectedKeys.has(item.idempotency_key)),
    [activeConversation, localFragments, projectedKeys],
  );

  const adopt = useCallback((conversation: Conversation) => {
    listRequestGeneration.current += 1;
    detailRequestGeneration.current += 1;
    activeConversationRef.current = conversation;
    explicitNewConversationRef.current = false;
    setConversations((items) => [conversation, ...items.filter((item) => item.conversation_id !== conversation.conversation_id)]);
    setActiveConversation(conversation);
  }, []);

  const select = useCallback((conversation: Conversation) => {
    detailRequestGeneration.current += 1;
    activeConversationRef.current = conversation;
    explicitNewConversationRef.current = false;
    setActiveConversation(conversation);
  }, []);

  const createForSend = useCallback(async (generation: number): Promise<Conversation | null> => {
    try {
      const conversation = await createConversation();
      if (generation !== newConversationGeneration.current) return null;
      adopt(conversation);
      // A draft typed before the conversation existed follows it.
      setDrafts((current) => {
        if (!current[NEW_DRAFT_KEY]) return current;
        const { [NEW_DRAFT_KEY]: pending, ...rest } = current;
        return { ...rest, [conversation.conversation_id]: pending };
      });
      return conversation;
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "새 대화를 만들지 못했습니다.");
      return null;
    }
  }, [adopt, onError]);

  const start = useCallback(async (): Promise<null> => {
    detailRequestGeneration.current += 1;
    newConversationGeneration.current += 1;
    firstSendRequestRef.current = null;
    explicitNewConversationRef.current = true;
    activeConversationRef.current = null;
    setActiveConversation(null);
    return null;
  }, []);

  /** Optimistic send: the fragment shows immediately; after 202 it stays as `accepted` until the projection converges. */
  const send = useCallback(
    async (conversationId: string, body: string, context: ConversationContextReference[], existing?: LocalFragment, followUpCandidateId?: string) => {
      const candidateId = existing?.follow_up_candidate_id ?? followUpCandidateId;
      const fragment: LocalFragment =
        existing ?? {
          local_id: createIdempotencyKey(),
          conversation_id: conversationId,
          body,
          state: "sending",
          context,
          idempotency_key: candidateId ? `follow-up:${candidateId}` : createIdempotencyKey(),
          follow_up_candidate_id: candidateId,
        };
      setLocalFragments((items) => [...items.filter((item) => item.local_id !== fragment.local_id), { ...fragment, state: "sending", error: undefined }]);
      try {
        await sendConversationMessage(conversationId, fragment.body, fragment.context, fragment.idempotency_key, candidateId);
        setLocalFragments((items) => items.map((item) => (item.local_id === fragment.local_id ? { ...item, state: "accepted" } : item)));
        await refreshActiveConversation();
        return true;
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "AX 메시지를 접수하지 못했습니다.";
        setLocalFragments((items) => items.map((item) => (item.local_id === fragment.local_id ? { ...item, state: "failed", error: message } : item)));
        onError(message);
        return false;
      }
    },
    [onError, refreshActiveConversation],
  );

  const retryFragment = useCallback((fragment: LocalFragment) => send(fragment.conversation_id, fragment.body, fragment.context, fragment), [send]);
  const discardFragment = useCallback((localId: string) => setLocalFragments((items) => items.filter((item) => item.local_id !== localId)), []);

  /** Executes the canonical effect exactly once (server idempotency). Projection refresh is the caller's step so a
   *  failed re-read is never mistaken for a failed approval. */
  const decide = useCallback(async (
    actionId: string,
    expectedVersion: number,
    decision: string,
    payload: { base_submission_version?: number; draft?: Record<string, unknown> } = {},
  ) => {
    await decideAction(actionId, expectedVersion, decision, payload);
  }, []);

  const cancelActive = useCallback(async () => {
    const active = activeConversationRef.current;
    if (!active) return;
    try {
      await cancelConversation(active.conversation_id, active.version);
      await refreshActiveConversation();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "AX 실행을 취소하지 못했습니다.");
    }
  }, [onError, refreshActiveConversation]);

  /** One-click retry of a failed/cancelled turn: the server creates a linked new Turn (idempotent per failed turn). */
  const retryTurn = useCallback(
    async (turnId: string) => {
      const active = activeConversationRef.current;
      if (!active) return;
      try {
        await retryConversationTurn(active.conversation_id, turnId);
        await refreshActiveConversation();
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : "다시 시도하지 못했습니다.");
      }
    },
    [onError, refreshActiveConversation],
  );

  const draftKey = activeConversation?.conversation_id ?? NEW_DRAFT_KEY;
  const setDraft = useCallback(
    (value: string, key?: string) =>
      setDrafts((current) => {
        const next = { ...current, [key ?? draftKey]: value };
        writeStoredDrafts(next);
        return next;
      }),
    [draftKey],
  );

  const sendCurrent = useCallback((body: string, context: ConversationContextReference[]): Promise<boolean> => {
    const active = activeConversationRef.current;
    if (active) {
      const requestKey = JSON.stringify([active.conversation_id, body, context]);
      const inFlight = activeSendRequestsRef.current.get(requestKey);
      if (inFlight) return inFlight;
      setDraft("", active.conversation_id);
      const request = send(active.conversation_id, body, context);
      activeSendRequestsRef.current.set(requestKey, request);
      void request.then(
        () => { if (activeSendRequestsRef.current.get(requestKey) === request) activeSendRequestsRef.current.delete(requestKey); },
        () => { if (activeSendRequestsRef.current.get(requestKey) === request) activeSendRequestsRef.current.delete(requestKey); },
      );
      return request;
    }
    if (firstSendRequestRef.current) return firstSendRequestRef.current;
    const generation = newConversationGeneration.current;
    const request = (async () => {
      const conversation = await createForSend(generation);
      if (!conversation) return false;
      setDraft("", conversation.conversation_id);
      return send(conversation.conversation_id, body, context);
    })();
    firstSendRequestRef.current = request;
    void request.then(
      () => { if (firstSendRequestRef.current === request) firstSendRequestRef.current = null; },
      () => { if (firstSendRequestRef.current === request) firstSendRequestRef.current = null; },
    );
    return request;
  }, [createForSend, send, setDraft]);

  return {
    conversations,
    activeConversation,
    listStatus,
    isProcessing,
    localFragments: visibleLocalFragments,
    draft: drafts[draftKey] ?? "",
    draftKey,
    setDraft,
    reset,
    refreshConversations,
    refreshActiveConversation,
    adopt,
    select,
    start,
    send,
    sendCurrent,
    retryFragment,
    discardFragment,
    decide,
    cancelActive,
    retryTurn,
  };
}
