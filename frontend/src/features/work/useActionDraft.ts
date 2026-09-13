import { useCallback, useEffect, useRef, useState } from "react";

export const ACTION_DRAFT_STORAGE_KEY = "scax.ax.action-drafts.v1";

type JsonDraft = Record<string, unknown>;

type StoredActionDraft = {
  principal_id: string;
  action_item_id: string;
  base_submission_version: number;
  draft: JsonDraft;
};

export type StaleActionDraft<T> = {
  baseSubmissionVersion: number;
  draft: T;
};

function isRecord(value: unknown): value is JsonDraft {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function actionDraftStorageId(principalId: string, actionId: string, baseSubmissionVersion: number): string {
  return [principalId, actionId, String(baseSubmissionVersion)].map(encodeURIComponent).join("::");
}

function readStore(): Record<string, StoredActionDraft> {
  try {
    const raw = window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) as unknown : null;
    if (!isRecord(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([, candidate]) => {
      if (!isRecord(candidate) || !isRecord(candidate.draft)) return false;
      return typeof candidate.principal_id === "string"
        && typeof candidate.action_item_id === "string"
        && Number.isInteger(candidate.base_submission_version);
    })) as Record<string, StoredActionDraft>;
  } catch {
    return {};
  }
}

function writeStore(records: Record<string, StoredActionDraft>): void {
  try {
    window.localStorage.setItem(ACTION_DRAFT_STORAGE_KEY, JSON.stringify(records));
  } catch {
    // Storage is a browser convenience. Refusal must not block the in-memory editor.
  }
}

function recordsFor(principalId: string, actionId: string): StoredActionDraft[] {
  return Object.values(readStore())
    .filter((record) => record.principal_id === principalId && record.action_item_id === actionId)
    .sort((left, right) => right.base_submission_version - left.base_submission_version);
}

function clearStoredActionDraft(principalId: string, actionId: string): void {
  if (!principalId) return;
  const kept = Object.fromEntries(Object.entries(readStore()).filter(([, record]) => (
    record.principal_id !== principalId || record.action_item_id !== actionId
  )));
  writeStore(kept);
}

function writeStoredActionDraft(
  principalId: string,
  actionId: string,
  baseSubmissionVersion: number,
  draft: JsonDraft,
): void {
  if (!principalId) return;
  const kept = Object.fromEntries(Object.entries(readStore()).filter(([, record]) => (
    record.principal_id !== principalId || record.action_item_id !== actionId
  )));
  const id = actionDraftStorageId(principalId, actionId, baseSubmissionVersion);
  writeStore({
    ...kept,
    [id]: {
      principal_id: principalId,
      action_item_id: actionId,
      base_submission_version: baseSubmissionVersion,
      draft,
    },
  });
}

function sameDraft(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/**
 * Browser-only recovery for one unsubmitted Action form. The server Submission remains authoritative: a local draft
 * is keyed by its principal and base Submission, and a changed base is surfaced as a choice rather than auto-merged.
 */
export function useActionDraft<T extends object>({
  principalId,
  actionId,
  baseSubmissionVersion,
  baseDraft,
  pending,
  sanitize,
}: {
  principalId: string;
  actionId: string;
  baseSubmissionVersion: number;
  baseDraft: T;
  pending: boolean;
  sanitize: (value: JsonDraft) => T;
}) {
  const initial = useRef<{ exact?: StoredActionDraft; stale?: StoredActionDraft } | null>(null);
  if (initial.current === null) {
    const records = principalId ? recordsFor(principalId, actionId) : [];
    initial.current = {
      exact: records.find((record) => record.base_submission_version === baseSubmissionVersion),
      stale: records.find((record) => record.base_submission_version !== baseSubmissionVersion),
    };
  }
  const initialRecord = initial.current.exact ?? initial.current.stale;
  const initialDraft = useRef<T | null>(null);
  if (initialDraft.current === null) initialDraft.current = initialRecord ? sanitize(initialRecord.draft) : baseDraft;
  const [draft, setDraftState] = useState<T>(initialDraft.current);
  const [restored, setRestored] = useState(Boolean(initialRecord));
  const [stale, setStale] = useState<StaleActionDraft<T> | null>(() => (
    initial.current?.stale && !initial.current.exact
      ? { baseSubmissionVersion: initial.current.stale.base_submission_version, draft: initialDraft.current! }
      : null
  ));
  const previousVersion = useRef(baseSubmissionVersion);
  const previousBase = useRef(baseDraft);

  // Rewrite an exact recovered draft through the editor sanitizer so unknown or obsolete fields do not survive.
  useEffect(() => {
    if (!initial.current?.exact || !principalId) return;
    writeStoredActionDraft(
      principalId,
      actionId,
      baseSubmissionVersion,
      initialDraft.current as unknown as JsonDraft,
    );
  }, [actionId, baseSubmissionVersion, principalId]);

  useEffect(() => {
    if (pending) return;
    clearStoredActionDraft(principalId, actionId);
    setRestored(false);
    setStale(null);
  }, [actionId, pending, principalId]);

  useEffect(() => {
    if (previousVersion.current === baseSubmissionVersion) return;
    const localChanged = !sameDraft(draft, previousBase.current);
    const stored = principalId
      ? recordsFor(principalId, actionId).find((record) => record.base_submission_version !== baseSubmissionVersion)
      : undefined;
    if (pending && (localChanged || stored)) {
      const localDraft = localChanged ? draft : sanitize(stored!.draft);
      setDraftState(localDraft);
      setRestored(true);
      setStale({
        baseSubmissionVersion: stored?.base_submission_version ?? previousVersion.current,
        draft: localDraft,
      });
    } else {
      setDraftState(baseDraft);
      setRestored(false);
      setStale(null);
    }
    previousVersion.current = baseSubmissionVersion;
    previousBase.current = baseDraft;
    // The transition is intentionally keyed only by the server's base Submission identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseSubmissionVersion]);

  const setDraft = useCallback((next: T) => {
    setDraftState(next);
    const changed = !sameDraft(next, baseDraft);
    setRestored(changed);
    if (!changed) {
      clearStoredActionDraft(principalId, actionId);
      return;
    }
    writeStoredActionDraft(principalId, actionId, baseSubmissionVersion, next as unknown as JsonDraft);
  }, [actionId, baseDraft, baseSubmissionVersion, principalId]);

  const clear = useCallback(() => {
    clearStoredActionDraft(principalId, actionId);
    setRestored(false);
    setStale(null);
  }, [actionId, principalId]);

  const reset = useCallback(() => {
    clearStoredActionDraft(principalId, actionId);
    setDraftState(baseDraft);
    setRestored(false);
    setStale(null);
  }, [actionId, baseDraft, principalId]);

  const continueWithLocal = useCallback(() => {
    if (!stale) return;
    clearStoredActionDraft(principalId, actionId);
    setDraftState(stale.draft);
    setStale(null);
    setRestored(true);
    if (!sameDraft(stale.draft, baseDraft)) {
      writeStoredActionDraft(
        principalId,
        actionId,
        baseSubmissionVersion,
        stale.draft as unknown as JsonDraft,
      );
    }
  }, [actionId, baseDraft, baseSubmissionVersion, principalId, stale]);

  return {
    draft,
    setDraft,
    restored,
    stale,
    clear,
    reset,
    startFromLatest: reset,
    continueWithLocal,
  };
}
