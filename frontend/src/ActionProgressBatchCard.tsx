import { useEffect, useMemo, useState } from "react";

import { ActionPreviewDetails, actionKicker, actionSubject } from "./ActionPreview";
import type { ActionItem } from "./viewModels";

type BatchOperation = {
  effect_id?: string;
  kind: "checklist.update" | "progress.note";
  task_id: string;
  expected_version: number;
  item_id?: string;
  text?: string | null;
  done?: boolean | null;
  summary?: string;
};

function batchOperations(value: unknown): BatchOperation[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const operation = item as Partial<BatchOperation>;
    if (
      (operation.kind !== "checklist.update" && operation.kind !== "progress.note")
      || typeof operation.task_id !== "string"
      || typeof operation.expected_version !== "number"
    ) return [];
    return [{ ...operation } as BatchOperation];
  });
}

export function ActionProgressBatchCard({
  action,
  onCommand,
}: {
  action: ActionItem;
  onCommand: (commandId: string, payload?: Record<string, unknown>) => Promise<void>;
}) {
  const contract = action.edit_contract?.editor === "task_progress_batch" ? action.edit_contract : null;
  const serializedBase = JSON.stringify(contract?.values.operations ?? []);
  const base = useMemo(() => batchOperations(JSON.parse(serializedBase)), [serializedBase]);
  const [operations, setOperations] = useState(base);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = action.commands?.find((command) => command.id === "confirm");
  const reject = action.commands?.find((command) => command.id === "reject");

  useEffect(() => {
    setOperations(base);
    if (action.state !== "pending") setEditing(false);
  }, [action.state, base]);

  async function run(command: string, payload?: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await onCommand(command, payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "업무 진행을 반영하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function originalIndex(operation: BatchOperation, fallback: number): number {
    const index = base.findIndex((item) => (
      operation.effect_id && item.effect_id === operation.effect_id
    ) || (!operation.effect_id && item.task_id === operation.task_id));
    return index >= 0 ? index : fallback;
  }

  function labelFor(operation: BatchOperation, index: number): string {
    return action.preview?.[originalIndex(operation, index)]?.label ?? `업무 ${index + 1}`;
  }

  return (
    <section className="ax-action-card action-progress-batch-card" data-action-id={action.action_id} data-state={action.state}>
      <span className="ax-card-kicker">{actionKicker(action)}{action.state === "pending" ? " · 승인 필요" : ""}</span>
      <b>{actionSubject(action)}</b>
      {editing ? (
        <div className="action-progress-batch-editor" aria-label="업무 진행 수정">
          {operations.map((operation, index) => (
            <fieldset className="action-progress-batch-row" key={operation.effect_id ?? `${operation.task_id}:${index}`}>
              <legend>{labelFor(operation, index)}</legend>
              {operation.kind === "progress.note" ? (
                <label>
                  <span>진행 내용</span>
                  <input
                    aria-label={`진행 내용 - ${labelFor(operation, index)}`}
                    disabled={busy}
                    onChange={(event) => setOperations((current) => current.map((item, itemIndex) => (
                      itemIndex === index ? { ...item, summary: event.target.value } : item
                    )))}
                    value={operation.summary ?? ""}
                  />
                </label>
              ) : (
                <label className="action-progress-batch-check">
                  <input
                    checked={operation.done === true}
                    disabled={busy}
                    onChange={(event) => setOperations((current) => current.map((item, itemIndex) => (
                      itemIndex === index ? { ...item, done: event.target.checked } : item
                    )))}
                    type="checkbox"
                  />
                  <span>{action.preview?.[originalIndex(operation, index)]?.value?.replace(/ · (완료|완료 해제)( · .*됨)?$/, "") || "체크리스트 단계"}</span>
                </label>
              )}
              <button
                className="btn h30 ghost"
                disabled={busy}
                onClick={() => setOperations((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                type="button"
              >
                제외
              </button>
            </fieldset>
          ))}
          {operations.length === 0 && <p>반영할 항목을 하나 이상 남겨 주세요.</p>}
        </div>
      ) : (
        <ActionPreviewDetails action={action} defaultOpen={action.state === "pending"} />
      )}
      {error && <p className="action-progress-batch-error" role="alert">{error}</p>}
      <small className={action.state}>
        {action.state === "pending"
          ? "확인 필요 · 승인해야 반영됩니다"
          : action.state === "approved"
            ? action.result_summary ?? "반영 완료"
            : "거절됨"}
      </small>
      {action.state === "pending" && contract && (
        <div className="action-progress-batch-actions">
          {editing ? (
            <>
              <button className="btn h30 ghost" disabled={busy} onClick={() => { setOperations(base); setEditing(false); setError(null); }} type="button">취소</button>
              <button
                className="btn h30 primary"
                disabled={busy || operations.length === 0 || operations.some((item) => item.kind === "progress.note" && !item.summary?.trim())}
                onClick={() => void run("confirm", {
                  base_submission_version: contract.base_submission_version,
                  draft: { operations },
                })}
                type="button"
              >
                {busy ? "저장 중…" : "저장"}
              </button>
            </>
          ) : (
            <>
              <button className="btn h30 ghost" disabled={busy} onClick={() => setEditing(true)} type="button">수정</button>
              {confirm && (
                <button
                  className="btn h30 primary"
                  disabled={busy}
                  onClick={() => void run(confirm.id, { base_submission_version: contract.base_submission_version })}
                  type="button"
                >
                  {busy ? "반영 중…" : confirm.label}
                </button>
              )}
              {reject && <button className="btn h30 ghost" disabled={busy} onClick={() => void run(reject.id)} type="button">{reject.label}</button>}
            </>
          )}
        </div>
      )}
    </section>
  );
}
