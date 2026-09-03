import { useEffect, useRef, useState } from "react";

import {
  addWorkRequestComment,
  createDirectTask,
  createWorkRequest,
  decideWorkRequest,
  detachTaskMaterial,
  getTaskMaterials,
  getWorkRequestTimeline,
  negotiateWorkRequest,
  resubmitWorkRequest,
  taskMaterialContentUrl,
  uploadTaskMaterial,
} from "./api";
import {
  dueDayText,
  formatMonthDay,
  isOverdue,
  isoDateInSeoul,
  personName,
  seoulToday,
  taskStateLabel,
  workRequestStateLabel,
} from "./labels";
import { ConfirmModal, Drawer } from "./Modal";
import type { DirectTask, Persona, RequestTimeline, TaskMaterial, TaskMaterialKind, TaskPatch, WorkRequest } from "./viewModels";

export type TaskAction = "start" | "block" | "resume" | "complete" | "cancel";

export function displayNameOf(personas: Persona[], id: string | null | undefined, fallback = "알 수 없음"): string {
  if (!id) return fallback;
  const persona = personas.find((item) => item.id === id);
  return persona ? personName(persona.display_name) : id;
}

export function StatusText({ state, label }: { state: string; label?: string }) {
  return <span className={`status ${state}`}>{label ?? (state in taskStateLabel ? taskStateLabel[state as keyof typeof taskStateLabel] : state)}</span>;
}

export function DueText({ task, today }: { task: DirectTask; today: string }) {
  if (!task.due_date) return <span className="t-meta">—</span>;
  const overdue = isOverdue(task, today);
  return (
    <span className={overdue ? "due-text overdue" : "due-text"}>
      {formatMonthDay(task.due_date)} <small>({dueDayText(task.due_date, today)})</small>
    </span>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)}KB`;
  return `${(size / 1024 / 1024).toFixed(1)}MB`;
}

/* ---------------------------------------------------------------- task detail (drawer 840) */

export function TaskDetailDrawer({
  task,
  ownerName,
  requesterName,
  canManage,
  busy,
  onTransition,
  onUpdate,
  onAskAx,
  onNotice,
  onError,
  onClose,
}: {
  task: DirectTask;
  ownerName: string;
  requesterName?: string | null;
  canManage: boolean;
  busy: boolean;
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<void>;
  onUpdate: (task: DirectTask, patch: TaskPatch) => Promise<void>;
  onAskAx?: (task: DirectTask) => void;
  onNotice?: (message: string) => void;
  onError: (message: string | null) => void;
  onClose: () => void;
}) {
  const today = seoulToday();
  const [isBlocking, setIsBlocking] = useState(false);
  const [blockReason, setBlockReason] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [startDate, setStartDate] = useState(task.start_date ?? "");
  const [dueDate, setDueDate] = useState(task.due_date ?? "");
  const [materials, setMaterials] = useState<TaskMaterial[] | null>(null);
  const [uploading, setUploading] = useState<TaskMaterialKind | null>(null);
  const inputFile = useRef<HTMLInputElement>(null);
  const outputFile = useRef<HTMLInputElement>(null);
  const closed = task.state === "cancelled";
  const editable = canManage && !closed;
  const dirty =
    title.trim() !== task.title ||
    description.trim() !== (task.description ?? "") ||
    startDate !== (task.start_date ?? "") ||
    dueDate !== (task.due_date ?? "");

  useEffect(() => {
    setTitle(task.title);
    setDescription(task.description ?? "");
    setStartDate(task.start_date ?? "");
    setDueDate(task.due_date ?? "");
  }, [task.task_id, task.version, task.title, task.description, task.start_date, task.due_date]);

  useEffect(() => {
    let cancelled = false;
    void getTaskMaterials(task.task_id)
      .then((items) => {
        if (!cancelled) setMaterials(items);
      })
      .catch(() => {
        if (!cancelled) setMaterials([]);
      });
    return () => {
      cancelled = true;
    };
  }, [task.task_id]);

  const save = async () => {
    if (!title.trim()) {
      onError("업무 제목을 입력해 주세요.");
      return;
    }
    if (startDate && dueDate && startDate > dueDate) {
      onError("시작일은 기한보다 늦을 수 없습니다.");
      return;
    }
    const patch: TaskPatch = {};
    if (title.trim() !== task.title) patch.title = title.trim();
    if (description.trim() !== (task.description ?? "")) patch.description = description.trim();
    if (startDate !== (task.start_date ?? "")) patch.start_date = startDate || null;
    if (dueDate !== (task.due_date ?? "")) patch.due_date = dueDate || null;
    await onUpdate(task, patch);
  };

  const submitBlock = async () => {
    const reason = blockReason.trim();
    if (!reason) return;
    await onTransition(task, "block", reason);
    setIsBlocking(false);
    setBlockReason("");
  };

  const upload = async (kind: TaskMaterialKind, file: File | undefined) => {
    if (!file) return;
    setUploading(kind);
    onError(null);
    try {
      const material = await uploadTaskMaterial(task.task_id, kind, file);
      setMaterials((current) => [...(current ?? []), material]);
      onNotice?.(`${kind === "input" ? "참고 자료" : "산출물"} '${material.name}'을 올렸습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "파일을 올리지 못했습니다.");
    } finally {
      setUploading(null);
      if (inputFile.current) inputFile.current.value = "";
      if (outputFile.current) outputFile.current.value = "";
    }
  };

  const detach = async (material: TaskMaterial) => {
    onError(null);
    try {
      await detachTaskMaterial(task.task_id, material.material_id);
      setMaterials((current) => (current ?? []).filter((item) => item.material_id !== material.material_id));
      onNotice?.(`'${material.name}'을 업무에서 뗐습니다. 기록은 남습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "자료를 떼지 못했습니다.");
    }
  };

  const renderMaterials = (kind: TaskMaterialKind, ref: React.RefObject<HTMLInputElement | null>) => {
    const items = (materials ?? []).filter((item) => item.kind === kind);
    return (
      <section className="drawer-section">
        <div className="section-row">
          <h4>{kind === "input" ? "참고 자료" : "산출물"}</h4>
          {editable && (
            <>
              <input
                aria-label={kind === "input" ? "참고 자료 파일" : "산출물 파일"}
                className="sr-only"
                onChange={(event) => void upload(kind, event.target.files?.[0])}
                ref={ref}
                type="file"
              />
              <button className="btn h30" disabled={uploading !== null || busy} onClick={() => ref.current?.click()} type="button">
                {uploading === kind ? "올리는 중…" : "파일 추가"}
              </button>
            </>
          )}
        </div>
        {materials === null ? (
          <p className="t-meta">불러오는 중…</p>
        ) : items.length === 0 ? (
          <p className="t-meta">{kind === "input" ? "등록된 참고 자료가 없습니다." : "등록된 산출물이 없습니다."}</p>
        ) : (
          <ul className="material-list">
            {items.map((item) => (
              <li key={item.material_id}>
                <a href={taskMaterialContentUrl(task.task_id, item.material_id)} rel="noreferrer" target="_blank">
                  {item.name}
                </a>
                <span className="t-meta">
                  {formatBytes(item.size_bytes)} · {formatMonthDay(isoDateInSeoul(item.created_at))}
                </span>
                {editable && (
                  <button className="btn h30 ghost" onClick={() => void detach(item)} type="button">
                    떼기
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  };

  return (
    <>
      <Drawer
        footer={
          canManage ? (
            <>
              {!closed && (
                <button className="btn h40 ghost" disabled={busy} onClick={() => setConfirmCancel(true)} type="button">
                  업무 취소
                </button>
              )}
              <span className="spacer" />
              {editable && dirty && (
                <button className="btn h40" disabled={busy} onClick={() => void save()} type="button">
                  변경 저장
                </button>
              )}
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
              {task.state === "done" && (
                <button className="btn h40" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
                  다시 진행
                </button>
              )}
              {closed && (
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
            {isOverdue(task, today) && <span className="badge danger">기한 초과</span>}
            <span className="badge outline">v{task.version}</span>
          </div>
        }
        kicker="업무 상세"
        label="업무 상세"
        onClose={onClose}
        title={task.title}
      >
        <div className="form-stack">
          <div className="field">
            <label htmlFor={`task-title-${task.task_id}`}>제목</label>
            <input className="title-input" disabled={!editable} id={`task-title-${task.task_id}`} onChange={(event) => setTitle(event.target.value)} value={title} />
          </div>
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
              <dt>
                <label htmlFor={`task-start-${task.task_id}`}>시작일</label>
              </dt>
              <dd>
                <input disabled={!editable} id={`task-start-${task.task_id}`} onChange={(event) => setStartDate(event.target.value)} type="date" value={startDate} />
              </dd>
            </div>
            <div>
              <dt>
                <label htmlFor={`task-due-${task.task_id}`}>기한</label>
              </dt>
              <dd>
                <input disabled={!editable} id={`task-due-${task.task_id}`} onChange={(event) => setDueDate(event.target.value)} type="date" value={dueDate} />
              </dd>
            </div>
          </dl>
          <div className="field">
            <label htmlFor={`task-description-${task.task_id}`}>업무 내용</label>
            <textarea
              disabled={!editable}
              id={`task-description-${task.task_id}`}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="무엇을, 왜, 어디까지 할지 적어 두면 요청자와 AX가 같은 맥락을 봅니다."
              value={description}
            />
          </div>
        </div>
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
        {renderMaterials("input", inputFile)}
        {renderMaterials("output", outputFile)}
        <section className="drawer-section">
          <h4>기록</h4>
          <p>
            {formatMonthDay(isoDateInSeoul(task.created_at))} 생성 · 최근 변경 {formatMonthDay(isoDateInSeoul(task.updated_at))}
            {task.state === "done" && " · 완료됨"}
            {task.state === "cancelled" && " · 취소됨"}
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
      {task.state === "done" && (
        <button className="btn h30 ghost" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
          다시 진행
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

const decisionLabel: Record<string, string> = { accept: "수락", negotiate: "조정 요청", reject: "거절" };

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
  const today = seoulToday();
  const [mode, setMode] = useState<"negotiate" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [isWorking, setIsWorking] = useState(false);
  const [timeline, setTimeline] = useState<RequestTimeline | null>(null);
  const [comment, setComment] = useState("");
  const [revision, setRevision] = useState<{ title: string; description: string; due_date: string } | null>(null);
  const nameOf = (id: string) => (id === personaId ? "나" : displayNameOf(personas, id, id));
  const requesterName = nameOf(request.requester_id ?? "");
  const isAssignee = request.assignee_id === personaId;
  const isRequester = request.requester_id === personaId;
  const assigneeName = isAssignee ? "나" : displayNameOf(personas, request.assignee_id, "담당자");
  const isOpen = request.state === "pending" || request.state === "negotiating";
  const decidable = canDecide && isAssignee && isOpen;
  const canResubmit = isRequester && request.state === "negotiating";
  const condition = conditionText(request.conditions);

  const loadTimeline = async () => {
    try {
      setTimeline(await getWorkRequestTimeline(request.request_id));
    } catch {
      setTimeline(null);
    }
  };

  useEffect(() => {
    let cancelled = false;
    void getWorkRequestTimeline(request.request_id)
      .then((next) => {
        if (!cancelled) setTimeline(next);
      })
      .catch(() => {
        if (!cancelled) setTimeline(null);
      });
    return () => {
      cancelled = true;
    };
  }, [request.request_id, request.version]);

  async function run(action: () => Promise<unknown>, success: string, failure: string, close = true) {
    setIsWorking(true);
    onError(null);
    try {
      await action();
      await onChanged();
      onNotice?.(success);
      if (close) onClose();
      else await loadTimeline();
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
      mode === "reject" ? `'${request.title}' 요청을 거절했습니다.` : `'${request.title}' 요청에 조정을 요청했습니다.`,
      "요청을 처리하지 못했습니다.",
    );
  }

  async function submitComment() {
    const text = comment.trim();
    if (!text) return;
    setIsWorking(true);
    onError(null);
    try {
      await addWorkRequestComment(request.request_id, text);
      setComment("");
      await loadTimeline();
    } catch (error) {
      onError(error instanceof Error ? error.message : "댓글을 남기지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  function submitRevision() {
    if (!revision) return;
    if (!revision.title.trim()) {
      onError("업무 제목을 입력해 주세요.");
      return;
    }
    const changes: { title?: string; description?: string; due_date?: string | null } = {};
    if (revision.title.trim() !== request.title) changes.title = revision.title.trim();
    if (revision.description.trim() !== (request.description ?? "")) changes.description = revision.description.trim();
    if (revision.due_date !== (request.due_date ?? "")) changes.due_date = revision.due_date || null;
    if (Object.keys(changes).length === 0) {
      onError("바뀐 내용이 없어 재상신할 수 없습니다. 조정 요청에 답하는 내용을 고쳐 주세요.");
      return;
    }
    void run(
      () => resubmitWorkRequest(request.request_id, request.version, changes),
      `'${request.title}' 요청을 재상신했습니다. 같은 요청의 새 회차로 판단자에게 다시 갑니다.`,
      "재상신하지 못했습니다.",
    );
  }

  const lastDecision = timeline?.review_decisions.at(-1);

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
        ) : canResubmit ? (
          <>
            <button className="btn h40 ghost" onClick={onClose} type="button">
              닫기
            </button>
            <span className="spacer" />
            {revision ? (
              <button className="btn h40 primary" disabled={isWorking} onClick={submitRevision} type="button">
                재상신
              </button>
            ) : (
              <button
                className="btn h40 primary"
                onClick={() => setRevision({ title: request.title, description: request.description ?? "", due_date: request.due_date ?? "" })}
                type="button"
              >
                내용 고쳐 재상신
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
          <StatusText label={workRequestStateLabel[request.state]} state={request.state} />
          {request.submission_version && request.submission_version > 1 && <span className="badge ai">재상신 {request.submission_version}회차</span>}
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
          <dt>희망 기한</dt>
          <dd>{request.due_date ? `${formatMonthDay(request.due_date)} (${dueDayText(request.due_date, today)})` : "없음"}</dd>
        </div>
        <div>
          <dt>생성된 업무</dt>
          <dd>{request.task_id ? "수락 후 생성됨" : "아직 없음"}</dd>
        </div>
      </dl>
      {request.description && !revision && (
        <section className="drawer-section">
          <h4>요청 내용</h4>
          <p className="prewrap">{request.description}</p>
        </section>
      )}

      {request.state === "negotiating" && lastDecision && (
        <section className="drawer-section">
          <h4>직전 판단</h4>
          <blockquote className="effect-note">
            {nameOf(lastDecision.actor_member_id)}의 조정 요청: {lastDecision.reason ?? condition ?? "조건 없음"}
          </blockquote>
        </section>
      )}

      {revision && (
        <section className="drawer-section">
          <h4>재상신 내용</h4>
          <div className="form-stack">
            <div className="field">
              <label htmlFor="revision-title">요청할 업무</label>
              <input id="revision-title" onChange={(event) => setRevision({ ...revision, title: event.target.value })} value={revision.title} />
            </div>
            <div className="field">
              <label htmlFor="revision-due">희망 기한</label>
              <input id="revision-due" onChange={(event) => setRevision({ ...revision, due_date: event.target.value })} type="date" value={revision.due_date} />
            </div>
            <div className="field">
              <label htmlFor="revision-description">요청 내용</label>
              <textarea id="revision-description" onChange={(event) => setRevision({ ...revision, description: event.target.value })} value={revision.description} />
            </div>
            <p className="t-meta">재상신은 같은 요청의 새 회차입니다. 이전 회차와 판단은 그대로 남고 달라진 항목만 diff로 표시됩니다.</p>
          </div>
        </section>
      )}

      {!revision && (
        <section className="drawer-section">
          <h4>{isOpen ? "수락하면 바뀌는 것" : "결과"}</h4>
          <blockquote className="effect-note">
            {request.state === "accepted" && `${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 “${request.title}”가 생성되었습니다.`}
            {request.state === "rejected" && "요청이 거절되어 업무가 생성되지 않았습니다."}
            {isOpen &&
              `수락하면 ${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 “${request.title}”가 ${request.due_date ? `기한 ${formatMonthDay(request.due_date)}로 ` : ""}생성됩니다. 거절하면 업무는 만들어지지 않습니다.`}
          </blockquote>
        </section>
      )}

      {mode && (
        <section className="drawer-section">
          <h4>{mode === "reject" ? "거절 사유" : "조정 요청 조건"}</h4>
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
              placeholder={mode === "reject" ? "거절 사유를 적어 주세요" : "예: 9월 15일까지면 가능"}
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

      {timeline && timeline.submissions.length > 0 && (
        <section className="drawer-section">
          <h4>
            회차와 판단 <span className="t-meta">· 판단 항목 {timeline.decision_item?.status === "resolved" ? "종결" : timeline.decision_item?.status === "awaiting_revision" ? "보완 대기" : "열림"}</span>
          </h4>
          <ol className="timeline-list">
            {timeline.submissions.map((submission) => {
              const decisions = timeline.review_decisions.filter((item) => item.submission_id === submission.submission_id);
              const assignment = timeline.review_assignments.find((item) => item.submission_id === submission.submission_id);
              return (
                <li key={submission.submission_id}>
                  <div className="timeline-item-head">
                    <b>{submission.submission_version}회차</b>
                    <span className="t-meta">
                      {nameOf(submission.submitted_by)} · {formatMonthDay(isoDateInSeoul(submission.submitted_at))}
                      {assignment && ` · 판단자 ${nameOf(assignment.reviewer_member_id)}`}
                    </span>
                  </div>
                  {submission.diff && Object.keys(submission.diff).length > 0 && (
                    <ul className="diff-list">
                      {Object.entries(submission.diff).map(([key, change]) => (
                        <li key={key}>
                          <span className="t-meta">{key === "title" ? "제목" : key === "description" ? "내용" : key === "due_date" ? "기한" : key}</span>
                          <s>{String(change.before ?? "—")}</s> → <b>{String(change.after ?? "—")}</b>
                        </li>
                      ))}
                    </ul>
                  )}
                  {decisions.map((decision) => (
                    <p className={`decision-line ${decision.decision}`} key={decision.review_decision_id}>
                      {nameOf(decision.actor_member_id)} · {decisionLabel[decision.decision] ?? decision.decision}
                      {decision.reason && ` — ${decision.reason}`}
                    </p>
                  ))}
                  {decisions.length === 0 && <p className="t-meta">판단 대기</p>}
                </li>
              );
            })}
          </ol>
        </section>
      )}

      <section className="drawer-section">
        <h4>
          논의 <span className="t-meta">· {timeline?.comments.length ?? 0}개 · 댓글은 상태를 바꾸지 않습니다</span>
        </h4>
        {timeline && timeline.comments.length > 0 ? (
          <ul className="comment-list">
            {timeline.comments.map((item) => (
              <li key={item.comment_id}>
                <b>{nameOf(item.author_member_id)}</b> <span className="t-meta">{formatMonthDay(isoDateInSeoul(item.created_at))}</span>
                <p className="prewrap">{item.body}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="t-meta">아직 논의가 없습니다.</p>
        )}
        <div className="inline-reason" style={{ padding: "8px 0 0" }}>
          <label className="sr-only" htmlFor={`comment-${request.request_id}`}>
            댓글
          </label>
          <input
            id={`comment-${request.request_id}`}
            onChange={(event) => setComment(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submitComment();
            }}
            placeholder="무엇이 걸리는지 남긴다"
            value={comment}
          />
          <button className="btn" disabled={isWorking || !comment.trim()} onClick={() => void submitComment()} type="button">
            남기기
          </button>
        </div>
      </section>
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
  ownerName,
  canCreateTask,
  canCreateRequest,
  assigneeCandidates,
  onCreated,
  onError,
  onClose,
}: {
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
  const [description, setDescription] = useState("");
  const [startDate, setStartDate] = useState("");
  const [dueDate, setDueDate] = useState("");
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
    if (kind === "task" && startDate && dueDate && startDate > dueDate) {
      onError("시작일은 기한보다 늦을 수 없습니다.");
      return;
    }
    setIsWorking(true);
    onError(null);
    try {
      if (kind === "task") {
        await createDirectTask(trimmed, {
          description: description.trim() || undefined,
          start_date: startDate || undefined,
          due_date: dueDate || undefined,
        });
        await onCreated(`'${trimmed}' 업무를 만들었습니다.`);
      } else {
        const request = await createWorkRequest(trimmed, assigneeId, {
          description: description.trim() || undefined,
          due_date: dueDate || undefined,
        });
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
            {kind === "task" ? "내가 처리할 업무를 만듭니다. 승인 없이 바로 내 업무에 들어갑니다." : "동료가 수락해야 그 사람의 업무가 됩니다. 희망 기한을 함께 보낼 수 있습니다."}
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
          {kind === "task" && (
            <div>
              <dt>
                <label htmlFor="new-task-start">시작일</label>
              </dt>
              <dd>
                <input id="new-task-start" onChange={(event) => setStartDate(event.target.value)} type="date" value={startDate} />
              </dd>
            </div>
          )}
          <div>
            <dt>
              <label htmlFor="new-task-due">{kind === "task" ? "기한" : "희망 기한"}</label>
            </dt>
            <dd>
              <input id="new-task-due" onChange={(event) => setDueDate(event.target.value)} type="date" value={dueDate} />
            </dd>
          </div>
        </dl>
        <div className="field">
          <label htmlFor="new-task-description">{kind === "task" ? "업무 내용" : "요청 내용"}</label>
          <textarea
            id="new-task-description"
            onChange={(event) => setDescription(event.target.value)}
            placeholder={kind === "task" ? "무엇을, 왜, 어디까지 할지 적어 두세요." : "상대가 판단할 수 있게 배경과 기대 결과를 적어 주세요."}
            value={description}
          />
        </div>
      </div>
    </Drawer>
  );
}
