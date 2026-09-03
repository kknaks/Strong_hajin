import { useState } from "react";

import { createDirectTask, createWorkRequest, decideWorkRequest, negotiateWorkRequest } from "./api";
import {
  formatMonthDay,
  isoDateInSeoul,
  personName,
  taskStateLabel,
  workRequestStateLabel,
} from "./labels";
import { ConfirmModal, Drawer } from "./Modal";
import type { DirectTask, Persona, WorkRequest } from "./viewModels";

export type TaskAction = "start" | "block" | "resume" | "complete" | "cancel";

export function displayNameOf(personas: Persona[], id: string | null | undefined, fallback = "알 수 없음"): string {
  if (!id) return fallback;
  const persona = personas.find((item) => item.id === id);
  return persona ? personName(persona.display_name) : id;
}

export function StatusText({ state, label }: { state: string; label?: string }) {
  return <span className={`status ${state}`}>{label ?? (state in taskStateLabel ? taskStateLabel[state as keyof typeof taskStateLabel] : state)}</span>;
}

/* ---------------------------------------------------------------- task detail (drawer 840) */

export function TaskDetailDrawer({
  task,
  ownerName,
  requesterName,
  canManage,
  busy,
  onTransition,
  onAskAx,
  onClose,
}: {
  task: DirectTask;
  ownerName: string;
  requesterName?: string | null;
  canManage: boolean;
  busy: boolean;
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<void>;
  onAskAx?: (task: DirectTask) => void;
  onClose: () => void;
}) {
  const [isBlocking, setIsBlocking] = useState(false);
  const [blockReason, setBlockReason] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const created = isoDateInSeoul(task.created_at);
  const updated = isoDateInSeoul(task.updated_at);

  const submitBlock = async () => {
    const reason = blockReason.trim();
    if (!reason) return;
    await onTransition(task, "block", reason);
    setIsBlocking(false);
    setBlockReason("");
  };

  return (
    <>
      <Drawer
        footer={
          canManage ? (
            <>
              {task.state !== "done" && task.state !== "cancelled" && (
                <button className="btn h40 ghost" disabled={busy} onClick={() => setConfirmCancel(true)} type="button">
                  업무 취소
                </button>
              )}
              <span className="spacer" />
              {task.state === "in_progress" && (
                <button className="btn h40" disabled={busy || isBlocking} onClick={() => setIsBlocking(true)} type="button">
                  막힘
                </button>
              )}
              {task.state === "open" && (
                <button className="btn h40 primary" disabled={busy} onClick={() => void onTransition(task, "start")} type="button">
                  시작
                </button>
              )}
              {task.state === "in_progress" && (
                <button className="btn h40 primary" disabled={busy} onClick={() => void onTransition(task, "complete")} type="button">
                  완료 처리
                </button>
              )}
              {task.state === "blocked" && (
                <button className="btn h40 primary" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
                  재개
                </button>
              )}
              {(task.state === "done" || task.state === "cancelled") && (
                <button className="btn h40" onClick={onClose} type="button">
                  닫기
                </button>
              )}
            </>
          ) : (
            <button className="btn h40" onClick={onClose} type="button">
              닫기
            </button>
          )
        }
        headerExtra={
          <div className="chip-row">
            <StatusText state={task.state} />
            <span className="badge outline">v{task.version}</span>
          </div>
        }
        kicker="업무 상세"
        label="업무 상세"
        onClose={onClose}
        title={task.title}
      >
        <dl className="meta-grid columns">
          <div>
            <dt>담당자</dt>
            <dd>{ownerName}</dd>
          </div>
          <div>
            <dt>요청자</dt>
            <dd>{requesterName ?? "본인 생성"}</dd>
          </div>
          <div>
            <dt>시작일</dt>
            <dd>{formatMonthDay(created)}</dd>
          </div>
          <div>
            <dt>{task.state === "done" ? "완료일" : task.state === "cancelled" ? "취소일" : "최근 변경"}</dt>
            <dd>{formatMonthDay(updated)}</dd>
          </div>
        </dl>
        {task.block_reason && (
          <section className="drawer-section">
            <h4>막힘 사유</h4>
            <p className="danger-text">{task.block_reason}</p>
          </section>
        )}
        {isBlocking && (
          <section className="drawer-section">
            <h4>막힘 사유 입력</h4>
            <div className="inline-reason" style={{ padding: 0 }}>
              <label className="sr-only" htmlFor={`block-reason-${task.task_id}`}>
                막힘 사유
              </label>
              <input
                autoFocus
                id={`block-reason-${task.task_id}`}
                onChange={(event) => setBlockReason(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submitBlock();
                }}
                placeholder="무엇 때문에 막혔는지 적어 주세요"
                value={blockReason}
              />
              <button className="btn primary" disabled={busy || !blockReason.trim()} onClick={() => void submitBlock()} type="button">
                막힘 처리
              </button>
              <button className="btn ghost" onClick={() => setIsBlocking(false)} type="button">
                입력 취소
              </button>
            </div>
          </section>
        )}
        <section className="drawer-section">
          <h4>다음 행동</h4>
          <p>
            {task.state === "open" && "시작을 누르면 진행 중으로 바뀌고 홈의 오늘 업무에 올라옵니다."}
            {task.state === "in_progress" && "막히면 사유를 남겨 두고, 끝나면 완료 처리합니다. 완료한 업무는 일일보고 근거로 모입니다."}
            {task.state === "blocked" && "막힘이 풀리면 재개하세요. 사유는 팀장 화면과 일일보고 근거에 그대로 남습니다."}
            {task.state === "done" && "완료된 업무입니다. 오늘 기록이면 일일보고 초안에 자동으로 포함됩니다."}
            {task.state === "cancelled" && "취소된 업무입니다. 다시 진행하려면 새 업무로 만드세요."}
          </p>
        </section>
        {onAskAx && (
          <section className="drawer-section">
            <h4>AX</h4>
            <button className="btn ai" onClick={() => onAskAx(task)} type="button">
              ✦ AX에게 이 업무 묻기
            </button>
          </section>
        )}
      </Drawer>
      {confirmCancel && (
        <ConfirmModal
          busy={busy}
          confirmLabel="업무 취소"
          danger
          description={`'${task.title}' 업무를 취소합니다. 취소한 업무는 다시 진행할 수 없고 기록만 남습니다.`}
          onClose={() => setConfirmCancel(false)}
          onConfirm={() => {
            setConfirmCancel(false);
            void onTransition(task, "cancel");
          }}
          title="업무를 취소할까요?"
        />
      )}
    </>
  );
}

/** Quick actions used inside table rows: one primary next step plus 막힘 while in progress. */
export function TaskQuickActions({
  task,
  busy,
  onTransition,
}: {
  task: DirectTask;
  busy: boolean;
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<void>;
}) {
  const [isBlocking, setIsBlocking] = useState(false);
  const [blockReason, setBlockReason] = useState("");
  const submitBlock = async () => {
    const reason = blockReason.trim();
    if (!reason) return;
    await onTransition(task, "block", reason);
    setIsBlocking(false);
    setBlockReason("");
  };
  return (
    <>
      {task.state === "open" && (
        <button className="btn h30 primary" disabled={busy} onClick={() => void onTransition(task, "start")} type="button">
          시작
        </button>
      )}
      {task.state === "in_progress" && (
        <>
          <button className="btn h30" disabled={busy || isBlocking} onClick={() => setIsBlocking(true)} type="button">
            막힘
          </button>
          <button className="btn h30 primary" disabled={busy} onClick={() => void onTransition(task, "complete")} type="button">
            완료
          </button>
        </>
      )}
      {task.state === "blocked" && (
        <button className="btn h30 primary" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
          재개
        </button>
      )}
      {isBlocking && (
        <div className="inline-reason">
          <label className="sr-only" htmlFor={`row-block-reason-${task.task_id}`}>
            막힘 사유
          </label>
          <input
            autoFocus
            id={`row-block-reason-${task.task_id}`}
            onChange={(event) => setBlockReason(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submitBlock();
              if (event.key === "Escape") setIsBlocking(false);
            }}
            placeholder="무엇 때문에 막혔는지 적어 주세요"
            value={blockReason}
          />
          <button className="btn h30 primary" disabled={busy || !blockReason.trim()} onClick={() => void submitBlock()} type="button">
            막힘 처리
          </button>
          <button className="btn h30 ghost" onClick={() => setIsBlocking(false)} type="button">
            입력 취소
          </button>
        </div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- work request detail (drawer) */

export function WorkRequestDetailDrawer({
  request,
  personaId,
  personas,
  canDecide,
  onChanged,
  onError,
  onNotice,
  onClose,
}: {
  request: WorkRequest;
  personaId: string;
  personas: Persona[];
  canDecide: boolean;
  onChanged: () => Promise<void> | void;
  onError: (message: string | null) => void;
  onNotice?: (message: string) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"negotiate" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [isWorking, setIsWorking] = useState(false);
  const requesterName = request.requester_id === personaId ? "나" : displayNameOf(personas, request.requester_id, "요청자");
  const isAssignee = request.assignee_id === personaId;
  const assigneeName = isAssignee ? "나" : displayNameOf(personas, request.assignee_id, "담당자");
  const isOpen = request.state === "pending" || request.state === "negotiating";
  const decidable = canDecide && isAssignee && isOpen;
  const condition = conditionText(request.conditions);

  async function run(action: () => Promise<unknown>, success: string, failure: string) {
    setIsWorking(true);
    onError(null);
    try {
      await action();
      await onChanged();
      onNotice?.(success);
      onClose();
    } catch (error) {
      onError(error instanceof Error ? error.message : failure);
    } finally {
      setIsWorking(false);
    }
  }

  function submitNote() {
    const trimmed = note.trim();
    if (!trimmed) {
      onError(mode === "reject" ? "거절 사유를 입력해 주세요." : "협의 조건을 입력해 주세요.");
      return;
    }
    void run(
      () =>
        mode === "reject"
          ? decideWorkRequest(request.request_id, "reject", request.version, trimmed)
          : negotiateWorkRequest(request.request_id, request.version, { note: trimmed }),
      mode === "reject" ? `'${request.title}' 요청을 거절했습니다.` : `'${request.title}' 요청에 협의 조건을 보냈습니다.`,
      "요청을 처리하지 못했습니다.",
    );
  }

  return (
    <Drawer
      footer={
        decidable ? (
          <>
            <button className="btn h40 ghost" disabled={isWorking} onClick={() => setMode("reject")} type="button">
              거절
            </button>
            <span className="spacer" />
            <button className="btn h40" disabled={isWorking} onClick={() => setMode("negotiate")} type="button">
              조정 요청
            </button>
            <button
              className="btn h40 primary"
              disabled={isWorking}
              onClick={() =>
                void run(
                  () => decideWorkRequest(request.request_id, "accept", request.version),
                  `'${request.title}' 요청을 수락했습니다. 내 업무에 생성되었습니다.`,
                  "요청을 수락하지 못했습니다.",
                )
              }
              type="button"
            >
              수락
            </button>
          </>
        ) : (
          <button className="btn h40" onClick={onClose} type="button">
            닫기
          </button>
        )
      }
      headerExtra={
        <div className="chip-row">
          <StatusText label={workRequestStateLabel[request.state]} state={request.state} />
          <span className="badge outline">v{request.version}</span>
        </div>
      }
      kicker="업무 요청"
      label="업무 요청 상세"
      onClose={onClose}
      title={request.title}
    >
      <dl className="meta-grid columns">
        <div>
          <dt>요청자</dt>
          <dd>{requesterName}</dd>
        </div>
        <div>
          <dt>담당자</dt>
          <dd>{assigneeName}</dd>
        </div>
        <div>
          <dt>생성된 업무</dt>
          <dd>{request.task_id ? "수락 후 생성됨" : "아직 없음"}</dd>
        </div>
      </dl>
      {condition && (
        <section className="drawer-section">
          <h4>협의 조건</h4>
          <p>{condition}</p>
        </section>
      )}
      <section className="drawer-section">
        <h4>{isOpen ? "수락하면 바뀌는 것" : "결과"}</h4>
        <blockquote className="effect-note">
          {request.state === "accepted" && `${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 “${request.title}”가 생성되었습니다.`}
          {request.state === "rejected" && "요청이 거절되어 업무가 생성되지 않았습니다."}
          {isOpen &&
            `수락하면 ${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 “${request.title}”가 생성됩니다. 거절하면 업무는 만들어지지 않습니다.`}
        </blockquote>
      </section>
      {mode && (
        <section className="drawer-section">
          <h4>{mode === "reject" ? "거절 사유" : "협의 조건"}</h4>
          <div className="inline-reason" style={{ padding: 0 }}>
            <label className="sr-only" htmlFor={`drawer-note-${request.request_id}`}>
              {mode === "reject" ? "거절 사유" : "협의 조건"}
            </label>
            <input
              autoFocus
              id={`drawer-note-${request.request_id}`}
              onChange={(event) => setNote(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitNote();
              }}
              placeholder={mode === "reject" ? "거절 사유를 적어 주세요" : "예: 9월 5일까지 완료 가능"}
              value={note}
            />
            <button className={mode === "reject" ? "btn danger" : "btn primary"} disabled={isWorking} onClick={submitNote} type="button">
              {mode === "reject" ? "거절 확정" : "조건 보내기"}
            </button>
            <button className="btn ghost" onClick={() => setMode(null)} type="button">
              입력 취소
            </button>
          </div>
        </section>
      )}
    </Drawer>
  );
}

export function conditionText(conditions: Record<string, unknown> | null): string | null {
  if (!conditions) return null;
  if (typeof conditions.note === "string" && conditions.note.trim()) return conditions.note;
  const entries = Object.entries(conditions).filter(([, value]) => value !== null && value !== "");
  if (entries.length === 0) return null;
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(", ");
}

/* ---------------------------------------------------------------- create (drawer) */

export function CreateWorkDrawer({
  personaId,
  ownerName,
  canCreateTask,
  canCreateRequest,
  assigneeCandidates,
  onCreated,
  onError,
  onClose,
}: {
  personaId: string;
  ownerName: string;
  canCreateTask: boolean;
  canCreateRequest: boolean;
  assigneeCandidates: Persona[];
  onCreated: (notice: string) => Promise<void> | void;
  onError: (message: string | null) => void;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<"task" | "request">(canCreateTask ? "task" : "request");
  const [title, setTitle] = useState("");
  const [assigneeId, setAssigneeId] = useState(assigneeCandidates[0]?.id ?? "");
  const [isWorking, setIsWorking] = useState(false);

  async function submit() {
    const trimmed = title.trim();
    if (!trimmed) {
      onError("업무 제목을 입력해 주세요.");
      return;
    }
    if (kind === "request" && !assigneeId) {
      onError("담당 후보를 선택해 주세요.");
      return;
    }
    setIsWorking(true);
    onError(null);
    try {
      if (kind === "task") {
        await createDirectTask(trimmed);
        await onCreated(`'${trimmed}' 업무를 만들었습니다.`);
      } else {
        const request = await createWorkRequest(trimmed, assigneeId);
        const assignee = assigneeCandidates.find((candidate) => candidate.id === assigneeId);
        await onCreated(`'${request.title}' 요청을 ${assignee ? personName(assignee.display_name) : "담당 후보"}에게 보냈습니다.`);
      }
      onClose();
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 만들지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  const titleInputId = kind === "task" ? "task-title" : "work-request-title";

  return (
    <Drawer
      footer={
        <>
          <button className="btn h40 ghost" disabled={isWorking} onClick={onClose} type="button">
            닫기
          </button>
          <span className="spacer" />
          <button
            className="btn h40 primary"
            disabled={isWorking || (kind === "request" && assigneeCandidates.length === 0)}
            onClick={() => void submit()}
            type="button"
          >
            {isWorking ? "만드는 중…" : kind === "task" ? "업무 추가" : "업무 요청 보내기"}
          </button>
        </>
      }
      headerExtra={
        <div className="chip-row">
          <div aria-label="생성 유형" className="segmented" role="tablist">
            {canCreateTask && (
              <button aria-selected={kind === "task"} onClick={() => setKind("task")} role="tab" type="button">
                업무
              </button>
            )}
            {canCreateRequest && (
              <button aria-selected={kind === "request"} onClick={() => setKind("request")} role="tab" type="button">
                요청
              </button>
            )}
          </div>
          <span className="t-meta">
            {kind === "task" ? "내가 처리할 업무를 만듭니다. 승인 없이 바로 내 업무에 들어갑니다." : "동료가 수락해야 그 사람의 업무가 됩니다."}
          </span>
        </div>
      }
      label="새 업무 추가"
      onClose={onClose}
      title="새 업무 추가"
    >
      <div className="form-stack">
        <div className="field">
          <span>
            <label htmlFor={titleInputId}>{kind === "task" ? "업무 제목" : "요청할 업무"}</label> <span className="danger-text">*</span>
          </span>
          <input
            autoFocus
            className="title-input"
            id={titleInputId}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
            placeholder={kind === "task" ? "업무명을 입력하세요" : "요청할 업무명을 입력하세요"}
            value={title}
          />
        </div>
        <dl className="meta-grid columns">
          <div>
            <dt>상태</dt>
            <dd>{kind === "task" ? <StatusText state="open" /> : <StatusText label="판단 대기" state="pending" />}</dd>
          </div>
          <div>
            <dt>{kind === "task" ? "담당자" : "요청자"}</dt>
            <dd>{ownerName}</dd>
          </div>
          {kind === "request" && (
            <div>
              <dt>
                <label htmlFor="work-request-assignee">담당 후보</label>
              </dt>
              <dd>
                <select
                  disabled={assigneeCandidates.length === 0}
                  id="work-request-assignee"
                  onChange={(event) => setAssigneeId(event.target.value)}
                  value={assigneeId}
                >
                  {assigneeCandidates.length === 0 ? (
                    <option value="">요청 가능한 동료가 없습니다.</option>
                  ) : (
                    assigneeCandidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.display_name}
                      </option>
                    ))
                  )}
                </select>
              </dd>
            </div>
          )}
        </dl>
      </div>
    </Drawer>
  );
}
