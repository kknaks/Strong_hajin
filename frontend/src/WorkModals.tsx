import { useEffect, useRef, useState } from "react";
import { createIdempotencyKey } from "./idempotency";

import {
  addWorkRequestComment,
  assignTask,
  createDirectTask,
  createWorkRequest,
  decideWorkRequest,
  detachTaskMaterial,
  addChecklistItem,
  addTaskReference,
  releaseTaskReference,
  submitTaskCompletion,
  reorderChecklist,
  getTask,
  getTaskHistory,
  getTaskHistoryDiff,
  getTaskMaterials,
  removeChecklistItem,
  updateChecklistItem,
  getWorkRequestTimeline,
  negotiateWorkRequest,
  requestAttachmentUrl,
  resubmitWorkRequest,
  taskMaterialContentUrl,
  uploadCommentAttachment,
  amendWorkRequest,
  uploadRequestEvidence,
  attachTaskMaterialLink,
  attachTaskMaterialReference,
  getTaskAssignmentCandidates,
  getTasks,
  reassignTask,
  uploadTaskMaterial,
} from "./api";
import {
  dueDayText,
  formatDate,
  formatDateTime,
  isOverdue,
  isoDateInSeoul,
  personName,
  seoulToday,
  taskStateLabel,
  workRequestStateLabel,
} from "./labels";
import { DateField } from "./DateField";
import { ConfirmModal, Drawer } from "./Modal";
import type {
  ChecklistItem,
  DirectTask,
  MaterialExtraction,
  Persona,
  RequestTimeline,
  TaskHistory,
  TaskHistoryDiff,
  TaskMaterial,
  TaskChild,
  TaskDelivery,
  TaskMaterialKind,
  TaskReference,
  TaskOrigin,
  TaskPatch,
  WorkRequest,
} from "./viewModels";

export type TaskAction = "start" | "block" | "resume" | "complete" | "cancel";

/** One diff cell: date fields go through the shared formatter so no raw ISO reaches a read-only surface. */
function diffValue(field: string, value: unknown): string {
  const text = value === null || value === undefined || value === "" ? "" : String(value);
  if (!text) return "—";
  return field === "due_date" || field === "start_date" ? formatDate(text) : text;
}

/** The words a person reads for each snapshot field a diff can name. */
const HISTORY_FIELD_LABEL: Record<string, string> = {
  title: "제목",
  description: "업무 내용",
  state: "상태",
  block_reason: "막힘 사유",
  start_date: "시작일",
  due_date: "기한",
  assignee: "담당자",
  checklist: "체크리스트",
  materials: "참고 자료",
};

/**
 * How this Task got here: the ledger lines, newest first, and — on request — what actually changed between the
 * version before a line and the version it produced. Nothing is fetched until someone asks, because most people
 * open a Task to work on it rather than to audit it.
 */
//: What a person is usually looking for when they open the history, and which ledger kinds answer it.
const HISTORY_FILTERS: Array<{ id: string; label: string; match: (kind: string) => boolean }> = [
  { id: "all", label: "전체", match: () => true },
  {
    id: "core",
    label: "내용·상태",
    match: (kind) => kind.startsWith("task.") && !kind.startsWith("task.checklist.") && !kind.startsWith("task.material_"),
  },
  { id: "checklist", label: "체크리스트", match: (kind) => kind.startsWith("task.checklist.") },
  { id: "materials", label: "자료", match: (kind) => kind.startsWith("task.material_") },
];

export function TaskHistorySection({ task }: { task: DirectTask }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("all");
  const [history, setHistory] = useState<TaskHistory | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [diffs, setDiffs] = useState<Record<number, TaskHistoryDiff | "loading" | string>>({});

  // A version the drawer settled while this was open means there is a new line to read.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setFailure(null);
    void getTaskHistory(task.task_id)
      .then((loaded) => {
        if (!cancelled) setHistory(loaded);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setHistory(null);
        setFailure(error instanceof Error ? error.message : "이력을 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [open, task.task_id, task.version]);

  const openDiff = async (version: number) => {
    if (diffs[version] && diffs[version] !== "loading") {
      setDiffs(({ [version]: _closed, ...rest }) => rest);
      return;
    }
    setDiffs((current) => ({ ...current, [version]: "loading" }));
    try {
      const diff = await getTaskHistoryDiff(task.task_id, version - 1, version);
      setDiffs((current) => ({ ...current, [version]: diff }));
    } catch (error) {
      setDiffs((current) => ({ ...current, [version]: error instanceof Error ? error.message : "변경 내용을 불러오지 못했습니다." }));
    }
  };

  return (
    <section aria-label="활동·이력" className="drawer-section">
      <div className="section-row">
        <h4>활동·이력</h4>
        <button className="btn h30 ghost" onClick={() => setOpen((current) => !current)} type="button">
          {open ? "이력 접기" : "이력 보기"}
        </button>
      </div>
      <p className="t-meta">
        {formatDate(isoDateInSeoul(task.created_at))} 생성 · 최근 변경 {formatDate(isoDateInSeoul(task.updated_at))}
        {task.state === "done" && " · 완료됨"}
        {task.state === "cancelled" && " · 취소됨"}
      </p>
      {open && failure && <p className="danger-text">{failure}</p>}
      {open && !failure && history === null && <p className="t-meta">불러오는 중…</p>}
      {open && history !== null && history.activity.length === 0 && <p className="t-meta">아직 기록이 없습니다.</p>}
      {open && history !== null && history.activity.length > 0 && (
        <div className="chip-row">
          {HISTORY_FILTERS.map((option) => (
            <button
              aria-pressed={filter === option.id}
              className={filter === option.id ? "btn h30" : "btn h30 ghost"}
              key={option.id}
              onClick={() => setFilter(option.id)}
              type="button"
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
      {open && history !== null && history.activity.length > 0 && (
        <ol className="activity-list">
          {history.activity.filter((row) => (HISTORY_FILTERS.find((option) => option.id === filter) ?? HISTORY_FILTERS[0]).match(row.event_kind)).map((row, index) => {
            const version = row.version;
            const diff = version === null ? undefined : diffs[version];
            return (
              <li data-version={version ?? undefined} key={`${row.occurred_at}-${index}`}>
                <div className="activity-line">
                  <b>{row.actor ? personName(row.actor.display_name) : "알 수 없음"}</b>
                  <span className="t-meta">{formatDateTime(row.occurred_at)}</span>
                  {version !== null && <span className="badge outline">v{version}</span>}
                  {row.causation?.kind === "action_item" && <span className="badge ai">AX를 통해</span>}
                </div>
                <p>{row.summary}</p>
                {row.reason && <p className="t-meta">사유: {row.reason}</p>}
                {version !== null && version > 1 && (
                  <button className="btn h30 ghost" onClick={() => void openDiff(version)} type="button">
                    변경 내용
                  </button>
                )}
                {diff === "loading" && <p className="t-meta">변경 내용을 불러오는 중…</p>}
                {typeof diff === "string" && diff !== "loading" && <p className="danger-text">{diff}</p>}
                {diff && typeof diff !== "string" && (
                  <dl aria-label="변경 내용" className="diff-grid" role="group">
                    {Object.entries(diff.changes).map(([field, change]) => (
                      <div key={field}>
                        <dt>{HISTORY_FIELD_LABEL[field] ?? field}</dt>
                        <dd>
                          {change.added || change.removed ? (
                            <>
                              {(change.added ?? []).length > 0 && <span>추가: {(change.added ?? []).join(", ")}</span>}
                              {(change.removed ?? []).length > 0 && <span>빠짐: {(change.removed ?? []).join(", ")}</span>}
                            </>
                          ) : (
                            <>
                              <span className="t-meta">{diffValue(field, change.before)}</span> → <span>{diffValue(field, change.after)}</span>
                            </>
                          )}
                        </dd>
                      </div>
                    ))}
                    {Object.keys(diff.changes).length === 0 && <p className="t-meta">이 버전에서 바뀐 내용이 없습니다.</p>}
                  </dl>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

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
      {formatDate(task.due_date)} <small>({dueDayText(task.due_date, today)})</small>
    </span>
  );
}

/** Content extraction state of a material: what AX can search, what it could not read, and why. */
export function ExtractionStatus({ extraction }: { extraction: MaterialExtraction | null }) {
  if (!extraction) return <span className="badge outline extraction-status" data-status="none">내용 색인 없음</span>;
  if (extraction.status === "completed") {
    return (
      <span className="badge ai extraction-status" data-status="completed" title={`${extraction.chunk_count}개 구간 · ${extraction.char_count.toLocaleString()}자`}>
        내용 검색 가능
      </span>
    );
  }
  if (extraction.status === "queued" || extraction.status === "running") {
    return (
      <span className="badge neutral extraction-status" data-status={extraction.status}>
        {extraction.status === "queued" ? "내용 추출 대기" : "내용 추출 중"}
      </span>
    );
  }
  return (
    <span className="badge danger extraction-status" data-status={extraction.status} title={extraction.failure_text ?? undefined}>
      {extraction.status === "unsupported" ? "검색 미지원 형식" : "내용을 읽지 못함"}
      {extraction.failure_text ? ` · ${extraction.failure_text}` : ""}
    </span>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)}KB`;
  return `${(size / 1024 / 1024).toFixed(1)}MB`;
}

/* ---------------------------------------------------------------- task detail (drawer 840) */

/** How a Task came to this person, in a sentence. Never the raw role the server used to classify it. */
export function originSentence(origin: TaskOrigin): string | null {
  const actor = origin.actor ? personName(origin.actor.display_name) : null;
  if (actor && origin.kind === "work_request") return `${actor}가 보낸 업무`;
  if (actor && origin.kind === "direct_assignment") return `${actor}가 담당자를 지정함`;
  if (actor) return `${actor}가 만든 업무`;
  return origin.source ? "AX 제안에서 생성됨" : null;
}

export function TaskDetailDrawer({
  onOpenTask,
  canAssign = false,
  onChanged,
  task,
  ownerName,
  onOpenSource,
  canManage,
  busy,
  onTransition,
  onUpdate,
  onAskAx,
  onNotice,
  onError,
  onClose,
}: {
  /** Open another Task this one points at, inside the product rather than through a URL. */
  onOpenTask?: (taskId: string) => void;
  /** Whether this person may put someone else on work. Moving it is a command of its own, not a form field. */
  canAssign?: boolean;
  /** Settles the projections after the holder changed. */
  onChanged?: () => Promise<void> | void;
  task: DirectTask;
  ownerName: string;
  /** Navigate to the resource the origin names. Absent when the source is withheld. */
  onOpenSource?: (source: { type: string; id: string }) => void;
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
  const [confirmUnfinished, setConfirmUnfinished] = useState(false);
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [startDate, setStartDate] = useState(task.start_date ?? "");
  const [dueDate, setDueDate] = useState(task.due_date ?? "");
  const [materials, setMaterials] = useState<TaskMaterial[] | null>(null);
  const [checklist, setChecklist] = useState<ChecklistItem[] | null>(task.checklist ?? null);
  const [newStep, setNewStep] = useState("");
  const [editingStep, setEditingStep] = useState<{ itemId: string; text: string } | null>(null);
  const addingStep = useRef(false);
  const [uploading, setUploading] = useState<TaskMaterialKind | null>(null);
  const [linkDraft, setLinkDraft] = useState<{ kind: TaskMaterialKind; url: string; label: string } | null>(null);
  const [references, setReferences] = useState<TaskReference[]>(task.references ?? []);
  // Where this work stands with the person who asked for it. Only the detail read carries it, so it lives here.
  const [delivery, setDelivery] = useState<TaskDelivery | null>(task.delivery ?? null);
  // The parts of this work, and what it is a part of. Both come from the detail read.
  const [children, setChildren] = useState<TaskChild[]>(task.children ?? []);
  const [parentTask, setParentTask] = useState(task.parent ?? null);
  const [newChild, setNewChild] = useState<string | null>(null);
  const [refDraft, setRefDraft] = useState<string | null>(null);
  const [refChoices, setRefChoices] = useState<DirectTask[] | null>(null);
  const [report, setReport] = useState<{ summary: string; outputs: string[] } | null>(null);
  const [handover, setHandover] = useState<{ assigneeId: string; reason: string } | null>(null);
  const [handoverChoices, setHandoverChoices] = useState<Persona[] | null>(null);
  const inputFile = useRef<HTMLInputElement>(null);
  const outputFile = useRef<HTMLInputElement>(null);
  // A checklist or material change moves the Task's version, and every mutation answers with the version it moved
  // to. Hold that here so the very next save carries it, without waiting for the parent's refresh to come back.
  const [settledVersion, setSettledVersion] = useState(task.version);
  useEffect(() => setSettledVersion(task.version), [task.task_id, task.version]);
  const current = settledVersion > task.version ? { ...task, version: settledVersion } : task;
  const closed = task.state === "cancelled";
  const readOnly = task.access === "read_only";
  const editable = canManage && !closed && !readOnly;
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
    // Deliberately not keyed on `task.version`: a checklist or material change moves the version without moving
    // any of these fields, and resetting there would wipe an edit the person is still writing.
  }, [task.task_id, task.title, task.description, task.start_date, task.due_date]);

  // The checklist is only on the detail read, so a task opened from a list projection loads it here.
  useEffect(() => {
    let cancelled = false;
    void getTask(task.task_id)
      .then((detail) => {
        if (cancelled) return;
        setChecklist(detail.checklist ?? []);
        setReferences(detail.references ?? []);
        setDelivery(detail.delivery ?? null);
        setChildren(detail.children ?? []);
        setParentTask(detail.parent ?? null);
      })
      .catch(() => {
        if (!cancelled) setChecklist([]);
      });
    return () => {
      cancelled = true;
    };
  }, [task.task_id, task.version]);

  useEffect(() => {
    if (readOnly) return;
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

  // Extraction runs in a separate worker; poll while any material is still queued/running so the status settles in view.
  const extractionPending = (materials ?? []).some((item) => item.extraction && (item.extraction.status === "queued" || item.extraction.status === "running"));
  useEffect(() => {
    if (!extractionPending) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void getTaskMaterials(task.task_id)
        .then((items) => {
          if (!cancelled) setMaterials(items);
        })
        .catch(() => undefined);
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [extractionPending, task.task_id]);

  // Checklist and material changes are changes to the Task, so the server freezes a new version for each one.
  // Settle it here, or the next save from this open drawer is refused as stale.
  /** Take the version a mutation answered with, so this drawer stops holding the one it opened at. */
  function moved(version: number | undefined) {
    if (typeof version === "number") setSettledVersion((held) => (version > held ? version : held));
  }

  async function settleVersion() {
    try {
      await onChanged?.();
    } catch {
      // The change itself succeeded; a refresh that failed must not be reported as a failed change.
    }
  }

  async function addStep() {
    const text = newStep.trim();
    if (!text || addingStep.current) return;
    addingStep.current = true;
    onError(null);
    let added = false;
    try {
      const created = await addChecklistItem(task.task_id, text);
      setChecklist((rows) => [...(rows ?? []), created]);
      moved(created.task_version);
      // Clear only what was sent: a fast typist may already be writing the next step while this one is in flight.
      setNewStep((current) => (current.trim() === text ? "" : current));
      added = true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "체크리스트 단계를 추가하지 못했습니다.");
    } finally {
      addingStep.current = false;
    }
    // The refresh is not part of the add: holding the guard across it would swallow the next step someone
    // submits while it is still in flight.
    if (added) await settleVersion();
  }

  async function toggleStep(item: ChecklistItem, done: boolean) {
    onError(null);
    try {
      const updated = await updateChecklistItem(task.task_id, item.item_id, { done, expected_version: item.version });
      setChecklist((rows) => (rows ?? []).map((row) => (row.item_id === item.item_id ? updated : row)));
      moved(updated.task_version);
      await settleVersion();
    } catch (error) {
      await refuseStepChange(error, "체크리스트를 갱신하지 못했습니다.");
    }
  }

  async function renameStep(item: ChecklistItem, text: string) {
    const cleaned = text.trim();
    setEditingStep(null);
    if (!cleaned || cleaned === item.text) return;
    onError(null);
    try {
      const updated = await updateChecklistItem(task.task_id, item.item_id, { text: cleaned, expected_version: item.version });
      setChecklist((rows) => (rows ?? []).map((row) => (row.item_id === item.item_id ? updated : row)));
      moved(updated.task_version);
      await settleVersion();
    } catch (error) {
      await refuseStepChange(error, "단계 내용을 고치지 못했습니다.");
    }
  }

  async function moveStep(item: ChecklistItem, direction: -1 | 1) {
    const rows = checklist ?? [];
    const from = rows.findIndex((row) => row.item_id === item.item_id);
    const to = from + direction;
    if (from < 0 || to < 0 || to >= rows.length) return;
    const order = rows.map((row) => row.item_id);
    [order[from], order[to]] = [order[to], order[from]];
    onError(null);
    try {
      const reordered = await reorderChecklist(task.task_id, order);
      setChecklist(reordered.checklist);
      moved(reordered.task_version);
      await settleVersion();
    } catch (error) {
      await refuseStepChange(error, "순서를 바꾸지 못했습니다.");
    }
  }

  async function removeStep(item: ChecklistItem) {
    onError(null);
    try {
      const removed = await removeChecklistItem(task.task_id, item.item_id, item.version);
      setChecklist((rows) => (rows ?? []).filter((row) => row.item_id !== item.item_id));
      moved(removed?.task_version);
      await settleVersion();
    } catch (error) {
      await refuseStepChange(error, "체크리스트 단계를 삭제하지 못했습니다.");
    }
  }

  /**
   * A refused change must not stay on screen as if it had landed. Re-read the list so the person sees what the
   * server actually has, and say what happened — a stale version means someone else got there first.
   */
  async function refuseStepChange(error: unknown, fallback: string) {
    const message = error instanceof Error ? error.message : fallback;
    const conflict = message.includes("stale") ? "다른 사람이 이 단계를 먼저 고쳤습니다." : message;
    try {
      const detail = await getTask(task.task_id);
      setChecklist(detail.checklist ?? []);
      onError(`${conflict} 목록을 다시 불러왔습니다.`);
    } catch {
      onError(message);
    }
  }

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
    await onUpdate(current, patch);
  };

  const openSteps = (checklist ?? []).filter((item) => !item.done).length;
  // Work someone else asked for is finished when they say so; this drawer reports, it does not close.
  const reviewed = Boolean(delivery) || task.origin?.kind === "work_request";
  const awaitingReview = task.state === "completion_submitted";
  const requesterName = task.origin?.actor ? personName(task.origin.actor.display_name) : "요청자";

  const submitReport = async () => {
    const summary = report?.summary.trim() ?? "";
    if (!summary) {
      onError("무엇을 어디까지 했는지 적어 주세요.");
      return;
    }
    onError(null);
    try {
      const updated = await submitTaskCompletion(task.task_id, current.version, {
        summary,
        output_material_ids: report?.outputs ?? [],
      });
      setReport(null);
      setDelivery(updated.delivery ?? null);
      moved(updated.version);
      await settleVersion();
      onNotice?.(`완료 보고를 보냈습니다. ${requesterName}의 확인을 기다립니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "완료 보고를 보내지 못했습니다.");
    }
  };

  /** Unfinished steps are worth saying out loud, but whether they matter is the holder's call, not a rule. */
  const complete = async () => {
    if (openSteps > 0) {
      setConfirmUnfinished(true);
      return;
    }
    await onTransition(current, "complete");
  };

  const submitBlock = async () => {
    const reason = blockReason.trim();
    if (!reason) return;
    await onTransition(current, "block", reason);
    setIsBlocking(false);
    setBlockReason("");
  };

  const upload = async (kind: TaskMaterialKind, file: File | undefined) => {
    if (!file) return;
    setUploading(kind);
    onError(null);
    try {
      const material = await uploadTaskMaterial(task.task_id, kind, file);
      setMaterials((rows) => [...(rows ?? []), material]);
      moved(material.task_version);
      await settleVersion();
      onNotice?.(`${kind === "input" ? "참고 자료" : "산출물"} '${material.name}'을 올렸습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "파일을 올리지 못했습니다.");
    } finally {
      setUploading(null);
      if (inputFile.current) inputFile.current.value = "";
      if (outputFile.current) outputFile.current.value = "";
    }
  };

  const attachLink = async (kind: TaskMaterialKind) => {
    const draft = linkDraft;
    if (!draft || !draft.url.trim() || !draft.label.trim()) {
      onError("링크 주소와 이름을 모두 적어 주세요.");
      return;
    }
    setUploading(kind);
    onError(null);
    try {
      const material = await attachTaskMaterialLink(task.task_id, kind, { url: draft.url.trim(), label: draft.label.trim() });
      setMaterials((rows) => [...(rows ?? []), material]);
      moved(material.task_version);
      setLinkDraft(null);
      await settleVersion();
      onNotice?.(`${kind === "input" ? "참고 자료" : "산출물"} 링크 '${material.name}'을 연결했습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "링크를 연결하지 못했습니다.");
    } finally {
      setUploading(null);
    }
  };

  const openHandover = async () => {
    if (handover) {
      setHandover(null);
      return;
    }
    setHandover({ assigneeId: "", reason: "" });
    if (handoverChoices === null) {
      try {
        setHandoverChoices(await getTaskAssignmentCandidates());
      } catch {
        setHandoverChoices([]);
      }
    }
  };

  const submitHandover = async () => {
    if (!handover?.assigneeId) {
      onError("옮길 담당자를 골라 주세요.");
      return;
    }
    onError(null);
    try {
      await reassignTask(task.task_id, current.version, handover.assigneeId, handover.reason.trim() || undefined);
      setHandover(null);
      onNotice?.("담당자를 바꿨습니다. 새 담당자가 수락하면 그 사람의 업무가 됩니다.");
      await onChanged?.();
    } catch (error) {
      onError(error instanceof Error ? error.message : "담당자를 바꾸지 못했습니다.");
    }
  };

  /** Break this work into a part of its own: a Task, not a checklist line. */
  const addSubtask = async () => {
    const title = (newChild ?? "").trim();
    if (!title) {
      onError("하위 업무 제목을 입력해 주세요.");
      return;
    }
    onError(null);
    try {
      const created = await createDirectTask(title, { parent_task_id: task.task_id });
      setChildren((rows) => [...rows, { task_id: created.task_id, title: created.title, state: created.state, due_date: created.due_date ?? null, assignee: created.assignee ?? null }]);
      setNewChild(null);
      await settleVersion();
      onNotice?.(`하위 업무 '${created.title}'을 만들었습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "하위 업무를 만들지 못했습니다.");
    }
  };

  const openReferencePicker = async () => {
    if (refDraft !== null) {
      setRefDraft(null);
      return;
    }
    setRefDraft("");
    if (refChoices === null) {
      try {
        setRefChoices((await getTasks(true)).filter((item) => item.task_id !== task.task_id));
      } catch {
        setRefChoices([]);
      }
    }
  };

  /** Point at earlier work. It is context, not a claim about cause, and it hands out no access. */
  const connectReference = async () => {
    if (!refDraft) {
      onError("연결할 업무를 골라 주세요.");
      return;
    }
    onError(null);
    try {
      const created = await addTaskReference(task.task_id, refDraft);
      setReferences((rows) => [...rows, created]);
      setRefDraft(null);
      moved(created.task_version);
      await settleVersion();
      onNotice?.(`'${created.task?.title ?? "업무"}'를 참고 업무로 연결했습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 연결하지 못했습니다.");
    }
  };

  const releaseReference = async (reference: TaskReference) => {
    onError(null);
    try {
      const released = await releaseTaskReference(task.task_id, reference.reference_id);
      setReferences((rows) => rows.filter((row) => row.reference_id !== reference.reference_id));
      moved(released.task_version);
      await settleVersion();
    } catch (error) {
      onError(error instanceof Error ? error.message : "참고 업무 연결을 해제하지 못했습니다.");
    }
  };

  const detach = async (material: TaskMaterial) => {
    onError(null);
    try {
      const detached = await detachTaskMaterial(task.task_id, material.material_id);
      setMaterials((rows) => (rows ?? []).filter((item) => item.material_id !== material.material_id));
      moved(detached.task_version);
      await settleVersion();
      onNotice?.(`'${material.name}'을 업무에서 뗐습니다. 기록은 남습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "자료를 떼지 못했습니다.");
    }
  };

  const renderMaterials = (kind: TaskMaterialKind, ref: React.RefObject<HTMLInputElement | null>) => {
    if (readOnly) return null;
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
              <button
                className="btn h30 ghost"
                disabled={uploading !== null || busy}
                onClick={() => setLinkDraft(linkDraft?.kind === kind ? null : { kind, url: "", label: "" })}
                type="button"
              >
                링크 추가
              </button>
            </>
          )}
        </div>
        {editable && linkDraft?.kind === kind && (
          /* A link is not a file: SCAX records where the work lives and the words a person reads, nothing more. */
          <div className="form-stack link-draft">
            <div className="field">
              <label htmlFor={`material-link-url-${kind}`}>{kind === "input" ? "참고 자료" : "산출물"} 링크 주소</label>
              <input
                id={`material-link-url-${kind}`}
                inputMode="url"
                onChange={(event) => setLinkDraft({ ...linkDraft, url: event.target.value })}
                placeholder="https://"
                value={linkDraft.url}
              />
            </div>
            <div className="field">
              <label htmlFor={`material-link-label-${kind}`}>{kind === "input" ? "참고 자료" : "산출물"} 링크 이름</label>
              <input
                id={`material-link-label-${kind}`}
                onChange={(event) => setLinkDraft({ ...linkDraft, label: event.target.value })}
                placeholder="사람이 읽는 이름"
                value={linkDraft.label}
              />
            </div>
            <div className="row-actions">
              <button className="btn h30 primary" disabled={uploading !== null || busy} onClick={() => void attachLink(kind)} type="button">
                연결
              </button>
              <button className="btn h30 ghost" onClick={() => setLinkDraft(null)} type="button">
                취소
              </button>
            </div>
          </div>
        )}
        {materials === null ? (
          <p className="t-meta">불러오는 중…</p>
        ) : items.length === 0 ? (
          <p className="t-meta">{kind === "input" ? "등록된 참고 자료가 없습니다." : "등록된 산출물이 없습니다."}</p>
        ) : (
          <ul className="material-list">
            {items.map((item) => (
              <li key={item.material_id}>
                {item.source_kind === "resource_ref" ? (
                  /* It lives inside the product, so it opens inside the product — and only when it resolved. */
                  item.resource && onOpenTask ? (
                    <button className="btn link" onClick={() => onOpenTask(item.resource!.id)} type="button">
                      {item.name}
                    </button>
                  ) : (
                    <span>{item.name}</span>
                  )
                ) : (
                  <a href={item.url ?? taskMaterialContentUrl(task.task_id, item.material_id)} rel="noreferrer" target="_blank">
                    {item.name}
                  </a>
                )}
                {/* SCAX holds no bytes and pinned no revision, so the reader is told it can move under them. */}
                {item.mutable_source && <span className="badge outline">변경 가능한 링크</span>}
                <span className="t-meta">
                  {item.mutable_source
                    ? formatDate(isoDateInSeoul(item.created_at))
                    : `${formatBytes(item.size_bytes)} · ${formatDate(isoDateInSeoul(item.created_at))}`}
                </span>
                <ExtractionStatus extraction={item.extraction ?? null} />
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
                <button className="btn h40 primary" disabled={busy} onClick={() => void onTransition(current, "start")} type="button">
                  시작
                </button>
              )}
              {task.state === "in_progress" && !reviewed && (
                <button className="btn h40 primary" disabled={busy} onClick={() => void complete()} type="button">
                  완료 처리
                </button>
              )}
              {(task.state === "in_progress" || task.state === "blocked") && reviewed && (
                <button
                  className="btn h40 primary"
                  disabled={busy}
                  onClick={() => setReport(report ? null : { summary: delivery?.summary ?? "", outputs: [] })}
                  type="button"
                >
                  완료 보고
                </button>
              )}
              {task.state === "blocked" && (
                <button className="btn h40 primary" disabled={busy} onClick={() => void onTransition(current, "resume")} type="button">
                  재개
                </button>
              )}
              {task.state === "done" && (
                <button className="btn h40" disabled={busy} onClick={() => void onTransition(current, "resume")} type="button">
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
            <span className="badge outline">v{current.version}</span>
          </div>
        }
        kicker="업무 상세"
        label="업무 상세"
        onClose={onClose}
        title={task.title}
      >
        {awaitingReview && (
          <section aria-label="완료 확인 대기" className="drawer-section notice">
            <h4>완료 확인 대기</h4>
            <p>
              {requesterName}에게 결과 확인을 요청했습니다. {requesterName}가 완료로 인정하면 이 업무가 완료됩니다.
            </p>
            {delivery?.summary && <p className="prewrap t-meta">보고한 결과: {delivery.summary}</p>}
          </section>
        )}
        {delivery?.status === "awaiting_revision" && delivery.last_reason && (
          <section aria-label="보완 필요" className="drawer-section notice danger">
            <h4>보완 필요</h4>
            <p className="prewrap">{delivery.last_reason}</p>
            <p className="t-meta">{delivery.rounds}번째 보고까지 진행했습니다. 보완한 뒤 다시 완료 보고를 보내세요.</p>
          </section>
        )}
        {report !== null && (
          <section aria-label="완료 보고" className="drawer-section">
            <h4>완료 보고</h4>
            <div className="field">
              <label htmlFor={`delivery-summary-${task.task_id}`}>결과 요약</label>
              <textarea
                id={`delivery-summary-${task.task_id}`}
                onChange={(event) => setReport({ ...report, summary: event.target.value })}
                placeholder="무엇을 어디까지 했는지, 요청한 내용을 어떻게 충족했는지 적어 주세요."
                rows={3}
                value={report.summary}
              />
            </div>
            {(materials ?? []).filter((item) => item.kind === "output").length > 0 && (
              <fieldset className="field cc-picker">
                <legend>보고에 담을 산출물</legend>
                <div className="chip-row">
                  {(materials ?? [])
                    .filter((item) => item.kind === "output")
                    .map((item) => {
                      const checked = report.outputs.includes(item.material_id);
                      return (
                        <label className={checked ? "chip-toggle on" : "chip-toggle"} key={item.material_id}>
                          <input
                            checked={checked}
                            onChange={(event) =>
                              setReport({
                                ...report,
                                outputs: event.target.checked
                                  ? [...report.outputs, item.material_id]
                                  : report.outputs.filter((id) => id !== item.material_id),
                              })
                            }
                            type="checkbox"
                          />
                          {item.name}
                        </label>
                      );
                    })}
                </div>
                <p className="t-meta">고른 산출물은 보고 시점의 무결성 값으로 고정되어 함께 남습니다.</p>
              </fieldset>
            )}
            <div className="row-actions">
              <button className="btn h30 primary" disabled={busy} onClick={() => void submitReport()} type="button">
                보고 보내기
              </button>
              <button className="btn h30 ghost" onClick={() => setReport(null)} type="button">
                취소
              </button>
            </div>
          </section>
        )}
        <div className="form-stack">
          {canAssign && !readOnly && (
            /* Moving the work is its own act, so it is a command here rather than a field in the form below. */
            <div className="handover">
              <button className="btn h30 ghost" onClick={() => void openHandover()} type="button">
                담당자 변경
              </button>
              {handover && (
                <div className="form-stack link-draft">
                  <div className="field">
                    <label htmlFor={`task-handover-${task.task_id}`}>담당자 변경 대상</label>
                    <select
                      id={`task-handover-${task.task_id}`}
                      onChange={(event) => setHandover({ ...handover, assigneeId: event.target.value })}
                      value={handover.assigneeId}
                    >
                      <option value="">담당자 고르기</option>
                      {(handoverChoices ?? []).map((choice) => (
                        <option key={choice.id} value={choice.id}>
                          {personName(choice.display_name)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor={`task-handover-reason-${task.task_id}`}>담당자 변경 사유</label>
                    <input
                      id={`task-handover-reason-${task.task_id}`}
                      onChange={(event) => setHandover({ ...handover, reason: event.target.value })}
                      placeholder="왜 옮기는지 적어 두면 이력에 남습니다"
                      value={handover.reason}
                    />
                  </div>
                  <div className="row-actions">
                    <button className="btn h30 primary" disabled={busy} onClick={() => void submitHandover()} type="button">
                      변경
                    </button>
                    <button className="btn h30 ghost" onClick={() => setHandover(null)} type="button">
                      취소
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
          {task.origin && (
            /* Only a real counterpart or a real source is named, and it is named as what happened rather than as a
               role column. A task nobody handed over has neither. */
            <p aria-label="업무 출처" className="origin-chip">
              {originSentence(task.origin) && <span className="badge outline">{originSentence(task.origin)}</span>}
              {task.origin.source &&
                (onOpenSource ? (
                  <button className="btn link" onClick={() => onOpenSource(task.origin!.source!)} type="button">
                    {task.origin.source.title ?? "출처 보기"}
                  </button>
                ) : (
                  <small className="t-meta">{task.origin.source.title}</small>
                ))}
            </p>
          )}
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
              <dt>시작일</dt>
              <dd>
                <DateField disabled={!editable} hideLabel id={`task-start-${task.task_id}`} label="시작일" onChange={setStartDate} value={startDate} />
              </dd>
            </div>
            <div>
              <dt>기한</dt>
              <dd>
                <DateField disabled={!editable} hideLabel id={`task-due-${task.task_id}`} label="기한" onChange={setDueDate} value={dueDate} />
              </dd>
            </div>
          </dl>
          {!readOnly && (
          <section aria-label="체크리스트" className="drawer-section">
            <h4>
              체크리스트{" "}
              {checklist === null ? (
                <small className="t-meta">· 불러오는 중</small>
              ) : (
                <span className="checklist-progress" data-done={checklist.filter((item) => item.done).length} data-total={checklist.length}>
                  {checklist.filter((item) => item.done).length}/{checklist.length}
                </span>
              )}
            </h4>
            {checklist !== null && checklist.length > 0 && (
              <ul className="checklist">
                {checklist.map((item, index) => (
                  <li className={item.done ? "checklist-item done" : "checklist-item"} data-item-id={item.item_id} key={item.item_id}>
                    {editingStep?.itemId === item.item_id ? (
                      <>
                        <label className="sr-only" htmlFor={`step-text-${item.item_id}`}>
                          단계 내용
                        </label>
                        <input
                          autoFocus
                          className="step-edit"
                          id={`step-text-${item.item_id}`}
                          onBlur={() => void renameStep(item, editingStep.text)}
                          onChange={(event) => setEditingStep({ itemId: item.item_id, text: event.target.value })}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              if (event.nativeEvent.isComposing) return;
                              void renameStep(item, editingStep.text);
                            }
                            if (event.key === "Escape") setEditingStep(null);
                          }}
                          value={editingStep.text}
                        />
                      </>
                    ) : (
                      <>
                        <label>
                          <input
                            checked={item.done}
                            disabled={!canManage || busy}
                            onChange={(event) => void toggleStep(item, event.target.checked)}
                            type="checkbox"
                          />
                          <span>{item.text}</span>
                        </label>
                        {canManage && (
                          <>
                            {/* Order moves with buttons, not only with a pointer: a drag would strand keyboard and touch. */}
                            <button
                              aria-label={`${item.text} 위로`}
                              className="btn h30 ghost"
                              disabled={busy || index === 0}
                              onClick={() => void moveStep(item, -1)}
                              type="button"
                            >
                              ↑
                            </button>
                            <button
                              aria-label={`${item.text} 아래로`}
                              className="btn h30 ghost"
                              disabled={busy || index === checklist.length - 1}
                              onClick={() => void moveStep(item, 1)}
                              type="button"
                            >
                              ↓
                            </button>
                            <button
                              aria-label={`${item.text} 수정`}
                              className="btn h30 ghost"
                              onClick={() => setEditingStep({ itemId: item.item_id, text: item.text })}
                              type="button"
                            >
                              수정
                            </button>
                            <button
                              aria-label={`${item.text} 삭제`}
                              className="btn h30 ghost"
                              onClick={() => void removeStep(item)}
                              title="목록에서 빼고 이 업무의 기록에는 남깁니다"
                              type="button"
                            >
                              삭제
                            </button>
                          </>
                        )}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {checklist !== null && checklist.length === 0 && <p className="t-meta">아직 단계가 없습니다. 이 업무를 쪼개서 적어 두세요.</p>}
            {canManage && (
              <div className="inline-reason" style={{ padding: "8px 0 0" }}>
                <label className="sr-only" htmlFor={`checklist-${task.task_id}`}>
                  체크리스트 단계
                </label>
                <input
                  id={`checklist-${task.task_id}`}
                  onChange={(event) => setNewStep(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    if (event.repeat || event.nativeEvent.isComposing) return;
                    void addStep();
                  }}
                  placeholder="이 업무를 끝내려면 무엇을 해야 하나"
                  value={newStep}
                />
                <button className="btn" disabled={busy || !newStep.trim()} onClick={() => void addStep()} type="button">
                  추가
                </button>
              </div>
            )}
          </section>
          )}
          <div className="field">
            <label htmlFor={`task-description-${task.task_id}`}>업무 내용</label>
            <textarea
              className="task-description"
              disabled={!editable}
              id={`task-description-${task.task_id}`}
              rows={4}
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
        {parentTask && (
          <section aria-label="상위 업무" className="drawer-section notice">
            <h4>상위 업무</h4>
            <p>
              이 업무는{" "}
              <button
                aria-label={`${parentTask.title} 열기`}
                className="btn link"
                onClick={() => onOpenTask?.(parentTask.task_id)}
                type="button"
              >
                {parentTask.title}
              </button>{" "}
              의 하위 업무입니다. <span className="t-meta">{taskStateLabel[parentTask.state]}</span>
            </p>
          </section>
        )}
        {!readOnly && !parentTask && (
          /* One level only: a part of the work does not itself get parts. */
          <section aria-label="하위 업무" className="drawer-section">
            <div className="section-row">
              <h4>
                하위 업무{" "}
                <span className="checklist-progress" data-done={task.child_progress?.done ?? children.filter((row) => row.state === "done" || row.state === "cancelled").length} data-total={children.length}>
                  {children.filter((row) => row.state === "done" || row.state === "cancelled").length}/{children.length}
                </span>
              </h4>
              {editable && (
                <button className="btn h30 ghost" disabled={busy} onClick={() => setNewChild(newChild === null ? "" : null)} type="button">
                  {newChild === null ? "하위 업무 추가" : "추가 취소"}
                </button>
              )}
            </div>
            {children.length > 0 ? (
              <ul className="material-list">
                {children.map((row) => (
                  <li key={row.task_id}>
                    <button aria-label={`${row.title} 열기`} className="btn link" onClick={() => onOpenTask?.(row.task_id)} type="button">
                      {row.title}
                    </button>
                    <span className="t-meta">
                      {taskStateLabel[row.state]}
                      {row.due_date ? ` · ${formatDate(row.due_date)}` : ""}
                      {row.assignee ? ` · ${personName(row.assignee.display_name)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="t-meta">하위 업무가 없습니다. 따로 담당자나 기한이 필요한 일이면 하위 업무로 나누세요.</p>
            )}
            {editable && newChild !== null && (
              <div className="inline-reason" style={{ padding: "8px 0 0" }}>
                <label className="sr-only" htmlFor={`subtask-title-${task.task_id}`}>
                  하위 업무 제목
                </label>
                <input
                  autoFocus
                  id={`subtask-title-${task.task_id}`}
                  onChange={(event) => setNewChild(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    if (event.repeat || event.nativeEvent.isComposing) return;
                    void addSubtask();
                  }}
                  placeholder="따로 맡길 만한 일을 적어 주세요"
                  value={newChild}
                />
                <button className="btn" disabled={busy || !newChild.trim()} onClick={() => void addSubtask()} type="button">
                  만들기
                </button>
              </div>
            )}
            <p className="t-meta">체크리스트는 이 업무 안의 단계이고, 하위 업무는 따로 맡고 따로 끝나는 업무입니다.</p>
          </section>
        )}
        {!readOnly && (
          <section aria-label="참고 업무" className="drawer-section">
            <div className="section-row">
              <h4>
                참고 업무 <span className="t-meta">· 맥락으로 이어 둔 이전 업무입니다</span>
              </h4>
              {editable && (
                <button className="btn h30 ghost" disabled={busy} onClick={() => void openReferencePicker()} type="button">
                  {refDraft === null ? "업무 연결" : "연결 취소"}
                </button>
              )}
            </div>
            {references.length > 0 ? (
              <ul className="material-list">
                {references.map((reference) => (
                  <li key={reference.reference_id}>
                    {reference.task ? (
                      <button
                        aria-label={`${reference.task.title} 열기`}
                        className="btn link"
                        onClick={() => onOpenTask?.(reference.task!.task_id)}
                        type="button"
                      >
                        {reference.task.title}
                      </button>
                    ) : (
                      // The pointer is a fact of this task; what it points at is not this reader's to see.
                      <span className="t-meta">볼 수 없는 업무</span>
                    )}
                    <span className="t-meta">
                      {reference.task ? taskStateLabel[reference.task.state] : "권한 없음"}
                      {reference.task?.due_date ? ` · ${formatDate(reference.task.due_date)}` : ""}
                      {reference.task?.assignee ? ` · ${personName(reference.task.assignee.display_name)}` : ""}
                    </span>
                    {editable && (
                      <button
                        aria-label={`${reference.task?.title ?? "볼 수 없는 업무"} 연결 해제`}
                        className="btn h30 ghost"
                        onClick={() => void releaseReference(reference)}
                        type="button"
                      >
                        연결 해제
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="t-meta">연결된 업무가 없습니다. 이어지는 업무라면 이전 업무를 연결해 두세요.</p>
            )}
            {editable && refDraft !== null && (
              <div className="form-stack link-draft">
                <div className="field">
                  <label htmlFor={`task-reference-${task.task_id}`}>연결할 업무</label>
                  <select id={`task-reference-${task.task_id}`} onChange={(event) => setRefDraft(event.target.value)} value={refDraft}>
                    <option value="">업무 고르기</option>
                    {(refChoices ?? []).map((choice) => (
                      <option key={choice.task_id} value={choice.task_id}>
                        {choice.title}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="row-actions">
                  <button className="btn h30 primary" disabled={busy || !refDraft} onClick={() => void connectReference()} type="button">
                    연결
                  </button>
                </div>
              </div>
            )}
          </section>
        )}
        {renderMaterials("input", inputFile)}
        {renderMaterials("output", outputFile)}
        <TaskHistorySection task={task} />
        {onAskAx && (
          <section className="drawer-section">
            <h4>AX</h4>
            <button
              className="btn ai"
              onClick={() => {
                // Hand the task to the AX panel and close this drawer; the drawer would otherwise cover the panel.
                onAskAx(task);
                onClose();
              }}
              type="button"
            >
              ✦ AX에게 이 업무 묻기
            </button>
          </section>
        )}
      </Drawer>
      {confirmUnfinished && (
        <ConfirmModal
          busy={busy}
          confirmLabel="그래도 완료"
          description={`아직 끝나지 않은 단계가 ${openSteps}개 있습니다. 그대로 이 업무를 완료할까요?`}
          onClose={() => setConfirmUnfinished(false)}
          onConfirm={() => {
            setConfirmUnfinished(false);
            void onTransition(current, "complete");
          }}
          title="남은 단계가 있습니다"
        />
      )}
      {confirmCancel && (
        <ConfirmModal
          busy={busy}
          confirmLabel="업무 취소"
          danger
          description={`'${task.title}' 업무를 취소합니다. 취소한 업무는 다시 진행할 수 없고 기록만 남습니다.`}
          onClose={() => setConfirmCancel(false)}
          onConfirm={() => {
            setConfirmCancel(false);
            void onTransition(current, "cancel");
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
  onOpenDerivedTask,
  personaId,
  personas,
  canDecide,
  onChanged,
  onError,
  onNotice,
  onClose,
}: {
  request: WorkRequest;
  /** Open the Task this request produced, through the server's own authorized read. */
  onOpenDerivedTask?: (taskId: string) => void;
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
  const [commentFile, setCommentFile] = useState<File | null>(null);
  const [isUploadingEvidence, setIsUploadingEvidence] = useState(false);
  const commentFileInput = useRef<HTMLInputElement>(null);
  // Synchronous guards: React state cannot close the window between two events in the same tick.
  const submittingComment = useRef(false);
  const commentKey = useRef<{ key: string; body: string } | null>(null);
  const evidenceInput = useRef<HTMLInputElement>(null);
  const [revision, setRevision] = useState<{ title: string; description: string; due_date: string } | null>(null);
  const nameOf = (id: string) => (id === personaId ? "나" : displayNameOf(personas, id, id));
  const requesterName = nameOf(request.requester_id ?? "");
  const isAssignee = request.assignee_id === personaId;
  const isRequester = request.requester_id === personaId;
  const isCc = !isAssignee && !isRequester;
  // A basis may only grow while the round is still open to it; every other state is already judged or closed.
  const canAdoptEvidence = (isAssignee || isRequester) && request.state === "pending";
  const assigneeName = isAssignee ? "나" : displayNameOf(personas, request.assignee_id, "담당자");
  const isOpen = request.state === "pending" || request.state === "negotiating";
  const decidable = canDecide && isAssignee && isOpen;
  const canResubmit = isRequester && request.state === "negotiating";
  // Improving one's own request needs nobody's permission, but only while it is still the assignee's to judge.
  const canAmend = isRequester && request.state === "pending";
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

  /**
   * One logical submit, however many times it is triggered. `submittingComment` is a ref, so a second Enter or a click
   * arriving in the same tick is refused before React has re-rendered the disabled button. The idempotency key is
   * generated once for the text being sent and reused while that text is unchanged, so a retry after a failure
   * resolves to the same server comment instead of a second one.
   */
  async function submitComment() {
    const text = comment.trim();
    if (!text || submittingComment.current) return;
    submittingComment.current = true;
    if (!commentKey.current || commentKey.current.body !== text) {
      commentKey.current = { key: createIdempotencyKey(), body: text };
    }
    setIsWorking(true);
    onError(null);
    try {
      const created = await addWorkRequestComment(request.request_id, text, commentKey.current.key);
      if (commentFile) await uploadCommentAttachment(request.request_id, created.comment_id, commentFile);
      commentKey.current = null;
      setComment("");
      setCommentFile(null);
      if (commentFileInput.current) commentFileInput.current.value = "";
      await loadTimeline();
    } catch (error) {
      onError(error instanceof Error ? error.message : "댓글을 남기지 못했습니다.");
    } finally {
      submittingComment.current = false;
      setIsWorking(false);
    }
  }

  async function uploadEvidence(file: File | undefined) {
    if (!file) return;
    setIsUploadingEvidence(true);
    onError(null);
    try {
      const evidence = await uploadRequestEvidence(request.request_id, file);
      onNotice?.(`'${evidence.name}'을 ${evidence.submission_version}회차의 ${evidence.evidence_role === "decision_basis" ? "판단 근거" : "보조 자료"}로 채택했습니다.`);
      // The basis moved the request on, so take the new version before anything here is decided on it.
      await onChanged();
      await loadTimeline();
    } catch (error) {
      onError(error instanceof Error ? error.message : "근거 자료를 올리지 못했습니다.");
    } finally {
      setIsUploadingEvidence(false);
      if (evidenceInput.current) evidenceInput.current.value = "";
    }
  }

  /** What the draft would change, in the shape the command takes. */
  function revisionChanges(draft: { title: string; description: string; due_date: string }) {
    const changes: { title?: string; description?: string; due_date?: string | null } = {};
    if (draft.title.trim() !== request.title) changes.title = draft.title.trim();
    if (draft.description.trim() !== (request.description ?? "")) changes.description = draft.description.trim();
    if (draft.due_date !== (request.due_date ?? "")) changes.due_date = draft.due_date || null;
    return changes;
  }

  function submitAmendment() {
    if (!revision) return;
    if (!revision.title.trim()) {
      onError("업무 제목을 입력해 주세요.");
      return;
    }
    const changes = revisionChanges(revision);
    if (Object.keys(changes).length === 0) {
      onError("바뀐 내용이 없어 수정할 수 없습니다.");
      return;
    }
    void run(
      () => amendWorkRequest(request.request_id, request.version, changes),
      `'${request.title}' 요청을 수정했습니다. 같은 요청의 새 회차로 담당자가 최신 내용을 판단합니다.`,
      "요청을 수정하지 못했습니다.",
    );
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
        ) : canAmend ? (
          <>
            <button className="btn h40 ghost" onClick={onClose} type="button">
              닫기
            </button>
            <span className="spacer" />
            {revision ? (
              <button className="btn h40 primary" disabled={isWorking} onClick={submitAmendment} type="button">
                수정 제출
              </button>
            ) : (
              <button
                className="btn h40 primary"
                onClick={() => setRevision({ title: request.title, description: request.description ?? "", due_date: request.due_date ?? "" })}
                type="button"
              >
                요청 수정
              </button>
            )}
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
          <dd>{request.due_date ? `${formatDate(request.due_date)} (${dueDayText(request.due_date, today)})` : "없음"}</dd>
        </div>
        <div>
          <dt>생성된 업무</dt>
          <dd>
            {request.task_id ? (
              onOpenDerivedTask ? (
                <button className="btn link" onClick={() => onOpenDerivedTask(request.task_id!)} type="button">
                  파생 업무 보기
                </button>
              ) : (
                "수락 후 생성됨"
              )
            ) : (
              "아직 없음"
            )}
          </dd>
        </div>
        {request.cc_member_ids && request.cc_member_ids.length > 0 && (
          <div>
            <dt>참조자</dt>
            <dd>{request.cc_member_ids.map((id) => nameOf(id)).join(", ")}</dd>
          </div>
        )}
      </dl>
      {isCc && <p className="t-meta">참조자로 받은 요청입니다. 읽고 논의할 수 있지만 판단은 {assigneeName}가 합니다.</p>}
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
          <h4>{canAmend ? "수정 내용" : "재상신 내용"}</h4>
          <div className="form-stack">
            <div className="field">
              <label htmlFor="revision-title">요청할 업무</label>
              <input id="revision-title" onChange={(event) => setRevision({ ...revision, title: event.target.value })} value={revision.title} />
            </div>
            <div className="field">
              <DateField id="revision-due" label="희망 기한" onChange={(next) => setRevision({ ...revision, due_date: next })} value={revision.due_date} />
            </div>
            <div className="field">
              <label htmlFor="revision-description">요청 내용</label>
              <textarea id="revision-description" onChange={(event) => setRevision({ ...revision, description: event.target.value })} value={revision.description} />
            </div>
            <div aria-label="제출 전 변경 요약" className="revision-diff">
              {Object.keys(revisionChanges(revision)).length === 0 ? (
                <p className="t-meta">바뀐 내용이 없습니다.</p>
              ) : (
                <ul>
                  {revisionChanges(revision).title !== undefined && (
                    <li>
                      <b>요청할 업무</b>: <s>{request.title}</s> → <b>{revision.title.trim()}</b>
                    </li>
                  )}
                  {revisionChanges(revision).description !== undefined && (
                    <li>
                      <b>요청 내용</b>: <s>{request.description || "없음"}</s> → <b>{revision.description.trim() || "없음"}</b>
                    </li>
                  )}
                  {revisionChanges(revision).due_date !== undefined && (
                    <li>
                      <b>희망 기한</b>: <s>{request.due_date ? formatDate(request.due_date) : "없음"}</s> →{" "}
                      <b>{revision.due_date ? formatDate(revision.due_date) : "없음"}</b>
                    </li>
                  )}
                </ul>
              )}
            </div>
            <p className="t-meta">
              {canAmend
                ? "수정은 같은 요청의 새 회차입니다. 담당자는 최신 회차를 판단하고 이전 회차와 근거는 그대로 남습니다."
                : "재상신은 같은 요청의 새 회차입니다. 이전 회차와 판단은 그대로 남고 달라진 항목만 diff로 표시됩니다."}
            </p>
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
              `수락하면 ${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 “${request.title}”가 ${request.due_date ? `기한 ${formatDate(request.due_date)}로 ` : ""}생성됩니다. 거절하면 업무는 만들어지지 않습니다.`}
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
                      {nameOf(submission.submitted_by)} · {formatDate(isoDateInSeoul(submission.submitted_at))}
                      {assignment && ` · 판단자 ${nameOf(assignment.reviewer_member_id)}`}
                    </span>
                  </div>
                  {submission.diff && Object.keys(submission.diff).length > 0 && (
                    <ul className="diff-list">
                      {Object.entries(submission.diff).map(([key, change]) => (
                        <li key={key}>
                          <span className="t-meta">{key === "title" ? "제목" : key === "description" ? "내용" : key === "due_date" ? "기한" : key}</span>
                          <s>{diffValue(key, change.before)}</s> → <b>{diffValue(key, change.after)}</b>
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
        <div className="section-row">
          <h4>
            근거 자료 <span className="t-meta">· {timeline?.evidence?.length ?? 0}개 · 회차에 고정되며 해시로 봉인됩니다</span>
          </h4>
          {canAdoptEvidence && (
            <>
              <input aria-label="근거 자료 파일" className="sr-only" onChange={(event) => void uploadEvidence(event.target.files?.[0])} ref={evidenceInput} type="file" />
              <button className="btn h30" disabled={isUploadingEvidence || isWorking} onClick={() => evidenceInput.current?.click()} type="button">
                {isUploadingEvidence ? "올리는 중…" : isAssignee ? "판단 근거 추가" : "보조 자료 추가"}
              </button>
            </>
          )}
        </div>
        {timeline && timeline.evidence && timeline.evidence.length > 0 ? (
          <ul className="material-list">
            {timeline.evidence.map((item) => (
              <li key={item.evidence_id}>
                <a href={requestAttachmentUrl(request.request_id, item.attachment_id)} rel="noreferrer" target="_blank">
                  {item.name}
                </a>
                <span className="t-meta">
                  {item.submission_version}회차 · {item.evidence_role === "decision_basis" ? "판단 근거" : "보조 자료"} · {nameOf(item.adopted_by)} · {formatBytes(item.size_bytes)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="t-meta">채택된 근거 자료가 없습니다.</p>
        )}
      </section>

      <section className="drawer-section">
        <h4>
          논의 <span className="t-meta">· {timeline?.comments.length ?? 0}개 · 댓글은 상태를 바꾸지 않습니다</span>
        </h4>
        {timeline && timeline.comments.length > 0 ? (
          <ul className="comment-list">
            {timeline.comments.map((item) => (
              <li key={item.comment_id}>
                <b>{nameOf(item.author_member_id)}</b> <span className="t-meta">{formatDate(isoDateInSeoul(item.created_at))}</span>
                <p className="prewrap">{item.body}</p>
                {item.attachments && item.attachments.length > 0 && (
                  <ul className="attachment-row">
                    {item.attachments.map((attachment) => (
                      <li key={attachment.attachment_id}>
                        <a href={requestAttachmentUrl(request.request_id, attachment.attachment_id)} rel="noreferrer" target="_blank">
                          📎 {attachment.name}
                        </a>
                        <span className="t-meta"> {formatBytes(attachment.size_bytes)}</span>
                      </li>
                    ))}
                  </ul>
                )}
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
              if (event.key !== "Enter") return;
              event.preventDefault();
              if (event.repeat || event.nativeEvent.isComposing) return;
              void submitComment();
            }}
            placeholder="무엇이 걸리는지 남긴다"
            value={comment}
          />
          <input
            aria-label="댓글 첨부 파일"
            className="sr-only"
            onChange={(event) => setCommentFile(event.target.files?.[0] ?? null)}
            ref={commentFileInput}
            type="file"
          />
          <button
            aria-label={commentFile ? `첨부: ${commentFile.name}` : "파일 첨부"}
            className={commentFile ? "btn ghost on" : "btn ghost"}
            onClick={() => commentFileInput.current?.click()}
            title={commentFile ? commentFile.name : "파일 첨부"}
            type="button"
          >
            📎{commentFile ? ` ${commentFile.name.length > 14 ? `${commentFile.name.slice(0, 12)}…` : commentFile.name}` : ""}
          </button>
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
  assignCandidates = [],
  ccCandidates = [],
  onCreated,
  onError,
  onClose,
}: {
  ownerName: string;
  canCreateTask: boolean;
  canCreateRequest: boolean;
  assigneeCandidates: Persona[];
  assignCandidates?: Persona[];
  ccCandidates?: Persona[];
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
  const [taskOwnerId, setTaskOwnerId] = useState("me");
  const [ccIds, setCcIds] = useState<string[]>([]);
  const [steps, setSteps] = useState<string[]>([]);
  const [newStep, setNewStep] = useState("");
  const [linkedTasks, setLinkedTasks] = useState<DirectTask[]>([]);
  const [referenceDraft, setReferenceDraft] = useState<string | null>(null);
  const [referenceChoices, setReferenceChoices] = useState<DirectTask[] | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const assignTarget = taskOwnerId === "me" ? null : assignCandidates.find((candidate) => candidate.id === taskOwnerId) ?? null;

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
      // Steps written here belong to the work from the start, in the order they were written.
      const checklist = steps.length > 0 ? steps : undefined;
      // Earlier work pointed at here travels with the request into the Task the acceptance creates.
      const reference_task_ids = linkedTasks.length > 0 ? linkedTasks.map((row) => row.task_id) : undefined;
      if (kind === "task" && assignTarget) {
        await assignTask(trimmed, assignTarget.id, {
          description: description.trim() || undefined,
          start_date: startDate || undefined,
          due_date: dueDate || undefined,
          checklist,
        });
        await onCreated(`'${trimmed}' 업무를 ${personName(assignTarget.display_name)}에게 배정했습니다. 수락하면 그 사람의 업무가 됩니다.`);
      } else if (kind === "task") {
        await createDirectTask(trimmed, {
          description: description.trim() || undefined,
          start_date: startDate || undefined,
          due_date: dueDate || undefined,
          checklist,
          reference_task_ids,
        });
        await onCreated(`'${trimmed}' 업무를 만들었습니다.`);
      } else {
        const request = await createWorkRequest(trimmed, assigneeId, {
          description: description.trim() || undefined,
          due_date: dueDate || undefined,
          cc_member_ids: ccIds.filter((id) => id !== assigneeId),
          checklist,
          reference_task_ids,
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

  async function openReferences() {
    if (referenceDraft !== null) {
      setReferenceDraft(null);
      return;
    }
    setReferenceDraft("");
    if (referenceChoices === null) {
      try {
        setReferenceChoices(await getTasks(true));
      } catch {
        setReferenceChoices([]);
      }
    }
  }

  function linkReference() {
    const chosen = (referenceChoices ?? []).find((row) => row.task_id === referenceDraft);
    if (!chosen || linkedTasks.some((row) => row.task_id === chosen.task_id)) return;
    setLinkedTasks((current) => [...current, chosen]);
    setReferenceDraft(null);
  }

  function appendStep() {
    const step = newStep.trim();
    if (!step) return;
    setSteps((current) => [...current, step]);
    setNewStep("");
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
            {isWorking ? "만드는 중…" : kind === "task" ? (assignTarget ? "업무 배정" : "업무 추가") : "업무 요청 보내기"}
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
            {kind === "task"
              ? assignTarget
                ? "팀원에게 배정합니다. 그 사람이 수락해야 업무가 됩니다."
                : "내가 처리할 업무를 만듭니다. 승인 없이 바로 내 업무에 들어갑니다."
              : "동료가 수락해야 그 사람의 업무가 됩니다. 희망 기한을 함께 보낼 수 있습니다."}
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
            <dd>{kind === "task" ? assignTarget ? <StatusText label="수락 대기" state="pending" /> : <StatusText state="open" /> : <StatusText label="판단 대기" state="pending" />}</dd>
          </div>
          {kind === "task" && assignCandidates.length > 0 ? (
            <div>
              <dt>
                <label htmlFor="new-task-owner">담당자</label>
              </dt>
              <dd>
                <select id="new-task-owner" onChange={(event) => setTaskOwnerId(event.target.value)} value={taskOwnerId}>
                  <option value="me">{ownerName} (나)</option>
                  {assignCandidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.display_name}
                    </option>
                  ))}
                </select>
              </dd>
            </div>
          ) : (
            <div>
              <dt>{kind === "task" ? "담당자" : "요청자"}</dt>
              <dd>{ownerName}</dd>
            </div>
          )}
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
                <DateField hideLabel id="new-task-start" label="시작일" onChange={setStartDate} value={startDate} />
              </dd>
            </div>
          )}
          <div>
            <dt>
              <label htmlFor="new-task-due">{kind === "task" ? "기한" : "희망 기한"}</label>
            </dt>
            <dd>
              <DateField hideLabel id="new-task-due" label="기한" onChange={setDueDate} value={dueDate} />
            </dd>
          </div>
        </dl>
        {kind === "request" && ccCandidates.length > 0 && (
          <fieldset className="field cc-picker">
            <legend>참조자</legend>
            <div className="chip-row">
              {ccCandidates
                .filter((candidate) => candidate.id !== assigneeId)
                .map((candidate) => {
                  const checked = ccIds.includes(candidate.id);
                  return (
                    <label className={checked ? "chip-toggle on" : "chip-toggle"} key={candidate.id}>
                      <input
                        checked={checked}
                        onChange={(event) => setCcIds((current) => (event.target.checked ? [...current, candidate.id] : current.filter((id) => id !== candidate.id)))}
                        type="checkbox"
                      />
                      {personName(candidate.display_name)}
                    </label>
                  );
                })}
            </div>
            <p className="t-meta">참조자는 요청을 읽고 논의할 수 있지만 판단하지 않습니다.</p>
          </fieldset>
        )}
        <fieldset aria-label="참고 업무" className="field cc-picker">
          <legend>참고 업무</legend>
          {linkedTasks.length > 0 && (
            <ul className="checklist">
              {linkedTasks.map((row) => (
                <li className="checklist-item" key={row.task_id}>
                  <span>{row.title}</span>
                  <button
                    aria-label={`${row.title} 빼기`}
                    className="btn h30 ghost"
                    onClick={() => setLinkedTasks((current) => current.filter((item) => item.task_id !== row.task_id))}
                    type="button"
                  >
                    빼기
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="row-actions" style={{ padding: "8px 0 0" }}>
            <button className="btn h30 ghost" onClick={() => void openReferences()} type="button">
              {referenceDraft === null ? "참고 업무 연결" : "연결 취소"}
            </button>
          </div>
          {referenceDraft !== null && (
            <div className="form-stack link-draft">
              <div className="field">
                <label htmlFor="new-task-reference">연결할 이전 업무</label>
                <select id="new-task-reference" onChange={(event) => setReferenceDraft(event.target.value)} value={referenceDraft}>
                  <option value="">업무 고르기</option>
                  {(referenceChoices ?? []).map((choice) => (
                    <option key={choice.task_id} value={choice.task_id}>
                      {choice.title}
                    </option>
                  ))}
                </select>
              </div>
              <div className="row-actions">
                <button className="btn h30 primary" disabled={!referenceDraft} onClick={linkReference} type="button">
                  연결
                </button>
              </div>
            </div>
          )}
          <p className="t-meta">
            {kind === "task"
              ? "이어지는 업무라면 이전 업무를 맥락으로 연결해 두세요. 인과관계를 주장하지 않습니다."
              : "여기 연결한 업무는 상대가 수락한 업무에도 그대로 이어집니다. 볼 수 있는 사람에게만 보입니다."}
          </p>
        </fieldset>
        <fieldset aria-label="시작 단계" className="field cc-picker">
          <legend>시작 단계</legend>
          {steps.length > 0 && (
            <ul className="checklist">
              {steps.map((step, index) => (
                <li className="checklist-item" key={`${step}-${index}`}>
                  <span>{step}</span>
                  <button
                    aria-label={`${step} 빼기`}
                    className="btn h30 ghost"
                    onClick={() => setSteps((current) => current.filter((_, position) => position !== index))}
                    type="button"
                  >
                    빼기
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="inline-reason" style={{ padding: "8px 0 0" }}>
            <label className="sr-only" htmlFor="new-task-step">
              추가할 단계
            </label>
            <input
              id="new-task-step"
              onChange={(event) => setNewStep(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                if (event.repeat || event.nativeEvent.isComposing) return;
                appendStep();
              }}
              placeholder={kind === "task" ? "이 업무를 끝내려면 무엇을 해야 하나" : "부탁할 일을 단계로 적어 두면 그대로 넘어갑니다"}
              value={newStep}
            />
            <button className="btn" disabled={!newStep.trim()} onClick={appendStep} type="button">
              단계 추가
            </button>
          </div>
          <p className="t-meta">
            {kind === "task"
              ? "지금 아는 단계만 적어도 됩니다. 나중에 업무 상세에서 더할 수 있습니다."
              : "여기 적은 단계는 상대가 수락한 업무의 체크리스트가 됩니다."}
          </p>
        </fieldset>
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
