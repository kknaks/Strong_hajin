import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cancelConversation, createConversation, decideAction, getConversation, getConversations, sendConversationMessage } from "../api";
import type { Conversation, ConversationContextReference } from "../viewModels";

/**
 * Conversation state for the AX chat: server projections stay the source of truth; this hook only guards
 * against stale overlapping responses (per-persona generations), polls while a turn is active, and keeps
 * optimistic local fragments until the server accepts them.
 */

export type LocalFragment = {
  local_id: string;
  conversation_id: string;
  body: string;
  state: "sending" | "failed";
  error?: string;
  context: ConversationContextReference[];
  idempotency_key: string;
};

export type ListStatus = "idle" | "loading" | "ready" | "error";

export function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `ax-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useConversations({ personaId, isOpen, onError }: { personaId: string; isOpen: boolean; onError: (message: string) => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [listStatus, setListStatus] = useState<ListStatus>("idle");
  const [localFragments, setLocalFragments] = useState<LocalFragment[]>([]);
  const activeConversationRef = useRef<Conversation | null>(null);
  const listRequestGeneration = useRef(0);
  const detailRequestGeneration = useRef(0);

  const reset = useCallback(() => {
    listRequestGeneration.current += 1;
    detailRequestGeneration.current += 1;
    setConversations([]);
    activeConversationRef.current = null;
    setActiveConversation(null);
    setLocalFragments([]);
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
    const nextActive = !current
      ? items[0] ?? null
      : !matchingItem
        ? items[0] ?? null
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
    if (!isOpen || !isProcessing) return;
    const timer = window.setInterval(() => {
      void refreshActiveConversation().catch(() => onError("AX 상태를 갱신하지 못했습니다."));
    }, 800);
    return () => window.clearInterval(timer);
  }, [isOpen, isProcessing, refreshActiveConversation, onError]);

  const adopt = useCallback((conversation: Conversation) => {
    listRequestGeneration.current += 1;
    detailRequestGeneration.current += 1;
    activeConversationRef.current = conversation;
    setConversations((items) => [conversation, ...items.filter((item) => item.conversation_id !== conversation.conversation_id)]);
    setActiveConversation(conversation);
  }, []);

  const select = useCallback((conversation: Conversation) => {
    detailRequestGeneration.current += 1;
    activeConversationRef.current = conversation;
    setActiveConversation(conversation);
  }, []);

  const start = useCallback(async (): Promise<Conversation | null> => {
    try {
      const conversation = await createConversation();
      adopt(conversation);
      return conversation;
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "새 대화를 만들지 못했습니다.");
      return null;
    }
  }, [adopt, onError]);

  /** Optimistic send: the fragment shows immediately as `sending`; on 202 the server projection replaces it. */
  const send = useCallback(
    async (conversationId: string, body: string, context: ConversationContextReference[], existing?: LocalFragment) => {
      const fragment: LocalFragment =
        existing ?? { local_id: createIdempotencyKey(), conversation_id: conversationId, body, state: "sending", context, idempotency_key: createIdempotencyKey() };
      setLocalFragments((items) => [...items.filter((item) => item.local_id !== fragment.local_id), { ...fragment, state: "sending", error: undefined }]);
      try {
        await sendConversationMessage(conversationId, fragment.body, fragment.context, fragment.idempotency_key);
        setLocalFragments((items) => items.filter((item) => item.local_id !== fragment.local_id));
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

  const decide = useCallback(
    async (actionId: string, expectedVersion: number, decision: "approve" | "reject") => {
      await decideAction(actionId, expectedVersion, decision);
      await refreshActiveConversation();
    },
    [refreshActiveConversation],
  );

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

  return {
    conversations,
    activeConversation,
    listStatus,
    isProcessing,
    localFragments: localFragments.filter((item) => item.conversation_id === activeConversation?.conversation_id),
    reset,
    refreshConversations,
    refreshActiveConversation,
    adopt,
    select,
    start,
    send,
    retryFragment,
    discardFragment,
    decide,
    cancelActive,
  };
}
