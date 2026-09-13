import { useEffect, useMemo, useState } from "react";

import { Button } from "../../ds/Button";
import { CommandConfirmationForm } from "./CommandConfirmationForm";
import { getActionItem, runActionCommand } from "../../lib/api";
import { ActionPreviewDetails } from "./ActionPreview";
import { DateField } from "../../ds/DateField";
import { datePickerLabel, formatDate, formatDateTime, formatMonthLong, personName, seoulToday, weekdayNames } from "../../lib/labels";
import { Drawer } from "../../ds/Modal";
import { StatusText } from "../work/WorkModals";
import { Skeleton } from "../../ds/Skeleton";
import type {
  ActionCommand,
  ActionDiscussionEntry,
  ActionItemDetail,
  ActionItemEnvelope,
  ActionRound,
  Persona,
} from "../../lib/viewModels";

/**
 * One judgement, however it was raised. Everything here is the server's projection: the subject, the question, the
 * preview rows, and which commands this principal may run right now. The client never derives a control from the kind.
 */

const statusLabel: Record<ActionItemEnvelope["status"], string> = {
  awaiting_review: "판단 대기",
  awaiting_revision: "조정 필요",
  resolved: "판단 완료",
};

const statusTone: Record<ActionItemEnvelope["status"], string> = {
  awaiting_review: "pending",
  awaiting_revision: "negotiating",
  resolved: "accepted",
};

const decisionLabel: Record<string, string> = {
  accept: "수락",
  negotiate: "조정 요청",
  reject: "거절",
  decline: "거절",
  confirm: "확정",
  approve: "승인",
  approved: "승인",
  rejected: "거절",
};

/** Fields a requester may change when answering an adjustment; the server re-validates each one. */
const REVISABLE = [
  { id: "title", label: "요청할 업무", type: "text" as const },
  { id: "description", label: "요청 내용", type: "textarea" as const },
  { id: "due_date", label: "희망 기한", type: "date" as const },
];

function nameOf(personas: Persona[], id: string): string {
  if (id === "ax") return "AX";
  const found = personas.find((persona) => persona.id === id);
  return found ? personName(found.display_name) : id;
}

function fieldValue(snapshot: Record<string, unknown>, id: string): string {
  const value = snapshot[id];
  return value === null || value === undefined ? "" : String(value);
}

/** Read-only display of one revisable field: dates go through the shared formatter, never raw ISO. */
function displayValue(fieldId: string, value: unknown): string {
  const text = value === null || value === undefined || value === "" ? "" : String(value);
  if (!text) return "없음";
  return REVISABLE.find((field) => field.id === fieldId)?.type === "date" ? formatDate(text) : text;
}

export function ActionItemCard({
  item,
  personas,
  onOpen,
}: {
  item: ActionItemEnvelope;
  personas: Persona[];
  onOpen: (item: ActionItemEnvelope) => void;
}) {
  return (
    <article className="task-card openable" data-action-item-id={item.action_item_id} data-kind={item.kind} onClick={() => onOpen(item)}>
      <div className="task-card-top">
        <small className="task-card-kicker">{item.operation_label}</small>
        <StatusText label={statusLabel[item.status]} state={statusTone[item.status]} />
      </div>
      <b className="task-card-title">{item.subject}</b>
      <p className="task-card-memo">{item.current_question}</p>
      <div className="task-card-foot">
        <span>{item.submission_version > 1 ? `${item.submission_version}회차` : "첫 회차"}</span>
        <span className="task-card-people">{item.waiting_on ? `${personName(item.waiting_on.display_name)} 차례` : "—"}</span>
      </div>
      <div className="task-card-actions">
        <Button variant="solid" tone="primary" size="sm" onClick={(event) => {
            event.stopPropagation();
            onOpen(item);
          }}
          type="button"
        >
          판단하기
        </Button>
      </div>
    </article>
  );
}

/**
 * What a round stands on, and whether the answer given on it was given on the same thing. The manifest carries
 * identities rather than names, so this counts and compares instead of pretending to list documents.
 */
function basisLine(round: ActionRound): string {
  const evidence = round.evidence ?? [];
  if (evidence.length === 0) return "근거 없음";
  const basis = evidence.filter((entry) => entry.evidence_role === "decision_basis").length;
  const parts = [`근거 ${evidence.length}건`, `판단 근거 ${basis} · 보조 ${evidence.length - basis}`];
  const answered = round.decisions.filter((decision) => decision.evidence_hash);
  if (answered.length > 0) {
    const same = answered.every((decision) => decision.evidence_hash === round.evidence_hash);
    parts.push(same ? "판단 당시 근거와 같습니다" : "판단 뒤 근거가 달라졌습니다");
  }
  return parts.join(" · ");
}

function RoundHistory({ rounds, personas }: { rounds: ActionRound[]; personas: Persona[] }) {
  if (rounds.length === 0) return null;
  return (
    <section aria-label="회차 기록" className="drawer-section">
      <h4>
        회차 기록 <small className="t-meta">· {rounds.length}회차 · 이전 회차는 수정되지 않습니다</small>
      </h4>
      <ol className="round-list">
        {rounds.map((round) => (
          <li className="round-row" data-submission-version={round.submission_version} key={round.submission_id}>
            <div className="round-head">
              <b>{round.submission_version}회차</b>
              <span className="t-meta">
                {nameOf(personas, round.submitted_by)} · {formatDateTime(round.submitted_at)}
              </span>
            </div>
            <dl className="round-snapshot">
              {REVISABLE.filter((field) => fieldValue(round.snapshot, field.id)).map((field) => (
                <div className="round-snapshot-row" key={field.id}>
                  <dt>{field.label}</dt>
                  <dd>{displayValue(field.id, fieldValue(round.snapshot, field.id))}</dd>
                </div>
              ))}
            </dl>
            {round.diff && Object.keys(round.diff).length > 0 && (
              <dl className="round-diff">
                {Object.entries(round.diff).map(([field, change]) => (
                  <div className="round-diff-row" key={field}>
                    <dt>{REVISABLE.find((entry) => entry.id === field)?.label ?? field}</dt>
                    <dd>
                      <s>{displayValue(field, change.before)}</s> → <b>{displayValue(field, change.after)}</b>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
            <p className="t-meta">{basisLine(round)}</p>
            {round.decisions.map((decision) => (
              <p className="round-decision" key={decision.review_decision_id}>
                <b>{nameOf(personas, decision.actor_member_id)}</b> {decisionLabel[decision.decision] ?? decision.decision}
                {decision.reason ? ` · ${decision.reason}` : ""}
                <span className="t-meta"> · {formatDateTime(decision.decided_at)}</span>
              </p>
            ))}
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * What the reviewer asked for, on the requester's first screen: the required reason, and the fields they proposed
 * changing. It is a proposal — the round below still holds exactly what the requester submitted.
 */
function AdjustmentAsk({
  reason,
  suggested,
  actor,
  personas,
}: {
  reason: string | null;
  suggested: Record<string, string>;
  actor: string;
  personas: Persona[];
}) {
  const fields = REVISABLE.filter((field) => suggested[field.id]);
  if (!reason && fields.length === 0) return null;
  return (
    <section aria-label="조정 요청" className="drawer-section adjustment-ask">
      <h4>
        조정 요청 <small className="t-meta">· {nameOf(personas, actor)}</small>
      </h4>
      {reason && <p className="adjustment-reason">{reason}</p>}
      {fields.length > 0 && (
        <dl className="round-snapshot">
          {fields.map((field) => (
            <div className="round-snapshot-row" key={field.id}>
              <dt>{field.label}</dt>
              <dd>{displayValue(field.id, suggested[field.id])}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

/** The comment thread. It travels with the judgement and never moves it, so it carries no control of its own. */
function Discussion({ entries, personas }: { entries: ActionDiscussionEntry[]; personas: Persona[] }) {
  if (entries.length === 0) return null;
  return (
    <section aria-label="논의" className="drawer-section">
      <h4>
        논의 <small className="t-meta">· {entries.length}건 · 상태를 바꾸지 않습니다</small>
      </h4>
      <ol className="discussion-list">
        {entries.map((entry) => (
          <li className="discussion-row" key={entry.comment_id}>
            <div className="round-head">
              <b>{nameOf(personas, entry.author_member_id)}</b>
              <span className="t-meta">{formatDateTime(entry.created_at)}</span>
            </div>
            <p className="discussion-body">{entry.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * The revision form: prefilled with the round being revised, with the diff shown before it is sent. A revision that
 * changes nothing is not a round, so the submit stays disabled until something differs.
 */
function RevisionForm({
  round,
  draft,
  suggested,
  onChange,
}: {
  round: ActionRound;
  draft: Record<string, string>;
  /** The reviewer's proposal, offered as a starting point. Applying it is the requester's own act. */
  suggested: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const changed = REVISABLE.filter((field) => draft[field.id] !== fieldValue(round.snapshot, field.id));
  const proposedFields = REVISABLE.filter((field) => suggested[field.id]);
  return (
    <section aria-label="수정안" className="drawer-section">
      <h4>
        수정안
        {proposedFields.length > 0 && (
          <Button variant="text" size="sm" onClick={() => onChange({ ...draft, ...Object.fromEntries(proposedFields.map((field) => [field.id, suggested[field.id]])) })}
            type="button"
          >
            제안대로 채우기
          </Button>
        )}
      </h4>
      <div className="form-grid">
        {REVISABLE.map((field) => (
          <div key={field.id}>
            {field.type !== "date" && <label htmlFor={`revision-${field.id}`}>{field.label}</label>}
            {field.type === "textarea" ? (
              <textarea
                id={`revision-${field.id}`}
                onChange={(event) => onChange({ ...draft, [field.id]: event.target.value })}
                value={draft[field.id] ?? ""}
              />
            ) : field.type === "date" ? (
              <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames}
                id={`revision-${field.id}`}
                label={field.label}
                onChange={(next) => onChange({ ...draft, [field.id]: next })}
                value={draft[field.id] ?? ""}
              />
            ) : (
              <input
                id={`revision-${field.id}`}
                onChange={(event) => onChange({ ...draft, [field.id]: event.target.value })}
                type={field.type}
                value={draft[field.id] ?? ""}
              />
            )}
          </div>
        ))}
      </div>
      <div aria-label="제출 전 변경 요약" className="revision-diff">
        {changed.length === 0 ? (
          <p className="t-meta">바뀐 내용이 없습니다. 조정 요청에 답하도록 내용을 고쳐 주세요.</p>
        ) : (
          <ul>
            {changed.map((field) => (
              <li key={field.id}>
                <b>{field.label}</b>: <s>{displayValue(field.id, fieldValue(round.snapshot, field.id))}</s> → <b>{displayValue(field.id, draft[field.id])}</b>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export function ActionItemDrawer({
  actionItemId,
  principalId = "",
  personas,
  onClose,
  onDone,
  onError,
  onNotice,
  onOpenDerivedTask,
}: {
  actionItemId: string;
  principalId?: string;
  personas: Persona[];
  /** Open the Task this judgement produced, closing the round trip from the Task's own source link. */
  onOpenDerivedTask?: (taskId: string) => void;
  onClose: () => void;
  /** Settles every affected projection and reports whether all of them succeeded. */
  onDone: () => Promise<boolean>;
  onError: (message: string | null) => void;
  onNotice?: (message: string) => void;
}) {
  const [detail, setDetail] = useState<ActionItemDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [reasonFor, setReasonFor] = useState<ActionCommand | null>(null);
  const [reason, setReason] = useState("");
  const [proposal, setProposal] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string> | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getActionItem(actionItemId)
      .then((next) => {
        if (!cancelled) setDetail(next);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "판단 항목을 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [actionItemId, onError]);

  const latest = useMemo(() => (detail?.rounds.length ? detail.rounds[detail.rounds.length - 1] : null), [detail]);

  async function run(command: ActionCommand, payload: Record<string, unknown> = {}) {
    if (!detail) return;
    setBusy(true);
    onError(null);
    try {
      await runActionCommand(detail.action_item_id, command.id, {
        expected_version: detail.expected_version,
        ...(command.id === "confirm" ? { base_submission_version: detail.submission_version } : {}),
        ...payload,
      });
      // The judgement is persisted; only claim it is reflected once every affected projection has settled.
      const reflected = await onDone();
      onNotice?.(reflected ? `'${detail.subject}' 판단을 반영했습니다.` : `'${detail.subject}' 판단을 저장했습니다.`);
      onClose();
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "판단을 처리하지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const changes = useMemo(() => {
    if (!latest || !draft) return {};
    return Object.fromEntries(
      REVISABLE.filter((field) => draft[field.id] !== fieldValue(latest.snapshot, field.id)).map((field) => [field.id, draft[field.id]]),
    );
  }, [draft, latest]);

  /** Only the proposal fields the reviewer actually filled are sent; an untouched field asks for nothing. */
  const filledProposal = useMemo(
    () => Object.fromEntries(REVISABLE.filter((field) => (proposal[field.id] ?? "").trim()).map((field) => [field.id, proposal[field.id]])),
    [proposal],
  );
  const suggested = detail?.suggested_changes ?? {};
  /** The adjustment that sent this back, read from the round it was decided on. */
  const adjustment = useMemo(
    () => (latest?.decisions ?? []).filter((decision) => decision.decision === "negotiate").at(-1) ?? null,
    [latest],
  );
  const commands = detail?.allowed_commands ?? [];
  return (
    <Drawer
          closeLabel="상세 닫기"
      footer={
        <>
          <Button variant="text" onClick={onClose} type="button">
            닫기
          </Button>
          <span className="scax-drawer__spacer" />
          {draft ? (
            <Button variant="solid" tone="primary" disabled={busy || Object.keys(changes).length === 0} onClick={() => void run({ id: "revise", label: "재상신", tone: "primary" }, { changes })}
              type="button"
            >
              수정안 재상신
            </Button>
          ) : reasonFor ? (
            <>
              <Button variant="text" onClick={() => setReasonFor(null)} type="button">
                입력 취소
              </Button>
              <Button
                disabled={busy || !reason.trim()}
                onClick={() => void run(reasonFor, { reason, ...(Object.keys(filledProposal).length ? { changes: filledProposal } : {}) })}
                tone={reasonFor.tone === "danger" ? "danger" : "primary"}
                type="button"
                variant="solid"
              >
                {reasonFor.label} 확정
              </Button>
            </>
          ) : (
            (detail?.edit_contract?.editor === "command" ? [] : commands).map((command) =>
              command.id === "revise" ? (
                <Button variant="solid" tone="primary" key={command.id} onClick={() =>
                    setDraft(Object.fromEntries(REVISABLE.map((field) => [field.id, latest ? fieldValue(latest.snapshot, field.id) : ""])))
                  }
                  type="button"
                >
                  {command.label}
                </Button>
              ) : (
                <Button
                  disabled={busy}
                  key={command.id}
                  onClick={() => (command.requires_reason ? setReasonFor(command) : void run(command))}
                  tone={command.tone === "primary" ? "primary" : command.tone === "danger" ? "danger" : "neutral"}
                  type="button"
                  variant={command.tone === "primary" || command.tone === "danger" ? "solid" : "text"}
                >
                  {command.label}
                </Button>
              ),
            )
          )}
        </>
      }
      kicker={detail?.operation_label ?? "판단"}
      label="판단 상세"
      onClose={onClose}
      title={detail?.subject ?? "불러오는 중…"}
    >
      {!detail ? (
        <Skeleton label="판단 항목을 불러오는 중" rows={4} />
      ) : (
        <>
          <p className="current-question">{detail.current_question}</p>
          <dl className="drawer-facts">
            <div>
              <dt>상태</dt>
              <dd>
                <StatusText label={statusLabel[detail.status]} state={statusTone[detail.status]} />
              </dd>
            </div>
            <div>
              <dt>현재 차례</dt>
              <dd>{detail.waiting_on ? personName(detail.waiting_on.display_name) : "없음"}</dd>
            </div>
            <div>
              <dt>회차</dt>
              <dd>{detail.submission_version}회차</dd>
            </div>
          </dl>
          <ActionPreviewDetails
            action={{ action_id: detail.action_item_id, preview: detail.preview } as never}
            defaultOpen
          />
          {detail.edit_contract?.editor === "command" && (
            <CommandConfirmationForm actionId={detail.action_item_id} principalId={principalId} contract={detail.edit_contract} commands={commands} onCommand={(commandId, payload) => {
              const command = commands.find(item => item.id === commandId);
              return command ? run(command, payload) : Promise.resolve(false);
            }} />
          )}
          {detail.status === "awaiting_revision" && adjustment && (
            <AdjustmentAsk
              actor={adjustment.actor_member_id}
              personas={personas}
              reason={adjustment.reason}
              suggested={suggested}
            />
          )}
          {draft && latest ? <RevisionForm draft={draft} onChange={setDraft} round={latest} suggested={suggested} /> : null}
          {reasonFor && (
            <section className="drawer-section">
              <h4>{reasonFor.label} 사유</h4>
              <div className="inline-reason" style={{ padding: 0 }}>
                <label className="sr-only" htmlFor="action-command-reason">
                  {reasonFor.label} 사유
                </label>
                <input
                  autoFocus
                  id="action-command-reason"
                  onChange={(event) => setReason(event.target.value)}
                  placeholder={reasonFor.id === "reject" || reasonFor.id === "decline" ? "거절 사유를 적어 주세요" : "무엇을 바꿔야 하는지 적어 주세요"}
                  value={reason}
                />
              </div>
              {reasonFor.id === "adjust" && (
                <div className="form-grid proposal-grid">
                  {REVISABLE.map((field) => (
                    <div key={field.id}>
                      {field.type !== "date" && <label htmlFor={`proposal-${field.id}`}>제안: {field.label}</label>}
                      {field.type === "date" ? (
                        <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames}
                          id={`proposal-${field.id}`}
                          label={`제안: ${field.label}`}
                          onChange={(next) => setProposal({ ...proposal, [field.id]: next })}
                          value={proposal[field.id] ?? ""}
                        />
                      ) : (
                        <input
                          id={`proposal-${field.id}`}
                          onChange={(event) => setProposal({ ...proposal, [field.id]: event.target.value })}
                          placeholder="선택 사항"
                          type="text"
                          value={proposal[field.id] ?? ""}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}
          {detail.derived_task_id && onOpenDerivedTask && (
            <p className="t-meta">
              이 판단으로 생긴 업무{" "}
              <Button variant="inline" onClick={() => onOpenDerivedTask(detail.derived_task_id!)} type="button">
                업무 보기
              </Button>
            </p>
          )}
          <Discussion entries={detail.discussion ?? []} personas={personas} />
          <RoundHistory personas={personas} rounds={detail.rounds} />
          {detail.status === "resolved" && <p className="t-meta">이 질문은 이미 판단이 끝났습니다. 기록으로만 남습니다.</p>}
          {detail.resource.type === "work_request" && detail.rounds.length > 0 && (
            <p className="t-meta">
              첫 제출 {formatDate(detail.rounds[0].submitted_at.slice(0, 10))} · 요청 원장 {detail.resource.id.slice(0, 8)}
            </p>
          )}
        </>
      )}
    </Drawer>
  );
}
