import { useEffect, useRef, useState } from "react";
import type React from "react";
import { Chip, ChipRow, ChipToggle } from "../../ds/Chip";
import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { createIdempotencyKey } from "../../lib/idempotency";
import { blockingChildrenOf, blockingPredecessorsOf, childProgressOf, hiddenPrecedingCountOf, isChildCancelled, isChildSettled, isRequestTask, startBlockedByPredecessors, visiblePredecessorsOf } from "./workRows";
import { useBrowserOperationGuard } from "../../lib/browserOperationGuard";

import {
  addWorkRequestComment,
  assignTask,
  getActionItems,
  runActionCommand,
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
  getTaskAssignments,
  getTaskProposals,
  getTasks,
  createTaskProposal,
  respondTaskProposal,
  withdrawTaskProposal,
  reopenTask,
  getWorkRequestAssigneeCandidates,
  listProjects,
  reassignTask,
  uploadTaskMaterial,
  uploadWorkRequestMaterial,
  attachWorkRequestMaterialLink,
} from "../../lib/api";
import { blockingChildReasonLabel, cancelReasonLabel, datePickerLabel, hiddenPredecessorsText, predecessorsUnfinishedText, projectLockedByPredecessorsText, derivedApprovalLabel, derivedAssignmentLabel, dueDayText, emptyActionLabel, formatDate, formatDateTime, formatMonthLong, isOverdue, isoDateInSeoul, personName, proposalFieldLabel, proposalKindLabel, selectLabel, seoulToday, taskStateLabel, weekdayNames, workRequestStateLabel } from "../../lib/labels";
import { DateField } from "../../ds/DateField";
import { ConfirmModal, Drawer, Modal, type OverlayShellProps } from "../../ds/Modal";
import { Skeleton } from "../../ds/Skeleton";
import { Checkbox, FieldMessage } from "../../ds/FormControls";
import { DropZone } from "../../ds/DropZone";
import { FileList } from "../../ds/FileList";
import { Icon } from "../../ds/icons/Icon";
import { Select } from "../../ds/Select";
import { EmptyValue } from "../../ds/Empty";
import { ProgressBar } from "../../ds/ProgressBar";
import type {
  ActionItemEnvelope,
  ChecklistItem,
  DirectTask,
  MaterialExtraction,
  Persona,
  RequestTimeline,
  TaskHistory,
  TaskHistoryDiff,
  TaskMaterial,
  TaskAssignmentsView,
  TaskChild,
  TaskDelivery,
  TaskProposal,
  TaskProposalsView,
  TaskMaterialKind,
  TaskReference,
  TaskOrigin,
  TaskPatch,
  TaskState,
  Project,
  WorkRequest,
} from "../../lib/viewModels";

export type TaskAction = "start" | "block" | "resume" | "complete" | "cancel";

/**
 * ★ 「이 업무를 지금 어디로 옮길 수 있나」를 정하는 **유일한 자리** (바퀴 5c).
 *
 * 표의 상태 셀 · 칸반의 드래그 · 행의 커맨드 단추(`TaskQuickActions`) **셋이 이 표 하나를 본다.**
 * 세 자리가 각자 `task.state` 로 분기하던 것을 여기로 모았다 — 규칙을 새로 만든 것이 아니라
 * 이미 세 곳에 같은 모양으로 적혀 있던 것을 한 벌로 합친 것이다(칸반의 `transitionFor` 가 원본이다).
 *
 * **`allowed_commands` 가 생기면 갈아끼울 자리가 여기다.** 서버가 봉투에 「이 업무에 지금 쓸 수 있는
 * 커맨드」를 실어 주면 `allowedTaskTransitions` 의 **몸통만** 그것을 읽게 바꾸면 된다 — 부르는 세 자리는
 * 한 줄도 안 고친다. 표 자체(`transitionFor`)는 그때 지운다.
 *
 * 지금 근거는 `task.state` 다. 이것이 «권한» 이 아니라 «그 상태에서 말이 되는 전이» 라는 점이 중요하다 —
 * 권한은 화면 바깥의 `canManageOwnTasks`(세션 봉투)가 이미 따로 쥐고 있고, 실제 차단은 서버가 한다.
 */
const transitionFor: Partial<Record<TaskState, Partial<Record<TaskState, TaskAction>>>> = {
  open: { in_progress: "start" },
  in_progress: { blocked: "block", done: "complete" },
  blocked: { in_progress: "resume" },
  done: { in_progress: "resume" },
};

/**
 * **`state="done"` 하나로 재개를 열지 않는다** (WORK-002 검수 R-1).
 *
 * 밖에서 보이는 `done` 은 두 가지를 합친다 — 요청 업무의 «완료 보고 제출»(승인 대기)과 «최종 완료» 다
 * (SPEC-003 §4). 앞의 것은 상대가 판단할 차례라 내가 되돌릴 자리가 아니고, 뒤의 것만 재개가 말이 된다.
 * 그 둘을 가르는 값은 `derived.approval` 뿐이다.
 *
 * `derived` 가 아예 없는 응답(이 계약 전의 서버)에서는 **지금까지 하던 대로** 둔다 — 모른다는 이유로
 * 있던 길을 닫으면 그것은 회귀다.
 */
function reopenBlockedByApproval(task: DirectTask): boolean {
  if (task.state !== "done") return false;
  if (!task.derived) return false;
  return task.derived.approval === "awaiting_review" || task.derived.approval === "awaiting_revision";
}

/** 한 업무가 지금 갈 수 있는 곳들. 칸반이 쓰던 순서(표에 적힌 차례) 그대로 돌려준다. */
export type TaskTransition = { to: TaskState; action: TaskAction };

export function allowedTaskTransitions(task: DirectTask): TaskTransition[] {
  const row = transitionFor[task.state];
  if (!row) return [];
  if (reopenBlockedByApproval(task)) return [];
  return Object.entries(row).map(([to, action]) => ({ to: to as TaskState, action: action as TaskAction }));
}

/** 그 전이를 지금 할 수 있나 — 커맨드 이름으로 묻는 자리(단추 하나하나가 이것을 쓴다). */
export function canTransition(task: DirectTask, action: TaskAction): boolean {
  return allowedTaskTransitions(task).some((transition) => transition.action === action);
}

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
        <Button variant="text" size="sm" onClick={() => setOpen((current) => !current)} type="button">
          {open ? "이력 접기" : "이력 보기"}
        </Button>
      </div>
      <p className="t-meta">
        {formatDate(isoDateInSeoul(task.created_at))} 생성 · 최근 변경 {formatDate(isoDateInSeoul(task.updated_at))}
        {task.state === "done" && " · 완료됨"}
        {task.state === "cancelled" && " · 취소됨"}
      </p>
      {open && failure && <p className="danger-text">{failure}</p>}
      {open && !failure && history === null && <Skeleton label="기록을 불러오는 중" rows={3} />}
      {open && history !== null && history.activity.length === 0 && <p className="t-meta">아직 기록이 없습니다.</p>}
      {open && history !== null && history.activity.length > 0 && (
        <ChipRow>
          {HISTORY_FILTERS.map((option) => (
            <Button
              aria-pressed={filter === option.id}
              key={option.id}
              onClick={() => setFilter(option.id)}
              size="sm"
              type="button"
              variant={filter === option.id ? "outlined" : "text"}
            >
              {option.label}
            </Button>
          ))}
        </ChipRow>
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
                  {version !== null && <Badge tone="outline">v{version}</Badge>}
                  {row.causation?.kind === "action_item" && <Badge tone="accent">AX를 통해</Badge>}
                </div>
                <p>{row.summary}</p>
                {row.reason && <p className="t-meta">사유: {row.reason}</p>}
                {version !== null && version > 1 && (
                  <Button variant="text" size="sm" onClick={() => void openDiff(version)} type="button">
                    변경 내용
                  </Button>
                )}
                {diff === "loading" && <Skeleton label="변경 내용을 불러오는 중" rows={2} />}
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
  if (!extraction) return <Badge className="extraction-status" data-status="none" tone="outline">내용 색인 없음</Badge>;
  if (extraction.status === "completed") {
    return (
      <Badge tone="accent" className="extraction-status" data-status="completed" title={`${extraction.chunk_count}개 구간 · ${extraction.char_count.toLocaleString()}자`}>
        내용 검색 가능
      </Badge>
    );
  }
  if (extraction.status === "queued" || extraction.status === "running") {
    return (
      <Badge className="extraction-status" data-status={extraction.status}>
        {extraction.status === "queued" ? "내용 추출 대기" : "내용 추출 중"}
      </Badge>
    );
  }
  if (extraction.status === "needs_ocr") {
    // A scan is not an empty document. Say it could not be read rather than letting a search look exhaustive.
    return (
      <Badge className="extraction-status" data-status="needs_ocr" title={extraction.failure_text ?? undefined}>
        스캔 문서 · 내용 검색 불가
      </Badge>
    );
  }
  if (extraction.status === "purged") {
    return (
      <Badge className="extraction-status" data-status="purged" tone="outline">
        완전 삭제됨
      </Badge>
    );
  }
  return (
    <Badge tone="danger" className="extraction-status" data-status={extraction.status} title={extraction.failure_text ?? undefined}>
      {extraction.status === "unsupported" ? "검색 미지원 형식" : "내용을 읽지 못함"}
      {extraction.failure_text ? ` · ${extraction.failure_text}` : ""}
    </Badge>
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
  personaId,
  personas,
  viewerIsRequester = false,
  viewerIsRecordRequester = false,
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
  presentation = "modal",
  onBack,
  backLabel,
}: {
  /**
   * 어느 «겹» 으로 설 것인가. **기본은 가운데 모달이다** (4차 발주 3).
   *
   * 지금까지 기본은 오른쪽 드로어였고, 요청 상세에서 갈아끼우는 자리만 모달이었다. 그래서 같은 업무
   * 상세가 어디서 열렸느냐에 따라 다른 겹으로 서고, 요청 상세(모달) 옆에 업무 상세(드로어)가 겹쳐
   * 뜰 수도 있었다. 이제 **넷이 다 가운데 모달**이라 전환이 한 자리에서 일어난다.
   *
   * `drawer` 는 남겨 둔다 — 이 부품을 쓰는 다른 화면이 생겼을 때 골격을 고르는 자리다.
   */
  presentation?: "drawer" | "modal";
  /** 넘기면 머리 왼쪽에 「뒤로」가 선다 — 이 겹을 닫지 않고 내용만 이전 것으로 되돌린다. */
  onBack?: () => void;
  backLabel?: string;
  /** Open another Task this one points at, inside the product rather than through a URL. */
  onOpenTask?: (taskId: string) => void;
  /** Whether this person may put someone else on work. Moving it is a command of its own, not a form field. */
  canAssign?: boolean;
  /** Settles the projections after the holder changed. */
  onChanged?: () => Promise<void> | void;
  /** 지금 이 화면을 보는 사람. **권한을 여기서 정하지 않는다** — 서버가 거절한다. 「내 차례인가」를 읽는 데만 쓴다. */
  personaId?: string;
  /** id 를 사람 이름으로 읽기 위한 목록. 없으면 id 가 그대로 보인다 — 없는 이름을 지어내지 않는다. */
  personas?: Persona[];
  /**
   * **이 업무의 요청자 자리에 내가 섰나** — 부르는 쪽이 요청 원장에서 읽어 넘긴다
   * (`isRequestOwner`: `requester_id` 또는 `promoted_by_member_id`).
   *
   * 드로어가 스스로 계산하지 않는 이유가 둘이다. (1) `origin.actor` 는 승격 요청에서 시스템 id 라
   * 누른 사람을 놓친다. (2) 요청 행을 읽을 권한은 업무와 다른 자리라, 드로어가 새 조회를 열면
   * 권한 밖에서 404 를 맞는다. **기본값은 `false`** — 모르면 없는 권한을 그리지 않는다.
   */
  viewerIsRequester?: boolean;
  /**
   * **요청 행의 요청자인가** — 재개만 이 값을 쓴다. 위 값보다 좁다.
   *
   * 서버가 재개를 `_requester_of`(= `requester_id` 하나)로 판정하기 때문이다. 제안 쪽
   * `_is_request_owner` 는 `promoted_by_member_id` 도 받으므로, 둘을 같은 값으로 그리면
   * **승격을 누른 사람에게 늘 403 인 재개 단추**가 선다 (재검수 N-1).
   */
  viewerIsRecordRequester?: boolean;
  task: DirectTask;
  ownerName: string;
  /** Navigate to the resource the origin names. Absent when the source is withheld. */
  onOpenSource?: (source: { type: string; id: string }) => void;
  canManage: boolean;
  busy: boolean;
  /** 전이를 보낸다. **`false` 면 서버가 거절한 것**이다 — 사유 입력 자리가 그때 열린 채로 남는다. */
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<boolean | void>;
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
  /** 하위 업무 한 건의 생성 의도 — 같은 제목으로 다시 누르는 재시도는 같은 키로 간다. */
  const subtaskAttempt = useRef<{ title: string; key: string } | null>(null);
  const [uploading, setUploading] = useState<TaskMaterialKind | null>(null);
  useBrowserOperationGuard(uploading !== null);
  const canLeave = () => {
    if (uploading === null) return true;
    onError('파일 업로드가 끝난 뒤 이동할 수 있습니다.');
    return false;
  };
  const close = () => {
    if (!canLeave()) return;
    onClose();
  };
  const openTask = (id: string) => { if (canLeave()) onOpenTask?.(id); };
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
  /** 완료 보고 모달이 열려 있나 — 초안(요약·산출물)은 그 모달이 든다 (5차 발주). */
  const [reporting, setReporting] = useState(false);
  const [handover, setHandover] = useState<{ assigneeId: string; reason: string } | null>(null);
  const [handoverChoices, setHandoverChoices] = useState<Persona[] | null>(null);
  /**
   * 담당 관계 — **현재와 대기를 각각** 읽는다 (V-18 · P-3). 서버가 이 조회를 아직 내지 않으면 `null` 로
   * 남고 그 구획을 통째로 그리지 않는다 — 없는 값을 「대기 없음」으로 단정하지 않는다.
   */
  const [assignments, setAssignments] = useState<TaskAssignmentsView | null>(null);
  /** 수락 후의 제안 — 취소 합의·조건 변경. 같은 이유로 `null` 은 「모른다」다. */
  const [proposals, setProposals] = useState<TaskProposalsView | null>(null);
  /** 열어 둔 사유 입력 — 취소 제안·그 응답·철회·재개가 이 한 자리를 쓴다. */
  const [prompt, setPrompt] = useState<
    | { kind: "propose"; proposalKind: "cancellation" }
    | { kind: "terms" }
    | { kind: "respond"; proposal: TaskProposal; agree: boolean }
    | { kind: "reopen" }
    /** 요청자가 「보완 요청」을 부르는 자리 — 계약이 사유를 필수로 받는다. */
    | { kind: "delivery_changes" }
    | null
  >(null);
  /**
   * 요청자의 **결과 확인** 자리 (4차 발주 5).
   *
   * 완료 보고의 최종 완료는 요청자가 낸다. 그 명령은 예전부터 판단 항목(`task.delivery`)에 있었고,
   * **여기서 새 API 를 만들지 않는다** — 열려 있는 그 항목을 찾아 같은 커맨드를 부를 뿐이다.
   * 못 찾으면 `null` 이고 단추를 세우지 않는다(내 차례가 아니거나 서버가 그 자리를 안 연 것이다).
   */
  const [deliveryDecision, setDeliveryDecision] = useState<ActionItemEnvelope | null>(null);
  /** 하위를 «어떻게» 만드는가 — 내가 직접 하거나(하위 Task), 남에게 요청하거나(하위 요청)다. */
  const [subtaskRequest, setSubtaskRequest] = useState(false);
  const [requestCandidates, setRequestCandidates] = useState<Persona[]>([]);
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
  /**
   * 지금 고쳐 쓰는 중인가 — **회차 충돌로 다시 읽을 때 그 입력을 지키는 자리다** (WORK-003 Phase 6).
   *
   * 저장이 회차 충돌로 거절되면 부르는 쪽이 최신 값을 다시 읽어 온다. 그때 초기화 effect 가 그대로
   * 돌면 서버 값이 **사람이 쓰던 문장을 덮어쓴다** — 충돌을 알려 주려다 그 사람의 일을 지운다.
   */
  const editingRef = useRef(false);
  const dirty =
    title.trim() !== task.title ||
    description.trim() !== (task.description ?? "") ||
    startDate !== (task.start_date ?? "") ||
    dueDate !== (task.due_date ?? "");
  editingRef.current = dirty;

  /**
   * 지금 고쳐 쓰는 중인가 — **회차 충돌로 다시 읽을 때 그 입력을 지키는 자리다** (WORK-003 Phase 6).
   *
   * 저장이 회차 충돌로 거절되면 부르는 쪽이 최신 값을 다시 읽어 온다. 그때 아래 effect 가 그대로
   * 돌면 서버 값이 **사람이 쓰던 문장을 덮어쓴다** — 충돌을 알려 주려다 그 사람의 일을 지운다.
   * 그래서 쓰던 것이 있으면 새 값으로 갈아끼우지 않는다: 최신 값은 회차로 들어오고(`settledVersion`),
   * 화면의 글자는 사람 것으로 남는다.
   */
  useEffect(() => {
    if (editingRef.current) return;
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
    void (async () => {
      try {
        const detail = await getTask(task.task_id);
        if (cancelled) return;
        setChecklist(detail.checklist ?? []);
        setReferences(detail.references ?? []);
        setDelivery(detail.delivery ?? null);
        setChildren(detail.children ?? []);
        setParentTask(detail.parent ?? null);
      } catch {
        if (!cancelled) setChecklist([]);
        return;
      }
      /*
       * 담당 관계와 제안은 **상세를 읽은 «뒤에»** 읽는다.
       *
       * 이유가 둘이다. (1) 못 읽는 업무에는 물어볼 것이 없다 — 상세가 실패하면 이 둘도 부르지 않는다.
       * (2) 열자마자 세 요청이 한꺼번에 나가면 서랍이 그리는 첫 화면(체크리스트·하위·참고 업무)이
       * 그만큼 늦어진다. 이 둘은 **첫 화면에 필요한 값이 아니다.**
       *
       * 서버가 이 조회를 아직 내지 않으면 `null` 로 남기고 그 구획을 통째로 그리지 않는다 —
       * 없는 것을 「없음」으로 그리면 담당 변경 대기가 있는데 화면이 없다고 말하게 된다.
       */
      try {
        const view = await getTaskAssignments(task.task_id);
        if (!cancelled) setAssignments(view ?? null);
      } catch {
        if (!cancelled) setAssignments(null);
      }
      try {
        const rows = await getTaskProposals(task.task_id);
        if (!cancelled) setProposals(rows ?? null);
      } catch {
        if (!cancelled) setProposals(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [task.task_id, task.version]);

  /**
   * 담당 관계와 제안 — **두 조회가 아직 서지 않은 서버에서는 조용히 비운다.**
   *
   * 없는 것을 「없음」으로 그리면 담당 변경 대기가 있는데 화면이 없다고 말하게 된다. 그래서 실패는
   * `null` 로 남기고 그 구획 자체를 그리지 않는다(부재의 종류를 가른다).
   */


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
  /** 요청으로 생겼고 이미 수락된 업무인가 — 직접 취소가 막히고 합의 취소만 남는 자리다 (V-19). */
  const requestTaskAccepted = isRequestTask(task) && Boolean(task.assignee) && task.derived?.assignment !== "awaiting_acceptance";
  /**
   * **이 업무를 지금 «내가» 드는가** — 수행 상태를 옮기는 명령(시작·완료·막힘·취소)이 여기 달렸다.
   *
   * 서버는 그 명령들을 **활성 담당에게만** 연다(`work_tasks.task()` 의 `_held_by`). 그래서
   * 아무도 들지 않는 업무(수락 전 요청 Task — 담당이 `null` 인 것이 그 모습이다)나 남이 드는 업무에
   * 단추를 세우면 **눌러도 404 다.** 검수 F-1 이 짚은 「업무 취소」가 정확히 그 자리였다.
   *
   * 값을 모르는 화면(오늘·캘린더는 `personaId` 를 넘기지 않는다)에서는 **지금까지대로 둔다** —
   * 그 화면들이 내는 것은 내가 드는 업무뿐이라, 모른다고 있던 길을 닫으면 그것이 회귀다.
   */
  const heldByNobody = !task.assignee && (isRequestTask(task) || task.derived?.assignment === "awaiting_acceptance");
  const heldByOther = Boolean(personaId && task.assignee && task.assignee.member_id !== personaId);
  const viewerDrives = !heldByNobody && !heldByOther && task.access !== "read_only";
  /* 「확인 대기」는 상태가 아니라 파생 표시다 — 외부 계약에 `completion_submitted` 가 없다
     (SPEC-003 §4 · SPEC-001 §4 State). `delivery` 는 SPEC-002 가 이미 내던 같은 사실의 다른 이름이라
     둘 중 하나만 와도 읽힌다. */
  const awaitingReview = task.derived?.approval === "awaiting_review" || delivery?.status === "awaiting_review";
  const requesterName = task.origin?.actor ? personName(task.origin.actor.display_name) : "요청자";

  /* 요청자의 결과 확인 — 열려 있는 판단 항목을 찾아 둔다. 내 차례가 아니면 찾지 않는다. */
  useEffect(() => {
    if (!awaitingReview || !viewerIsRecordRequester) {
      setDeliveryDecision(null);
      return;
    }
    let cancelled = false;
    void getActionItems()
      .then((items) => {
        if (cancelled) return;
        setDeliveryDecision(
          items.find(
            (item) =>
              item.kind === "task.delivery" &&
              item.resource?.id === task.task_id &&
              item.status === "awaiting_review" &&
              item.allowed_commands.some((command) => command.id === "accept"),
          ) ?? null,
        );
      })
      .catch(() => {
        // 못 읽었다고 「없다」로 단정하지 않는다 — 단추만 서지 않고, 판단 항목 화면의 길은 그대로다.
        if (!cancelled) setDeliveryDecision(null);
      });
    return () => {
      cancelled = true;
    };
  }, [awaitingReview, viewerIsRecordRequester, task.task_id, task.version]);

  /** 요청자가 결과를 인정한다 — **이것이 최종 완료다** (4차 발주 5 · `accept_delivery`). */
  const acceptDelivery = async () => {
    if (!deliveryDecision) return;
    onError(null);
    try {
      await runActionCommand(deliveryDecision.action_item_id, "accept", { expected_version: deliveryDecision.expected_version });
      onNotice?.("완료를 인정했습니다. 이 업무가 완료되었습니다.");
      await onChanged?.();
    } catch (error) {
      onError(error instanceof Error ? error.message : "완료를 인정하지 못했습니다.");
    }
  };

  /** 아직 아니라고 말한다 — 사유가 필수이고, 업무는 진행 중으로 돌아가 다음 회차를 연다. */
  const requestDeliveryChanges = async (reason: string): Promise<boolean> => {
    if (!deliveryDecision) return false;
    onError(null);
    try {
      await runActionCommand(deliveryDecision.action_item_id, "request_changes", {
        expected_version: deliveryDecision.expected_version,
        reason,
      });
      onNotice?.("보완을 요청했습니다. 담당자가 보완해 다시 보고합니다.");
      await onChanged?.();
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "보완을 요청하지 못했습니다.");
      return false;
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

  // Enter 로도 보낼 수 있지만, 빈 채로 눌렀을 때 아무 일도 일어나지 않으면 그건 침묵한 실패다 (v2 09·RULES 12)
  const [blockReasonError, setBlockReasonError] = useState<string | null>(null);
  const submitBlock = async () => {
    const reason = blockReason.trim();
    if (!reason) {
      setBlockReasonError("막힘 사유를 적어 주세요.");
      return;
    }
    setBlockReasonError(null);
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
      /* v2: 제안일 뿐 **기존 담당은 닫히지 않는다**(V-18) — 그래서 「바꿨다」가 아니라 「보냈다」다.
         상대가 수락하는 순간 교체가 한 덩어리로 일어나고, 중간에 담당 없는 구간이 생기지 않는다. */
      onNotice?.("담당 변경을 제안했습니다. 상대가 수락할 때까지 기존 담당이 그대로입니다.");
      await onChanged?.();
      try {
        setAssignments((await getTaskAssignments(task.task_id)) ?? null);
      } catch {
        // 제안 자체는 성공했다 — 다시 읽기 실패를 제안 실패로 말하지 않는다.
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : "담당자를 바꾸지 못했습니다.");
    }
  };

  /** 제안 목록을 다시 읽는다. 못 읽어도 명령 자체는 성공했으므로 들고 있던 것을 그대로 남긴다. */
  async function readProposals(): Promise<TaskProposalsView | null> {
    try {
      return (await getTaskProposals(task.task_id)) ?? proposals;
    } catch {
      return proposals;
    }
  }

  /** 직속 하위의 셈 — 서버 값이 먼저이고, 취소는 완결과 따로 센다. */
  const childProgress = childProgressOf({ ...task, children });
  /** 상위 완료를 막는 하위 — **이름으로** 보여 준다 (U-7 · I-7). */
  const blockingChildren = blockingChildrenOf({ ...task, children });
  /**
   * 시작을 막는 선행 (SPEC-001 U-14 · WORK-003 Phase 6).
   *
   * **하위와 다른 축이다** — 하위는 완료를, 선행은 시작을 막는다. 그래서 목록도 문장도 따로 둔다.
   * 서버가 거절해도 같은 문장이 나오도록 문구는 `predecessorsUnfinishedText` 한 자리에서 나온다.
   */
  const blockingPredecessors = blockingPredecessorsOf(task);
  const startBlocked = startBlockedByPredecessors(task);
  const blockedText = predecessorsUnfinishedText(blockingPredecessors.map((row) => row.title));
  const hiddenPredecessors = hiddenPrecedingCountOf(task);
  const visiblePredecessors = visiblePredecessorsOf(task);

  /** 하위 요청 모달의 수신 후보 — 열 때 한 번만 읽는다. */
  const openSubtaskRequest = async () => {
    setSubtaskRequest(true);
    if (requestCandidates.length > 0) return;
    try {
      setRequestCandidates(await getWorkRequestAssigneeCandidates());
    } catch {
      setRequestCandidates([]);
    }
  };

  /** 지금 이 업무에 응답을 기다리는 제안 — 같은 종류는 하나뿐이다 (SPEC-003 §4 Validation). */
  const pendingProposal = proposals?.pending?.[0] ?? null;

  /**
   * 수락 후의 취소·조건 변경은 **제안이다** (V-19·V-20).
   * 보내는 것만으로 아무것도 바뀌지 않고, 담당자가 동의해야 그때 움직인다.
   */
  const proposeChange = async (kind: "cancellation" | "terms_change", reason: string, payload?: Record<string, unknown>) => {
    onError(null);
    try {
      const answered = await createTaskProposal(task.task_id, current.version, { kind, reason: reason || undefined, payload });
      moved(answered?.task_version);
      setProposals(await readProposals());
      await settleVersion();
      onNotice?.(kind === "cancellation" ? "취소를 제안했습니다. 담당자가 동의해야 취소됩니다." : "조건 변경을 제안했습니다. 담당자가 동의해야 반영됩니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "제안을 보내지 못했습니다.");
    }
  };

  const respondProposal = async (proposal: TaskProposal, agree: boolean, reason: string) => {
    onError(null);
    try {
      const answered = await respondTaskProposal(task.task_id, proposal.proposal_id, current.version, { agree, reason: reason || undefined });
      moved(answered?.task_version);
      setProposals(await readProposals());
      await settleVersion();
      onNotice?.(agree ? "제안에 동의했습니다." : "동의하지 않았습니다. 업무는 그대로입니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "제안에 답하지 못했습니다.");
    }
  };

  const cancelProposal = async (proposal: TaskProposal) => {
    onError(null);
    try {
      await withdrawTaskProposal(task.task_id, proposal.proposal_id, current.version);
      setProposals(await readProposals());
      await settleVersion();
      onNotice?.("제안을 철회했습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "제안을 철회하지 못했습니다.");
    }
  };

  /**
   * 재개 — **완료된 상위가 있으면 서버가 거부한다**(L-13). 그 거부를 화면이 미리 흉내내지 않는다:
   * 상위를 읽을 권한이 없어 안 보이는 상위도 서버는 본다.
   */
  const reopen = async (reason: string) => {
    onError(null);
    try {
      await reopenTask(task.task_id, current.version, reason || undefined);
      await settleVersion();
      onNotice?.("업무를 재개했습니다. 이전 완료 이력과 회차는 그대로 남습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 재개하지 못했습니다.");
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
    /* 하위 업무도 생성 명령이라 멱등 키를 싣는다. 쓴 제목이 그대로면 재시도는 같은 키를 다시 보내고,
       제목을 고쳐 다시 적으면 그것은 새 하위 업무이므로 새 키를 받는다. */
    if (subtaskAttempt.current?.title !== title) subtaskAttempt.current = { title, key: createIdempotencyKey() };
    try {
      const created = await createDirectTask(title, { parent_task_id: task.task_id }, subtaskAttempt.current.key);
      subtaskAttempt.current = null;
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
      const detached = await detachTaskMaterial(task.task_id, material.binding_id);
      setMaterials((rows) => (rows ?? []).filter((item) => item.binding_id !== material.binding_id));
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
              <Button size="sm" disabled={uploading !== null || busy} onClick={() => ref.current?.click()} type="button">
                {uploading === kind ? "올리는 중…" : "파일 추가"}
              </Button>
              <Button variant="text" size="sm" disabled={uploading !== null || busy} onClick={() => setLinkDraft(linkDraft?.kind === kind ? null : { kind, url: "", label: "" })}
                type="button"
              >
                링크 추가
              </Button>
            </>
          )}
        </div>
        {editable && linkDraft?.kind === kind && (
          /* A link is not a file: SCAX records where the work lives and the words a person reads, nothing more. */
          <div className="form-stack link-draft">
            <div className="scax-field">
              <label className="scax-field__label" htmlFor={`material-link-url-${kind}`}>{kind === "input" ? "참고 자료" : "산출물"} 링크 주소</label>
              <input
                id={`material-link-url-${kind}`}
                inputMode="url"
                onChange={(event) => setLinkDraft({ ...linkDraft, url: event.target.value })}
                placeholder="https://"
                value={linkDraft.url}
              />
            </div>
            <div className="scax-field">
              <label className="scax-field__label" htmlFor={`material-link-label-${kind}`}>{kind === "input" ? "참고 자료" : "산출물"} 링크 이름</label>
              <input
                id={`material-link-label-${kind}`}
                onChange={(event) => setLinkDraft({ ...linkDraft, label: event.target.value })}
                placeholder="사람이 읽는 이름"
                value={linkDraft.label}
              />
            </div>
            <div className="row-actions">
              <Button variant="solid" tone="primary" size="sm" disabled={uploading !== null || busy} onClick={() => void attachLink(kind)} type="button">
                연결
              </Button>
              <Button variant="text" size="sm" onClick={() => setLinkDraft(null)} type="button">
                취소
              </Button>
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
              <li key={item.binding_id}>
                {item.source_kind === "resource_ref" ? (
                  /* It lives inside the product, so it opens inside the product — and only when it resolved. */
                  item.resource && onOpenTask ? (
                    <Button variant="inline" onClick={() => openTask(item.resource!.id)} type="button">
                      {item.name}
                    </Button>
                  ) : (
                    <span>{item.name}</span>
                  )
                ) : (
                  <a href={item.url ?? taskMaterialContentUrl(task.task_id, item.material_id)} rel="noreferrer" target="_blank">
                    {item.name}
                  </a>
                )}
                {/* SCAX holds no bytes and pinned no revision, so the reader is told it can move under them. */}
                {item.mutable_source && <Badge tone="outline">변경 가능한 링크</Badge>}
                <span className="t-meta">
                  {item.mutable_source
                    ? formatDate(isoDateInSeoul(item.created_at))
                    : `${formatBytes(item.size_bytes)} · ${formatDate(isoDateInSeoul(item.created_at))}`}
                </span>
                <ExtractionStatus extraction={item.extraction ?? null} />
                {editable && (
                  <Button variant="text" size="sm" onClick={() => void detach(item)} type="button">
                    떼기
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  };

  /* 껍데기만 갈린다 — 안의 구성·명령·상태는 한 벌 그대로다. 두 골격은 `OverlayShellProps` 를 같이 받는다. */
  const Shell: (props: OverlayShellProps) => React.ReactElement = presentation === "modal" ? Modal : Drawer;

  return (
    <>
      <Shell
          closeLabel="상세 닫기"
        onBack={onBack}
        backLabel={backLabel}
        footer={
          canManage ? (
            <>
              {/*
                * **수락된 요청 업무의 직접 취소는 막혀 있다**(V-19 · `WORK_CANCEL_REQUIRES_AGREEMENT`) —
                * 그 자리에서 `cancelled` 로 가는 길은 «합의 취소» 하나다. 그래서 요청자에게는 「취소 제안」을
                * 내고, 직접 취소는 본인·배정 업무와 수락 전 요청에만 남긴다. 최종 판정은 서버가 한다.
                */}
              {/* 직접 취소는 **내가 드는 업무**에서만 부를 수 있다 — 서버가 활성 담당에게만 연다. */}
              {!closed && !requestTaskAccepted && viewerDrives && (
                <Button variant="text" disabled={busy} onClick={() => setConfirmCancel(true)} type="button">
                  업무 취소
                </Button>
              )}
              {/* 제안은 **요청자의 자리다** (SPEC-003 §5 · `application.py` 의 `_is_request_owner`).
                  담당자에게도 세우면 그 드로어의 가장 흔한 사용자가 누를 때마다 403 을 본다 (검수 F-1). */}
              {!closed && requestTaskAccepted && viewerIsRequester && !pendingProposal && (
                <>
                  <Button variant="text" disabled={busy} onClick={() => setPrompt({ kind: "propose", proposalKind: "cancellation" })} type="button">
                    취소 제안
                  </Button>
                  {/* 수락 뒤에는 `PATCH` 가 거부된다(V-20 · `WORK_REQUEST_LOCKED_AFTER_ACCEPT`) —
                      조건을 바꾸는 길은 제안 하나뿐이라 그 자리를 여기 둔다. */}
                  <Button variant="text" disabled={busy} onClick={() => setPrompt({ kind: "terms" })} type="button">
                    조건 변경 제안
                  </Button>
                </>
              )}
              <span className="scax-drawer__spacer" />
              {editable && dirty && (
                <Button disabled={busy} onClick={() => void save()} type="button">
                  변경 저장
                </Button>
              )}
              {task.state === "in_progress" && (
                <Button disabled={busy || isBlocking} onClick={() => setIsBlocking(true)} type="button">
                  막힘
                </Button>
              )}
              {/*
                * **선행이 남았으면 누르기 전에 막는다** (SPEC-001 U-14). 눌린 뒤 서버 거절도 같은
                * 문장이라, 사람은 두 경로에서 같은 사실을 읽는다.
                */}
              {task.state === "open" && (
                <Button variant="solid" tone="primary" disabled={busy || startBlocked} onClick={() => void onTransition(current, "start")} type="button">
                  시작
                </Button>
              )}
              {/*
                * **`open` 에서도 [완료]가 선다** (SPEC-001 §4 State · SPEC-003 §4 · 7-C).
                * 「시작하지 않고 끝난 일」이 있고, 그 전이는 **상세 상단에서만** 부른다 — 행 액션에는
                * `open` 의 다음 한 걸음([시작])만 둔다.
                *
                * 미완결 하위로 막힐지는 **서버가 판정한다.** 화면이 `blocking` 을 보고 단추를 지우지
                * 않는다 — 내가 못 읽는 하위도 서버는 세기 때문에, 0 을 「완료해도 된다」로 읽으면 틀린다.
                */}
              {(task.state === "in_progress" || task.state === "open") && !reviewed && (
                /* `시작 전 → 완료` 직행도 같은 게이트를 지난다 — 그 길이 열려 있으면 시작 게이트에
                   우회로가 생긴다. `진행 중` 의 완료는 선행을 보지 않는다(막는 것은 시작이다). */
                <Button variant="solid" tone="primary" disabled={busy || startBlocked} onClick={() => void complete()} type="button">
                  완료 처리
                </Button>
              )}
              {startBlocked && <small className="t-meta scax-blocked-note">{blockedText}</small>}
              {(task.state === "in_progress" || task.state === "blocked") && reviewed && (
                <Button variant="solid" tone="primary" disabled={busy} onClick={() => setReporting(true)} type="button">
                  완료 보고
                </Button>
              )}
              {task.state === "blocked" && (
                <Button variant="outlined" tone="primary" size="sm" disabled={busy} onClick={() => void onTransition(current, "resume")} type="button">
                  재개
                </Button>
              )}
              {/*
                * **재개는 `state` 하나로 서지 않는다** (검수 R-1). 밖에서 보이는 `done` 은 승인 대기와
                * 최종 완료를 합치므로, 되돌릴 수 있는 자리인지는 `derived.approval` 이 가른다.
                * 승인 대기 중이면 지금은 상대의 차례이고 여기에는 부를 명령이 없다.
                */}
              {/* **재개도 자리가 갈린다** (`_require_may_reopen`): 요청 업무는 **요청자**, 본인·배정 업무는
                  **담당자**다. 한 조건으로 그리면 둘 중 하나는 늘 403 을 본다. */}
              {/* 재개의 요청자 판정은 **`requester_id` 하나**다 — 제안과 같은 값으로 묶지 않는다(N-1). */}
              {task.state === "done" && !reopenBlockedByApproval(task) && (reviewed ? viewerIsRecordRequester : viewerDrives) && (
                <Button variant="outlined" tone="primary" size="sm" disabled={busy} onClick={() => setPrompt({ kind: "reopen" })} type="button">
                  재개
                </Button>
              )}
              {closed && (
                <Button onClick={close} type="button">
                  닫기
                </Button>
              )}
            </>
          ) : (
            <Button onClick={close} type="button">
              닫기
            </Button>
          )
        }
        headerExtra={
          <ChipRow>
            <StatusText state={task.state} />
            {isOverdue(task, today) && <Badge tone="danger">기한 초과</Badge>}
            <Badge tone="outline">v{current.version}</Badge>
          </ChipRow>
        }
        kicker="업무 상세"
        label="업무 상세"
        onClose={close}
        title={task.title}
      >
        {/*
          * 「완료 확인 대기」 (4차 발주 5) — 보고를 낸 쪽에는 **기다린다는 사실**이, 요청자에게는
          * **판단하는 자리**가 선다. 판단 명령은 새로 만들지 않았다: 예전부터 있던 `task.delivery`
          * 판단 항목의 커맨드 둘(`accept`·`request_changes`)을 그대로 부른다.
          */}
        {awaitingReview && (
          <section aria-label="완료 확인 대기" className="drawer-section notice">
            <h4>완료 확인 대기</h4>
            <p>
              {deliveryDecision
                ? "담당자가 완료 보고를 보냈습니다. 요청한 결과가 충족됐는지 확인해 주세요."
                : `${requesterName}에게 결과 확인을 요청했습니다. ${requesterName}가 완료로 인정하면 이 업무가 완료됩니다.`}
            </p>
            {delivery?.summary && <p className="prewrap t-meta">보고한 결과: {delivery.summary}</p>}
            {deliveryDecision && (
              <div className="row-actions">
                <Button disabled={busy} onClick={() => void acceptDelivery()} size="sm" tone="primary" type="button" variant="solid">
                  완료 인정
                </Button>
                <Button disabled={busy} onClick={() => setPrompt({ kind: "delivery_changes" })} size="sm" type="button" variant="outlined">
                  보완 요청
                </Button>
              </div>
            )}
          </section>
        )}
        {delivery?.status === "awaiting_revision" && delivery.last_reason && (
          <section aria-label="보완 필요" className="drawer-section notice danger">
            <h4>보완 필요</h4>
            <p className="prewrap">{delivery.last_reason}</p>
            <p className="t-meta">{delivery.rounds}번째 보고까지 진행했습니다. 보완한 뒤 다시 완료 보고를 보내세요.</p>
          </section>
        )}
        {/*
          * **담당 변경 대기 — 기존 담당과 새 제안이 «각각» 읽힌다** (U-8 · V-18).
          * 제안이 서도 기존 담당은 닫히지 않으므로, 한 줄로 합쳐 쓰면 책임 공백이 화면에서 생긴다.
          * 이 조회를 아직 내지 않는 서버에서는 구획 자체를 그리지 않는다 — 「대기 없음」으로 단정하지 않는다.
          */}
        {assignments?.pending && (
          <section aria-label="담당 변경 대기" className="drawer-section notice">
            <h4>담당 변경 대기</h4>
            <p>
              지금 담당은 <b>{displayNameOf(personas ?? [], assignments.current?.assignee_id, ownerName)}</b> 이고,
              <b> {displayNameOf(personas ?? [], assignments.pending.assignee_id, "새 담당 후보")}</b> 에게 담당 변경을 제안해 두었습니다.
            </p>
            <p className="t-meta">
              상대가 수락하면 그 자리에서 한 덩어리로 바뀝니다. 그때까지 담당은 그대로입니다.
              {assignments.pending.decline_reason ? ` · 사유: ${assignments.pending.decline_reason}` : ""}
            </p>
          </section>
        )}
        {/*
          * **응답을 기다리는 제안** — 취소 합의와 조건 변경 (V-19·V-20 · D-6).
          * 제안만으로는 아무것도 바뀌지 않는다. 답할 수 있는 사람은 담당자뿐이고, 최종 판정은 서버가 한다.
          */}
        {pendingProposal && (
          <section aria-label="응답 대기 제안" className="drawer-section notice">
            <h4>{proposalKindLabel[String(pendingProposal.kind)] ?? "제안"}</h4>
            {pendingProposal.reason && <p className="prewrap">{pendingProposal.reason}</p>}
            {/* 조건 변경은 **무엇을 바꾸자는 것인지** 보여야 답할 수 있다. 서버가 실어 준 값 그대로 읽는다. */}
            {pendingProposal.kind === "terms_change" && pendingProposal.payload && (
              <dl className="meta-grid columns">
                {Object.entries(pendingProposal.payload).map(([field, value]) => (
                  <div key={field}>
                    <dt>{proposalFieldLabel[field] ?? field}</dt>
                    <dd>{field === "due_date" && typeof value === "string" ? formatDate(value) : String(value ?? "—")}</dd>
                  </div>
                ))}
              </dl>
            )}
            <p className="t-meta">
              {pendingProposal.kind === "cancellation"
                ? "담당자가 동의하면 이 업무가 취소됩니다. 동의 전에는 아무것도 바뀌지 않습니다."
                : "담당자가 동의하면 조건이 바뀝니다. 동의 전에는 원래 조건이 그대로입니다."}
            </p>
            <div className="row-actions">
              {/* 응답은 담당자의 자리다. 서버가 그 판정을 다시 하므로 화면은 자리만 연다. */}
              {personaId && task.assignee?.member_id === personaId && (
                <>
                  <Button variant="solid" tone="primary" size="sm" disabled={busy} onClick={() => setPrompt({ kind: "respond", proposal: pendingProposal, agree: true })} type="button">
                    동의
                  </Button>
                  <Button variant="outlined" tone="neutral" size="sm" disabled={busy} onClick={() => setPrompt({ kind: "respond", proposal: pendingProposal, agree: false })} type="button">
                    동의하지 않음
                  </Button>
                </>
              )}
              {personaId && pendingProposal.proposed_by === personaId && (
                <Button variant="text" size="sm" disabled={busy} onClick={() => void cancelProposal(pendingProposal)} type="button">
                  제안 철회
                </Button>
              )}
            </div>
          </section>
        )}
        {/*
          * **완료를 막는 하위를 이름으로 보여 준다** (U-7 · I-7).
          *
          * 이 목록은 **안내이지 관문이 아니다** — 비어 있다고 완료가 통과한다는 뜻이 아니다.
          * 내가 읽을 수 없는 하위는 여기에도 셈에도 없지만 서버는 그것까지 세고 거절한다(U-15).
          */}
        {blockingChildren.length > 0 && (
          <section aria-label="완료를 막는 하위" className="drawer-section notice">
            <h4>아직 끝나지 않은 하위가 있습니다</h4>
            <ul className="material-list">
              {blockingChildren.map((child) => (
                <li key={child.task_id}>
                  <Button aria-label={`${child.title} 열기`} variant="inline" onClick={() => openTask(child.task_id)} type="button">
                    {child.title}
                  </Button>
                  <span className="t-meta">{blockingChildReasonLabel[child.why] ?? child.why}</span>
                </li>
              ))}
            </ul>
            <p className="t-meta">이 업무를 최종 완료하려면 위 하위가 먼저 끝나야 합니다. 취소된 하위는 세지 않습니다.</p>
          </section>
        )}
        <div className="form-stack">
          {canAssign && !readOnly && (
            /* Moving the work is its own act, so it is a command here rather than a field in the form below. */
            <div className="handover">
              <Button variant="text" size="sm" onClick={() => void openHandover()} type="button">
                담당자 변경
              </Button>
              {handover && (
                <div className="form-stack link-draft">
                  <div className="scax-field">
                    <span>담당자 변경 대상</span>
                    <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
                      id={`task-handover-${task.task_id}`}
                      label="담당자 변경 대상"
                      onChange={(next) => setHandover({ ...handover, assigneeId: next })}
                      options={(handoverChoices ?? []).map((choice) => ({ value: choice.id, label: personName(choice.display_name) }))}
                      placeholder="담당자 고르기"
                      value={handover.assigneeId}
                    />
                  </div>
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor={`task-handover-reason-${task.task_id}`}>담당자 변경 사유</label>
                    <input
                      id={`task-handover-reason-${task.task_id}`}
                      onChange={(event) => setHandover({ ...handover, reason: event.target.value })}
                      placeholder="왜 옮기는지 적어 두면 이력에 남습니다"
                      value={handover.reason}
                    />
                  </div>
                  <div className="row-actions">
                    <Button variant="solid" tone="primary" size="sm" disabled={busy} onClick={() => void submitHandover()} type="button">
                      변경
                    </Button>
                    <Button variant="text" size="sm" onClick={() => setHandover(null)} type="button">
                      취소
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
          {task.origin && (
            /* Only a real counterpart or a real source is named, and it is named as what happened rather than as a
               role column. A task nobody handed over has neither. */
            <p aria-label="업무 출처" className="origin-chip">
              {originSentence(task.origin) && <Badge tone="outline">{originSentence(task.origin)}</Badge>}
              {task.origin.source &&
                (onOpenSource ? (
                  <Button variant="inline" onClick={() => { if (canLeave()) onOpenSource(task.origin!.source!); }} type="button">
                    {task.origin.source.title ?? "출처 보기"}
                  </Button>
                ) : (
                  <small className="t-meta">{task.origin.source.title}</small>
                ))}
            </p>
          )}
          <div className="scax-field">
            <label className="scax-field__label" htmlFor={`task-title-${task.task_id}`}>제목</label>
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
                <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames} disabled={!editable} hideLabel id={`task-start-${task.task_id}`} label="시작일" onChange={setStartDate} value={startDate} />
              </dd>
            </div>
            <div>
              <dt>기한</dt>
              <dd>
                <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames} disabled={!editable} hideLabel id={`task-due-${task.task_id}`} label="기한" onChange={setDueDate} value={dueDate} />
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
              <ProgressBar ariaLabel="진행률" done={checklist.filter((item) => item.done).length} total={checklist.length} />
            )}
            {checklist !== null && checklist.length > 0 && (
              <ul className="checklist">
                {checklist.map((item, index) => (
                  <li className={item.done ? "scax-checklist__row done" : "scax-checklist__row"} data-item-id={item.item_id} key={item.item_id}>
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
                        <Checkbox checked={item.done} disabled={!canManage || busy} onChange={(next) => void toggleStep(item, next)}>
                          {item.text}
                        </Checkbox>
                        {canManage && (
                          <>
                            {/* Order moves with buttons, not only with a pointer: a drag would strand keyboard and touch. */}
                            <Button variant="text" size="sm" aria-label={`${item.text} 위로`} disabled={busy || index === 0} onClick={() => void moveStep(item, -1)}
                              type="button"
                            >
                              <Icon name="arrow-up" size={14} />
                            </Button>
                            <Button variant="text" size="sm" aria-label={`${item.text} 아래로`} disabled={busy || index === checklist.length - 1} onClick={() => void moveStep(item, 1)}
                              type="button"
                            >
                              <Icon name="arrow-down" size={14} />
                            </Button>
                            <Button variant="text" size="sm" aria-label={`${item.text} 수정`} onClick={() => setEditingStep({ itemId: item.item_id, text: item.text })}
                              type="button"
                            >
                              수정
                            </Button>
                            <Button variant="text" size="sm" aria-label={`${item.text} 삭제`} onClick={() => void removeStep(item)}
                              title="목록에서 빼고 이 업무의 기록에는 남깁니다"
                              type="button"
                            >
                              삭제
                            </Button>
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
                <Button size="sm" disabled={busy || !newStep.trim()} onClick={() => void addStep()} type="button">
                  추가
                </Button>
              </div>
            )}
          </section>
          )}
          <div className="scax-field">
            <label className="scax-field__label" htmlFor={`task-description-${task.task_id}`}>업무 내용</label>
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
        {/*
          * 막힘 사유 입력 (4차 발주 4).
          *
          * 예전에는 입력과 단추 둘이 **한 줄 flex**(`.inline-reason`)로 서서, 좁은 자리에서는 입력칸이
          * 단추에 밀려 몇 글자만 보였다 — 사유를 적는 칸이 사유를 못 읽는 칸이었다. 이제 입력이 한 줄을
          * 통째로 쓰고 단추는 그 아래 오른쪽에 선다. **공백이면 「막힘 처리」가 비활성**이고, 그 상태가
          * 왜인지는 바로 위 도움말이 말한다 — 눌러도 아무 일이 없는 단추를 두지 않는다.
          */}
        {isBlocking && (
          <section aria-label="막힘 사유 입력" className="drawer-section scax-block-reason">
            <h4>막힘 사유 입력</h4>
            <div className="scax-field">
              <label className="scax-field__label" htmlFor={`block-reason-${task.task_id}`}>
                막힘 사유
              </label>
              <input
                aria-invalid={blockReasonError ? true : undefined}
                autoFocus
                className="scax-block-reason__input"
                id={`block-reason-${task.task_id}`}
                onChange={(event) => {
                  setBlockReason(event.target.value);
                  setBlockReasonError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submitBlock();
                  if (event.key === "Escape") setIsBlocking(false);
                }}
                placeholder="무엇 때문에 막혔는지 적어 주세요"
                value={blockReason}
              />
            </div>
            <FieldMessage error={blockReasonError} help="적어 둔 사유는 카드와 목록에 그대로 보입니다." />
            <div className="scax-block-reason__actions">
              <Button variant="text" size="sm" onClick={() => setIsBlocking(false)} type="button">
                입력 취소
              </Button>
              <Button variant="solid" tone="primary" size="sm" disabled={busy || !blockReason.trim()} onClick={() => void submitBlock()} type="button">
                막힘 처리
              </Button>
            </div>
          </section>
        )}
        {/*
          * 선행업무 줄 (SPEC-001 U-13 · U-7) — **비면 줄이 없다.**
          *
          * 끝나지 않은 선행을 **눈에 띄게** 낸다: 그것이 시작을 막는 이유이기 때문이다.
          * 볼 수 없는 선행은 **제목 없이 건수만** — 자료 구획과 다르다(자료는 건수도 내지 않는다).
          * 여기서는 막는 이유를 숨기면 사람이 다음 걸음을 고를 수 없다.
          */}
        {(task.predecessors ?? []).length > 0 && (
          <section aria-label="선행업무" className="drawer-section">
            <h4>선행업무</h4>
            {visiblePredecessors.length > 0 && (
              <ul className="material-list">
                {visiblePredecessors.map((row) => {
                  const unfinished = row.state !== "done" && row.state !== "cancelled";
                  return (
                    <li key={row.task_id}>
                      {onOpenTask ? (
                        <Button aria-label={`${row.title} 열기`} onClick={() => openTask(row.task_id)} type="button" variant="inline">
                          {row.title}
                        </Button>
                      ) : (
                        <span>{row.title}</span>
                      )}
                      {unfinished ? (
                        <Badge tone="danger">{taskStateLabel[row.state] ?? row.state}</Badge>
                      ) : (
                        <span className="t-meta">{taskStateLabel[row.state] ?? row.state}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            {hiddenPredecessors > 0 && <p className="t-meta">{hiddenPredecessorsText(hiddenPredecessors)}</p>}
            {startBlocked && <p className="danger-text">{blockedText}</p>}
          </section>
        )}
        {parentTask && (
          <section aria-label="상위 업무" className="drawer-section notice">
            <h4>상위 업무</h4>
            <p>
              이 업무는{" "}
              <Button
                aria-label={`${parentTask.title} 열기`}
                variant="inline"
                onClick={() => openTask(parentTask.task_id)}
                type="button"
              >
                {parentTask.title}
              </Button>{" "}
              의 하위 업무입니다. <span className="t-meta">{taskStateLabel[parentTask.state]}</span>
            </p>
          </section>
        )}
        {!readOnly && (
          /*
           * **중심 업무 + 직속 하위 두 단계** (F-1 · V-6 · L-11).
           *
           * 예전에는 상위가 있는 업무에 이 구획을 아예 그리지 않았다 — 「한 겹만 나눈다」는 옛 제한이다.
           * v2 는 **저장 깊이에 제한이 없고**(V-6) 화면만 두 단계다: 어느 업무를 열든 그 직속 하위가
           * 보이고, 더 깊은 것은 그 하위로 들어가 읽는다. 그래서 상위 유무로 가리지 않는다.
           *
           * 셈은 **완결·막힘·취소를 각각** 낸다 — 취소된 하위는 완료를 막지 않지만 목록에는 남는다(V-16).
           */
          <section aria-label="하위 업무" className="drawer-section">
            <div className="section-row">
              <h4>
                하위 업무{" "}
                <span className="checklist-progress" data-done={childProgress.done} data-total={childProgress.total}>
                  {childProgress.done}/{childProgress.total}
                </span>
                {childProgress.blocking > 0 && <small className="t-meta"> · 완료를 막는 하위 {childProgress.blocking}</small>}
                {childProgress.cancelled > 0 && <small className="t-meta"> · 취소 {childProgress.cancelled}</small>}
              </h4>
              {editable && (
                <>
                  <Button variant="text" size="sm" disabled={busy} onClick={() => setNewChild(newChild === null ? "" : null)} type="button">
                    {newChild === null ? "직접 작업 추가" : "추가 취소"}
                  </Button>
                  {/* 하위를 남에게 맡기는 길 — 요청 입구로 간다. `parent_task_id` 는 거기서만 받는다(O-27). */}
                  <Button variant="text" size="sm" disabled={busy} onClick={() => void openSubtaskRequest()} type="button">
                    하위 요청 보내기
                  </Button>
                </>
              )}
            </div>
            {children.length > 0 ? (
              <ul className="material-list">
                {children.map((row) => (
                  <li data-child-task={row.task_id} key={row.task_id}>
                    <Button aria-label={`${row.title} 열기`} variant="inline" onClick={() => openTask(row.task_id)} type="button">
                      {row.title}
                    </Button>
                    <span className="t-meta">
                      {/* 취소된 하위는 «왜» 취소됐는지로 읽힌다 — 「취소됨 — 요청 거절」 (F-3 · V-11). */}
                      {isChildCancelled(row)
                        ? cancelReasonLabel[String(row.cancel_reason ?? "direct")] ?? taskStateLabel.cancelled
                        : row.derived?.assignment === "awaiting_acceptance"
                          ? derivedAssignmentLabel.awaiting_acceptance
                          : row.derived?.approval === "awaiting_review"
                            ? derivedApprovalLabel.awaiting_review
                            : taskStateLabel[row.state] ?? row.state}
                      {row.due_date ? ` · ${formatDate(row.due_date)}` : ""}
                      {row.assignee ? ` · ${personName(row.assignee.display_name)}` : ""}
                    </span>
                    {!isChildCancelled(row) && !isChildSettled(row) && <Badge tone="outline">미완결</Badge>}
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
                <Button size="sm" disabled={busy || !newChild.trim()} onClick={() => void addSubtask()} type="button">
                  만들기
                </Button>
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
                <Button variant="text" size="sm" disabled={busy} onClick={() => void openReferencePicker()} type="button">
                  {refDraft === null ? "업무 연결" : "연결 취소"}
                </Button>
              )}
            </div>
            {references.length > 0 ? (
              <ul className="material-list">
                {references.map((reference) => (
                  <li key={reference.reference_id}>
                    {reference.task ? (
                      <Button
                        aria-label={`${reference.task.title} 열기`}
                        variant="inline"
                        onClick={() => openTask(reference.task!.task_id)}
                        type="button"
                      >
                        {reference.task.title}
                      </Button>
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
                      <Button variant="text" size="sm" aria-label={`${reference.task?.title ?? "볼 수 없는 업무"} 연결 해제`} onClick={() => void releaseReference(reference)}
                        type="button"
                      >
                        연결 해제
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="t-meta">연결된 업무가 없습니다. 이어지는 업무라면 이전 업무를 연결해 두세요.</p>
            )}
            {editable && refDraft !== null && (
              <div className="form-stack link-draft">
                <div className="scax-field">
                  <span>연결할 업무</span>
                  <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
                    id={`task-reference-${task.task_id}`}
                    label="연결할 업무"
                    onChange={setRefDraft}
                    options={(refChoices ?? []).map((choice) => ({ value: choice.task_id, label: choice.title }))}
                    placeholder="업무 고르기"
                    value={refDraft}
                  />
                </div>
                <div className="row-actions">
                  <Button variant="solid" tone="primary" size="sm" disabled={busy || !refDraft} onClick={() => void connectReference()} type="button">
                    연결
                  </Button>
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
            <Button
              variant="ai"
              onClick={() => {
                // Hand the task to the AX panel and close this drawer; the drawer would otherwise cover the panel.
                if (!canLeave()) return;
                onAskAx(task);
                onClose();
              }}
              type="button"
            >
              <Icon name="sparkle" size={14} /> AX에게 이 업무 묻기
            </Button>
          </section>
        )}
      </Shell>
      {/*
        * 완료 보고 (4차 발주 5 · 5차 발주) — **입력 모달 한 벌이다.**
        *
        * 상세에서 열든 목록 행에서 열든 같은 부품이 선다. 묻는 것(결과 요약 · 함께 낼 산출물)도,
        * 막는 것(끝나지 않은 하위 · OQ-203)도, 보내는 명령도 한 자리라 두 입구가 갈릴 수 없다.
        */}
      {reporting && (
        <CompletionReportModal
          busy={busy}
          initialSummary={delivery?.summary ?? ""}
          materials={materials ?? []}
          onClose={() => setReporting(false)}
          onError={onError}
          onNotice={onNotice}
          onSubmitted={async (updated) => {
            setReporting(false);
            setDelivery(updated.delivery ?? null);
            moved(updated.version);
            await settleVersion();
          }}
          requesterName={requesterName}
          subtasks={children}
          task={current}
        />
      )}
      {confirmUnfinished && (
        <ConfirmModal
          cancelLabel="돌아가기"
          closeLabel="닫기"
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
        /*
         * **직접 취소는 사유가 필수다** (SPEC-003 §4 Validation · SPEC-001 · `WORK_REASON_REQUIRED`).
         *
         * 지금까지 이 자리는 확인 모달이라 **누르면 곧바로 취소**됐고 사유가 서버로 가지 않았다.
         * 확정된 계약이 빠져 있던 자리라 채운다 — 새 정책이 아니고, **권한·허용 대상은 그대로**다.
         *
         * 막힘 사유와 같은 부품을 쓴다(`ReasonPrompt`) — 입력·취소·오류가 한 모양으로 읽힌다.
         * 서버가 거절하면 **쓴 문장을 지우지 않는다**: `false` 를 돌려주어 이 자리를 열어 둔다.
         */
        <ReasonPrompt
          busy={busy}
          confirmLabel="업무 취소"
          danger
          description={`'${task.title}' 업무를 취소합니다. 취소한 업무는 다시 진행할 수 없고 사유와 함께 기록만 남습니다.`}
          fieldLabel="취소 사유"
          heading="취소 사유를 남겨 주세요"
          label="취소 사유"
          onClose={() => setConfirmCancel(false)}
          onSubmit={async (reason) => {
            const settled = await onTransition(current, "cancel", reason);
            // 부르는 쪽이 성공 여부를 말해 주지 않는 화면에서는 지금까지대로 닫는다.
            if (settled === false) return false;
            setConfirmCancel(false);
            return true;
          }}
          placeholder="왜 취소하는지 적어 주세요"
        />
      )}
      {prompt?.kind === "propose" && (
        <ReasonPrompt
          busy={busy}
          confirmLabel="제안 보내기"
          description={`'${task.title}' 업무의 취소를 제안합니다. 담당자가 동의해야 취소되고, 동의 전에는 아무것도 바뀌지 않습니다.`}
          fieldLabel="제안 사유"
          heading="취소를 제안합니다"
          label="취소 제안"
          onClose={() => setPrompt(null)}
          onSubmit={(reason) => {
            setPrompt(null);
            void proposeChange("cancellation", reason);
          }}
        />
      )}
      {prompt?.kind === "terms" && (
        <TermsChangePrompt
          busy={busy}
          onClose={() => setPrompt(null)}
          onSubmit={(payload, reason) => {
            setPrompt(null);
            void proposeChange("terms_change", reason, payload);
          }}
          task={task}
        />
      )}
      {prompt?.kind === "respond" && (
        <ReasonPrompt
          busy={busy}
          confirmLabel={prompt.agree ? "동의" : "동의하지 않음"}
          danger={!prompt.agree}
          description={
            prompt.agree
              ? "동의하면 제안한 내용이 그 자리에서 반영됩니다."
              : "동의하지 않으면 업무와 조건이 지금 그대로 남습니다. 제안한 사람에게 사유가 전달됩니다."
          }
          fieldLabel="응답 사유"
          heading={prompt.agree ? "제안에 동의합니다" : "동의하지 않습니다"}
          label={prompt.agree ? "제안 동의" : "제안 거절"}
          onClose={() => setPrompt(null)}
          onSubmit={(reason) => {
            const { proposal, agree } = prompt;
            setPrompt(null);
            void respondProposal(proposal, agree, reason);
          }}
          optional={prompt.agree}
        />
      )}
      {prompt?.kind === "reopen" && (
        <ReasonPrompt
          busy={busy}
          confirmLabel="재개"
          description={`'${task.title}' 업무를 다시 엽니다. 이전 완료 이력·회차·결과는 그대로 남습니다. 완료된 상위가 있으면 상위를 먼저 재개해야 합니다.`}
          fieldLabel="재개 사유 (선택)"
          heading="업무를 재개합니다"
          label="재개 사유"
          onClose={() => setPrompt(null)}
          onSubmit={(reason) => {
            setPrompt(null);
            void reopen(reason);
          }}
          optional
        />
      )}
      {prompt?.kind === "delivery_changes" && (
        /* 보완 요청은 사유가 필수다 — 계약(`request_changes`)이 그렇게 받는다. 서버가 거절하면
           이 자리를 열어 둔 채 쓴 문장을 지키기 위해 `false` 를 돌려준다. */
        <ReasonPrompt
          busy={busy}
          confirmLabel="보완 요청"
          description={`'${task.title}' 결과가 아직 충족되지 않았다고 알립니다. 업무는 진행 중으로 돌아가고 지난 회차는 그대로 남습니다.`}
          fieldLabel="무엇이 더 필요한가"
          heading="보완할 내용을 적어 주세요"
          label="보완 요청 사유"
          onClose={() => setPrompt(null)}
          onSubmit={async (reason) => {
            const accepted = await requestDeliveryChanges(reason);
            if (accepted) setPrompt(null);
            return accepted;
          }}
        />
      )}
      {subtaskRequest && (
        /* 하위를 남에게 맡긴다 — **요청 입구로만** 간다. 상위 연결은 발송 단계부터 실린다(V-9). */
        <CreateWorkModal
          assigneeCandidates={requestCandidates}
          canCreateRequest
          canCreateTask={false}
          initial={{ parentTaskId: task.task_id }}
          onClose={() => setSubtaskRequest(false)}
          onCreated={async (message) => {
            setSubtaskRequest(false);
            onNotice?.(message);
            await settleVersion();
          }}
          onError={onError}
          ownerName={ownerName}
          size="md"
        />
      )}
    </>
  );
}

/** Quick actions used inside table rows: one primary next step plus 막힘 while in progress. */
/**
 * 「막힘」으로 옮기기 전에 사유를 받는 자리 (바퀴 5c 에서 한 벌로 모았다).
 *
 * `transitionDirectTask` 의 `block` 은 사유를 함께 받는다 — 그래서 «고른 즉시 보내는» 길이 없다.
 * 칸반이 드래그로 막힘 칸에 떨어뜨릴 때 쓰던 것이 원본이고, 표의 상태 칸도 같은 문제를 만나서
 * (상태 열은 120px 이라 한 줄 입력칸이 들어갈 자리가 아니다) 같은 것을 쓴다. 마크업은 칸반의 것 그대로다.
 */
/**
 * 사유를 받고 명령을 보내는 한 칸짜리 모달 (WORK-002 Phase 7-C).
 *
 * v2 는 사유를 **필수로** 받는 자리를 여럿 연다 — 요청 거절 · 철회 · 담당 변경과 그 거절 ·
 * 취소 제안과 그 응답 · 재개(선택). 그 자리마다 모달을 새로 그리면 같은 규칙이 여섯 벌로 갈린다.
 * 하나로 두고 **말만 부르는 쪽이 넘긴다**(부품은 말을 모른다 · 바퀴 11).
 *
 * `optional` 은 사유가 «선택» 인 자리(재개)에서만 켠다 — 그때는 빈 채로도 보낼 수 있다.
 */
/**
 * 완료 보고 — **요청 업무를 끝내는 한 벌의 입구다** (4차 발주 5 · 5차 발주).
 *
 * 요청 업무의 직접 완료는 서버가 막는다(`이 업무는 요청자의 확인이 필요합니다`). 끝나는 길은
 * 「완료 보고 → 요청자 확인」 하나뿐이라, **그 보고를 받는 자리도 하나여야 한다.**
 *
 * 5차 발주가 고친 것이 그 자리다: 상세에는 이 모달이 있었는데 **목록 행의 「완료」는 곧바로
 * `complete` 전이를 보내고 있었다.** 요청 업무에서는 그것이 늘 서버 거절이고, 사람은 빨간 글 한 줄을
 * 보고 왜 안 되는지 모른 채 멈춘다. 이제 두 입구가 같은 부품을 연다.
 *
 * **스스로 읽을 줄 안다.** 상세는 이미 들고 있는 자료·하위를 넘기고, 행은 넘기지 않는다 —
 * 넘기지 않은 값만 이 모달이 직접 읽는다(`getTask` · `getTaskMaterials`). 그래서 행에서 열어도
 * 막는 하위와 낼 산출물이 상세에서 연 것과 똑같이 보인다.
 */
export function CompletionReportModal({
  task,
  requesterName,
  busy = false,
  materials: givenMaterials,
  subtasks: givenSubtasks,
  initialSummary = "",
  onClose,
  onSubmitted,
  onNotice,
  onError,
}: {
  task: DirectTask;
  /** 누구의 확인을 기다리게 되는가. 보내기 전에 그것을 말해 둔다. */
  requesterName: string;
  busy?: boolean;
  /** 이 업무의 자료. **넘기지 않으면 직접 읽는다** — `null` 은 「아직 안 읽었다」가 아니라 「없다」다. */
  materials?: TaskMaterial[];
  /** 직속 하위. 넘기지 않으면 상세 읽기로 직접 가져온다 (OQ-203 검사가 이 값을 쓴다). */
  subtasks?: TaskChild[];
  initialSummary?: string;
  onClose: () => void;
  /** 보고가 받아들여졌다. 닫는 것도, 화면을 정리하는 것도 부르는 쪽의 몫이다. */
  onSubmitted: (updated: DirectTask) => void | Promise<void>;
  onNotice?: (message: string) => void;
  onError: (message: string | null) => void;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [outputs, setOutputs] = useState<string[]>([]);
  /** 보내는 중 — 두 번째 누름이 두 번째 보고가 되지 않게 막는다. */
  const [pending, setPending] = useState(false);
  const [ownMaterials, setOwnMaterials] = useState<TaskMaterial[] | null>(null);
  const [ownDetail, setOwnDetail] = useState<DirectTask | null>(null);

  useEffect(() => {
    if (givenMaterials !== undefined) return;
    let cancelled = false;
    void getTaskMaterials(task.task_id)
      .then((items) => {
        if (!cancelled) setOwnMaterials(items);
      })
      .catch(() => {
        // 자료를 못 읽었다고 보고를 막지 않는다 — 산출물은 «함께 낼 수 있는 것» 이지 필수가 아니다.
        if (!cancelled) setOwnMaterials([]);
      });
    return () => {
      cancelled = true;
    };
  }, [givenMaterials, task.task_id]);

  useEffect(() => {
    if (givenSubtasks !== undefined) return;
    let cancelled = false;
    void getTask(task.task_id)
      .then((detail) => {
        if (!cancelled) setOwnDetail(detail);
      })
      .catch(() => {
        if (!cancelled) setOwnDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [givenSubtasks, task.task_id]);

  /* 회차는 **방금 읽은 값이 먼저다.** 목록 투영이 든 회차가 한 걸음 뒤일 수 있고, 그대로 보내면 409 다. */
  const current = ownDetail && ownDetail.version >= task.version ? ownDetail : task;
  const materials = givenMaterials ?? ownMaterials ?? [];
  const blocking = blockingChildrenOf({ ...current, children: givenSubtasks ?? current.children ?? [] });
  const outputMaterials = materials
    .filter((item) => item.kind === "output")
    .filter((item, index, rows) => rows.findIndex((row) => row.material_id === item.material_id) === index);
  const blocked = busy || pending || !summary.trim() || blocking.length > 0;

  const submit = async () => {
    const clean = summary.trim();
    if (!clean) {
      onError("무엇을 어디까지 했는지 적어 주세요.");
      return;
    }
    /* OQ-203 — 끝나지 않은 하위가 있으면 **보고 제출도** 막는다. 요청 업무는 직접 완료가 애초에
       막혀 있어서 하위 검사가 닿지 않았고, 그래서 하위가 남은 채로 보고가 올라갔다. */
    if (blocking.length > 0) {
      onError(`끝나지 않은 하위 업무가 있습니다: ${blocking.map((child) => child.title).join(", ")}`);
      return;
    }
    onError(null);
    setPending(true);
    try {
      const updated = await submitTaskCompletion(current.task_id, current.version, { summary: clean, output_material_ids: outputs });
      onNotice?.(`완료 보고를 보냈습니다. ${requesterName}의 확인을 기다립니다.`);
      await onSubmitted(updated);
    } catch (error) {
      // 서버가 거절하면 **쓴 문장을 지우지 않는다** — 이 자리는 열린 채로 남는다.
      onError(error instanceof Error ? error.message : "완료 보고를 보내지 못했습니다.");
    } finally {
      setPending(false);
    }
  };

  return (
    <Modal
      closeLabel="완료 보고 닫기"
      footer={
        <>
          <Button disabled={pending} onClick={onClose} type="button" variant="text">
            취소
          </Button>
          <Button disabled={blocked} onClick={() => void submit()} tone="primary" type="button" variant="solid">
            보고 보내기
          </Button>
        </>
      }
      kicker="완료 보고"
      label="완료 보고"
      onClose={onClose}
      size="md"
      title={current.title}
    >
      {blocking.length > 0 && (
        <section aria-label="보고를 막는 하위" className="drawer-section notice danger">
          <h4>아직 끝나지 않은 하위가 있습니다</h4>
          <ul className="material-list">
            {blocking.map((child) => (
              <li key={child.task_id}>
                {child.title} <span className="t-meta">{blockingChildReasonLabel[child.why] ?? child.why}</span>
              </li>
            ))}
          </ul>
          <p className="t-meta">하위가 먼저 끝나야 완료 보고를 보낼 수 있습니다. 취소된 하위는 세지 않습니다.</p>
        </section>
      )}
      <div className="scax-field">
        <label className="scax-field__label" htmlFor={`delivery-summary-${current.task_id}`}>
          결과 요약
        </label>
        <textarea
          autoFocus
          id={`delivery-summary-${current.task_id}`}
          onChange={(event) => setSummary(event.target.value)}
          placeholder="무엇을 어디까지 했는지, 요청한 내용을 어떻게 충족했는지 적어 주세요."
          rows={5}
          value={summary}
        />
        <FieldMessage help={`보낸 뒤에는 ${requesterName}의 확인을 기다립니다. 확인이 끝나야 최종 완료입니다.`} />
      </div>
      {outputMaterials.length > 0 && (
        <fieldset className="scax-field cc-picker">
          <legend>보고에 담을 산출물</legend>
          <ChipRow>
            {outputMaterials.map((item) => (
              <ChipToggle
                checked={outputs.includes(item.material_id)}
                key={item.material_id}
                onChange={(next) =>
                  setOutputs((current) => (next ? [...current, item.material_id] : current.filter((id) => id !== item.material_id)))
                }
              >
                {item.name}
              </ChipToggle>
            ))}
          </ChipRow>
          <p className="t-meta">고른 산출물은 보고 시점의 무결성 값으로 고정되어 함께 남습니다.</p>
        </fieldset>
      )}
    </Modal>
  );
}

export function ReasonPrompt({
  label,
  heading,
  description,
  fieldLabel,
  placeholder,
  confirmLabel,
  danger,
  optional,
  busy,
  onSubmit,
  onClose,
}: {
  label: string;
  heading: string;
  description?: React.ReactNode;
  fieldLabel: string;
  placeholder?: string;
  confirmLabel: string;
  danger?: boolean;
  optional?: boolean;
  busy?: boolean;
  /**
   * 사유를 들고 명령을 보낸다.
   *
   * **`false` 를 돌려주면 이 자리를 닫지 않는다** — 서버가 거절했을 때 사람이 쓴 문장을 잃지 않기
   * 위해서다. 지금까지처럼 아무것도 돌려주지 않으면 **부르는 쪽이 닫는다**(기존 호출부 그대로).
   */
  onSubmit: (reason: string) => void | Promise<boolean | void>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  /** 보내는 중 — **두 번째 누름이 두 번째 명령이 되지 않게** 막는 자리다. */
  const [pending, setPending] = useState(false);
  const inputId = `reason-${label.replace(/\s+/g, "-")}`;
  const blocked = (!reason.trim() && !optional) || Boolean(busy) || pending;
  const submit = async () => {
    if (blocked) return;
    setPending(true);
    try {
      await onSubmit(reason.trim());
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section aria-label={label} aria-modal="true" className="modal" role="dialog">
        <header className="modal-head">
          <h3>{heading}</h3>
        </header>
        <div className="modal-body">
          {description && <p>{description}</p>}
          <div className="scax-field">
            <label className="scax-field__label" htmlFor={inputId}>{fieldLabel}</label>
            <input
              autoFocus
              id={inputId}
              onChange={(event) => setReason(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
                if (event.key === "Escape") onClose();
              }}
              placeholder={placeholder}
              value={reason}
            />
          </div>
        </div>
        <footer className="modal-foot">
          <Button variant="text" disabled={pending} onClick={onClose} type="button">
            돌아가기
          </Button>
          <Button
            variant="solid"
            tone={danger ? "danger" : "primary"}
            disabled={blocked}
            onClick={() => void submit()}
            type="button"
          >
            {confirmLabel}
          </Button>
        </footer>
      </section>
    </div>
  );
}

/**
 * 조건 변경 제안 — **무엇을 바꾸자는 것인지 실제로 적는 자리다** (V-20 · SPEC-003 §4 `payload`).
 *
 * 사유만 받는 제안은 답할 수 없다: 담당자는 「동의」를 눌렀을 때 무엇이 바뀌는지 모른 채 눌러야 한다.
 * 그래서 이 모달은 **바꿀 값 자체**(기한 · 요청 내용)를 받아 `payload` 로 싣고, 하나도 안 바꾸면
 * 보내지 않는다. 계약이 받는 칸만 묻는다 — `labels.proposalFieldLabel` 이 그 목록이다.
 */
export function TermsChangePrompt({
  task,
  busy,
  onSubmit,
  onClose,
}: {
  task: DirectTask;
  busy?: boolean;
  onSubmit: (payload: Record<string, unknown>, reason: string) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [dueDate, setDueDate] = useState(task.due_date ?? "");
  const [description, setDescription] = useState(task.description ?? "");
  const [reason, setReason] = useState("");
  /**
   * 서버가 **적용하는 칸은 셋뿐이다** — `title` · `description` · `due_date` (`due_date` 는 ISO 문자열
   * 또는 `null`). 고친 칸만 싣고, **빈 `payload` 는 422** 라서 하나도 안 고쳤으면 아예 보내지 않는다.
   */
  const payload: Record<string, unknown> = {};
  if (title.trim() && title.trim() !== task.title) payload.title = title.trim();
  if (dueDate !== (task.due_date ?? "")) payload.due_date = dueDate || null;
  if (description.trim() !== (task.description ?? "")) payload.description = description.trim();
  const changed = Object.keys(payload).length > 0;
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section aria-label="조건 변경 제안" aria-modal="true" className="modal" role="dialog">
        <header className="modal-head">
          <h3>조건 변경을 제안합니다</h3>
        </header>
        <div className="modal-body">
          <p>담당자가 동의해야 반영됩니다. 동의 전에는 지금 조건이 그대로입니다.</p>
          <div className="scax-field">
            <label className="scax-field__label" htmlFor={`terms-title-${task.task_id}`}>{proposalFieldLabel.title}</label>
            <input id={`terms-title-${task.task_id}`} onChange={(event) => setTitle(event.target.value)} value={title} />
          </div>
          <div className="scax-field">
            <DateField
              formatMonth={formatMonthLong}
              labels={datePickerLabel}
              today={seoulToday()}
              weekdayNames={weekdayNames}
              id={`terms-due-${task.task_id}`}
              label={proposalFieldLabel.due_date}
              onChange={setDueDate}
              value={dueDate}
            />
          </div>
          <div className="scax-field">
            <label className="scax-field__label" htmlFor={`terms-description-${task.task_id}`}>{proposalFieldLabel.description}</label>
            <textarea
              id={`terms-description-${task.task_id}`}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              value={description}
            />
          </div>
          <div className="scax-field">
            <label className="scax-field__label" htmlFor={`terms-reason-${task.task_id}`}>제안 사유</label>
            <input
              id={`terms-reason-${task.task_id}`}
              onChange={(event) => setReason(event.target.value)}
              placeholder="왜 바꾸어야 하는지 적어 주세요"
              value={reason}
            />
          </div>
          {!changed && <p className="t-meta">바꿀 값을 하나 이상 고쳐야 제안할 수 있습니다.</p>}
        </div>
        <footer className="modal-foot">
          <Button variant="text" onClick={onClose} type="button">
            돌아가기
          </Button>
          <Button variant="solid" tone="primary" disabled={!changed || busy} onClick={() => onSubmit(payload, reason.trim())} type="button">
            제안 보내기
          </Button>
        </footer>
      </section>
    </div>
  );
}

export function BlockReasonPrompt({
  task,
  busy,
  onSubmit,
  onClose,
}: {
  task: DirectTask;
  busy: boolean;
  onSubmit: (reason: string) => void;
  onClose: () => void;
}) {
  return (
    <ReasonPrompt
      busy={busy}
      confirmLabel="막힘 처리"
      description={`'${task.title}' 업무를 막힘으로 옮깁니다. 사유는 팀장 화면과 일일보고 근거에 남습니다.`}
      fieldLabel="막힘 사유"
      heading="막힘 사유를 남겨 주세요"
      label="막힘 사유"
      onClose={onClose}
      onSubmit={onSubmit}
      placeholder="무엇 때문에 막혔는지 적어 주세요"
    />
  );
}

export function TaskQuickActions({
  task,
  busy,
  onTransition,
  onChanged,
  onNotice,
  onError,
}: {
  task: DirectTask;
  busy: boolean;
  /** 전이를 보낸다. 돌려주는 값(받아들여졌나)은 이 자리가 쓰지 않는다 — 사유 자리만 그것을 읽는다. */
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<boolean | void>;
  /** 완료 보고가 올라간 뒤 목록을 다시 읽는 자리. 없으면 이 행은 보고만 보내고 제자리에 남는다. */
  onChanged?: () => Promise<void> | void;
  onNotice?: (message: string) => void;
  onError?: (message: string | null) => void;
}) {
  const [isBlocking, setIsBlocking] = useState(false);
  /**
   * 행에서 여는 완료 보고 (5차 발주).
   *
   * **요청 업무의 「완료」는 전이가 아니다.** 여기서 `complete` 를 보내면 서버가 늘 거절한다
   * (`이 업무는 요청자의 확인이 필요합니다`) — 상세의 「완료 보고」와 행의 「완료」가 서로 다른 일을
   * 하고 있던 자리다. 이제 둘이 같은 모달을 연다.
   */
  const [reporting, setReporting] = useState(false);
  const requested = isRequestTask(task);
  const requesterName = task.origin?.actor ? personName(task.origin.actor.display_name) : "요청자";
  /* 행에서도 **누르기 전에** 막는다 — 상세와 목록이 같은 사실을 다르게 말하지 않는다 (U-14). */
  const startBlocked = startBlockedByPredecessors(task);
  const blockedText = predecessorsUnfinishedText(blockingPredecessorsOf(task).map((row) => row.title));
  return (
    <>
      {/* 바퀴 5c: 어느 단추가 서는지는 이제 `allowedTaskTransitions` 한 자리가 정한다 — 라벨과 꼴만 여기 남는다 */}
      {canTransition(task, "start") && (
        <Button variant="solid" tone="primary" size="sm" disabled={busy || startBlocked} onClick={() => void onTransition(task, "start")} type="button">
          시작
        </Button>
      )}
      {canTransition(task, "block") && (
        <Button size="sm" disabled={busy || isBlocking} onClick={() => setIsBlocking(true)} type="button">
          막힘
        </Button>
      )}
      {canTransition(task, "complete") && (
        /* 일반 업무는 지금까지대로 **바로 완료**다. 요청 업무만 보고 모달로 간다 — 라벨도 그것을 말한다. */
        <Button
          variant="solid"
          tone="primary"
          size="sm"
          disabled={busy || reporting || startBlocked}
          onClick={() => (requested ? setReporting(true) : void onTransition(task, "complete"))}
          type="button"
        >
          {requested ? "완료 보고" : "완료"}
        </Button>
      )}
      {/* 「재개」는 둘 다 resume 이지만 부르는 말이 다르다 — 막힌 것을 푸는 것과 끝낸 것을 되돌리는 것이다 */}
      {canTransition(task, "resume") && task.state === "blocked" && (
        <Button variant="outlined" tone="primary" size="sm" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
          재개
        </Button>
      )}
      {canTransition(task, "resume") && task.state === "done" && (
        <Button variant="text" size="sm" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
          다시 진행
        </Button>
      )}
      {/*
        * 4차 발주 4: 행의 사유 입력은 **200px 액션 칸 안에** 있었다 — 그 칸이 격자로 잡혀 있어서
        * 입력칸이 몇 글자만 보였다. 표 상태 칸이 이미 쓰고 있던 `BlockReasonPrompt` 로 통일한다.
        * 사유가 비면 「막힘 처리」가 비활성인 것은 그 부품이 이미 하는 일이다.
        */}
      {startBlocked && <small className="t-meta scax-blocked-note">{blockedText}</small>}
      {isBlocking && (
        <BlockReasonPrompt
          busy={busy}
          onClose={() => setIsBlocking(false)}
          onSubmit={(reason) => {
            void onTransition(task, "block", reason);
            setIsBlocking(false);
          }}
          task={task}
        />
      )}
      {reporting && (
        /* 상세가 여는 것과 **같은 부품**이다 — 묻는 것도, 막는 것도(OQ-203), 보내는 명령도 한 자리다.
           자료와 하위는 행이 들고 있지 않으므로 이 모달이 직접 읽는다. */
        <CompletionReportModal
          busy={busy}
          onClose={() => setReporting(false)}
          onError={onError ?? (() => {})}
          onNotice={onNotice}
          onSubmitted={async () => {
            setReporting(false);
            await onChanged?.();
          }}
          requesterName={requesterName}
          task={task}
        />
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
  readOnly = false,
  onChanged,
  onError,
  onNotice,
  onClose,
  onBack,
  backLabel,
}: {
  request: WorkRequest;
  /** Open the Task this request produced, through the server's own authorized read. */
  onOpenDerivedTask?: (taskId: string) => void;
  personaId: string;
  personas: Persona[];
  canDecide: boolean;
  /** 참고 수신함에서는 상태·결과·이력만 읽는다. */
  readOnly?: boolean;
  onChanged: () => Promise<void> | void;
  onError: (message: string | null) => void;
  onNotice?: (message: string) => void;
  onClose: () => void;
  /**
   * 넘기면 머리 왼쪽에 「뒤로」가 선다 — 이 겹을 «닫지 않고» 내용만 이전 상세로 되돌린다 (4차 발주 3).
   * 업무 상세에서 출처(요청)를 따라 들어온 자리가 그것이다.
   */
  onBack?: () => void;
  backLabel?: string;
}) {
  const today = seoulToday();
  const [mode, setMode] = useState<"negotiate" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [isWorking, setIsWorking] = useState(false);
  const [timeline, setTimeline] = useState<RequestTimeline | null>(null);
  const [comment, setComment] = useState("");
  const [commentFile, setCommentFile] = useState<File | null>(null);
  const [isUploadingEvidence, setIsUploadingEvidence] = useState(false);
  useBrowserOperationGuard(isUploadingEvidence || (isWorking && commentFile !== null));
  const close = () => {
    if (isUploadingEvidence || isWorking) { onError('저장과 업로드가 끝난 뒤 닫을 수 있습니다.'); return; }
    onClose();
  };
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
  const canAdoptEvidence = !readOnly && (isAssignee || isRequester) && request.state === "pending";
  const assigneeName = isAssignee ? "나" : displayNameOf(personas, request.assignee_id, "담당자");
  const isOpen = request.state === "pending" || request.state === "negotiating";
  const decidable = !readOnly && canDecide && isAssignee && isOpen;
  const canResubmit = !readOnly && isRequester && request.state === "negotiating";
  // Improving one's own request needs nobody's permission, but only while it is still the assignee's to judge.
  const canAmend = !readOnly && isRequester && request.state === "pending";
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
    <Modal
          closeLabel="상세 닫기"
      onBack={onBack}
      backLabel={backLabel ?? "이전 상세로 돌아가기"}
      footer={
        decidable ? (
          <>
            <Button variant="text" disabled={isWorking} onClick={() => setMode("reject")} type="button">
              거절
            </Button>
            <span className="scax-drawer__spacer" />
            <Button disabled={isWorking} onClick={() => setMode("negotiate")} type="button">
              조정 요청
            </Button>
            <Button variant="solid" tone="primary" disabled={isWorking} onClick={() =>
                void run(
                  () => decideWorkRequest(request.request_id, "accept", request.version),
                  `'${request.title}' 요청을 수락했습니다. 내 업무에 표시되고 내가 담당자로 지정되었습니다.`,
                  "요청을 수락하지 못했습니다.",
                )
              }
              type="button"
            >
              수락
            </Button>
          </>
        ) : canAmend ? (
          <>
            <Button variant="text" onClick={close} type="button">
              닫기
            </Button>
            <span className="scax-drawer__spacer" />
            {revision ? (
              <Button variant="solid" tone="primary" disabled={isWorking} onClick={submitAmendment} type="button">
                수정 제출
              </Button>
            ) : (
              <Button variant="solid" tone="primary" onClick={() => setRevision({ title: request.title, description: request.description ?? "", due_date: request.due_date ?? "" })}
                type="button"
              >
                요청 수정
              </Button>
            )}
          </>
        ) : canResubmit ? (
          <>
            <Button variant="text" onClick={close} type="button">
              닫기
            </Button>
            <span className="scax-drawer__spacer" />
            {revision ? (
              <Button variant="solid" tone="primary" disabled={isWorking} onClick={submitRevision} type="button">
                재상신
              </Button>
            ) : (
              <Button variant="solid" tone="primary" onClick={() => setRevision({ title: request.title, description: request.description ?? "", due_date: request.due_date ?? "" })}
                type="button"
              >
                내용 고쳐 재상신
              </Button>
            )}
          </>
        ) : (
          <Button onClick={close} type="button">
            닫기
          </Button>
        )
      }
      headerExtra={
        <ChipRow>
          <StatusText label={workRequestStateLabel[request.state]} state={request.state} />
          {request.submission_version && request.submission_version > 1 && <Badge tone="accent">재상신 {request.submission_version}회차</Badge>}
          <Badge tone="outline">v{request.version}</Badge>
        </ChipRow>
      }
      label="업무 요청 상세"
      onClose={close}
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
                <Button variant="inline" onClick={() => onOpenDerivedTask(request.task_id!)} type="button">
                  파생 업무 보기
                </Button>
              ) : (
                isOpen ? "생성됨 · 담당 수락 대기" : "생성됨"
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
      {readOnly ? <p className="t-meta">참고로 받은 요청입니다. 상태와 처리 결과, 이력을 확인할 수 있습니다.</p> : isCc && <p className="t-meta">참조자로 받은 요청입니다. 읽고 논의할 수 있지만 판단은 {assigneeName}가 합니다.</p>}
      {request.description && !revision && (
        <section className="drawer-section">
          <h4>요청 내용</h4>
          <p className="prewrap">{request.description}</p>
        </section>
      )}
      {!revision && request.checklist && request.checklist.length > 0 && (
        <section className="drawer-section">
          <h4>체크리스트</h4>
          <ul aria-label="요청 체크리스트" className="checklist">
            {request.checklist.map((step, index) => (
              <li className="scax-checklist__row" key={`${step}-${index}`}>
                <Checkbox checked={false} disabled onChange={() => undefined}>
                  {step}
                </Checkbox>
              </li>
            ))}
          </ul>
        </section>
      )}
      {!revision && request.references && request.references.length > 0 && (
        <section className="drawer-section">
          <h4>
            참고 업무 <span className="t-meta">· 요청과 함께 전달된 이전 업무입니다</span>
          </h4>
          <ul aria-label="요청 참고 업무" className="material-list">
            {request.references.map((reference) => (
              <li key={reference.reference_id}>
                {reference.task ? (
                  <Button
                    aria-label={`${reference.task.title} 열기`}
                    variant="inline"
                    onClick={() => onOpenDerivedTask?.(reference.task!.task_id)}
                    type="button"
                  >
                    {reference.task.title}
                  </Button>
                ) : (
                  <span className="t-meta">볼 수 없는 업무</span>
                )}
                <span className="t-meta">
                  {reference.task ? taskStateLabel[reference.task.state] : "권한 없음"}
                  {reference.task?.due_date ? ` · ${formatDate(reference.task.due_date)}` : ""}
                  {reference.task?.assignee ? ` · ${personName(reference.task.assignee.display_name)}` : ""}
                </span>
              </li>
            ))}
          </ul>
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
            <div className="scax-field">
              <label className="scax-field__label" htmlFor="revision-title">요청할 업무</label>
              <input id="revision-title" onChange={(event) => setRevision({ ...revision, title: event.target.value })} value={revision.title} />
            </div>
            <div className="scax-field">
              <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames} id="revision-due" label="희망 기한" onChange={(next) => setRevision({ ...revision, due_date: next })} value={revision.due_date} />
            </div>
            <div className="scax-field">
              <label className="scax-field__label" htmlFor="revision-description">요청 내용</label>
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
          {/*
            * **수락은 «생성» 이 아니라 «담당 활성화» 다** (3차 발주 2).
            *
            * 요청을 보내는 순간 서버는 Task 를 이미 만든다 — 수락 전에는 담당이 비어 있을 뿐이고
            * (`derived.assignment === "awaiting_acceptance"`), 거절하면 그 Task 가 **취소된다**
            * (「취소됨 — 요청 거절」). 그래서 「수락하면 생성됩니다 / 거절하면 만들어지지 않습니다」는
            * 화면만 참인 말이었다. 계약이 하는 일을 그대로 적는다 — 새 동작을 만들지 않았다.
            */}
          <blockquote className="effect-note">
            {request.state === "accepted" && `“${request.title}”가 ${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 표시되고 ${assigneeName === "나" ? "내가" : `${assigneeName}가`} 담당자로 지정되었습니다.`}
            {/* 판단이 없었던 신규 경로다 — 「수락됨」과 같은 말로 적지 않는다 (WORK-001 Phase 4). */}
            {request.state === "assigned" && `“${request.title}”가 수락 없이 바로 ${assigneeName === "나" ? "내" : `${assigneeName}의`} 업무에 표시되고 ${assigneeName === "나" ? "내가" : `${assigneeName}가`} 담당자로 지정되었습니다.`}
            {request.state === "rejected" && "요청이 거절되어 업무는 취소되었습니다."}
            {isOpen && "수락하면 이 업무가 내 업무에 표시되고, 내가 담당자로 지정됩니다. 거절하면 업무는 취소됩니다."}
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
            <Button
              disabled={isWorking}
              onClick={submitNote}
              size="sm"
              tone={mode === "reject" ? "danger" : "primary"}
              type="button"
              variant="solid"
            >
              {mode === "reject" ? "거절 확정" : "조건 보내기"}
            </Button>
            <Button variant="text" size="sm" onClick={() => setMode(null)} type="button">
              입력 취소
            </Button>
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
              <Button size="sm" disabled={isUploadingEvidence || isWorking} onClick={() => evidenceInput.current?.click()} type="button">
                {isUploadingEvidence ? "올리는 중…" : isAssignee ? "판단 근거 추가" : "보조 자료 추가"}
              </Button>
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
                          <Icon name="paperclip" size={14} /> {attachment.name}
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
        {!readOnly && <div className="inline-reason" style={{ padding: "8px 0 0" }}>
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
          <Button
            aria-label={commentFile ? `첨부: ${commentFile.name}` : "파일 첨부"}
            className={commentFile ? "on" : undefined}
            onClick={() => commentFileInput.current?.click()}
            size="sm"
            title={commentFile ? commentFile.name : "파일 첨부"}
            type="button"
            variant="text"
          >
            <Icon name="paperclip" size={14} />
            {commentFile ? ` ${commentFile.name.length > 14 ? `${commentFile.name.slice(0, 12)}…` : commentFile.name}` : ""}
          </Button>
          <Button size="sm" disabled={isWorking || !comment.trim()} onClick={() => void submitComment()} type="button">
            남기기
          </Button>
        </div>}
      </section>
    </Modal>
  );
}

export function conditionText(conditions: Record<string, unknown> | null): string | null {
  if (!conditions) return null;
  if (typeof conditions.note === "string" && conditions.note.trim()) return conditions.note;
  const entries = Object.entries(conditions).filter(([, value]) => value !== null && value !== "");
  if (entries.length === 0) return null;
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(", ");
}

/* ---------------------------------------------------------------- create (modal) */

const noProjectCandidates: Project[] = [];

/** 시안이 내용 칸에 적어 둔 길이 — **상한이 아니라 눈금이다**(계약에 길이 제한이 없다). */
const COMPOSER_HINT_LENGTH = 200;

/**
 * 생성 모달의 왼쪽 세로 탭 (최종 발주 2).
 *
 * **필수 하나와 선택 셋이다.** 제목 없이는 아무것도 만들 수 없으므로 「기본 정보」만 필수이고,
 * 나머지 셋은 지금 몰라도 나중에 업무 상세에서 채울 수 있는 것들이다. 그 사실이 목록의 두 무리로
 * 그대로 읽히게 두 tablist 로 세운다 — 한 무리에 머리글을 섞으면 tablist 의 자식 규약이 깨진다.
 */
type CreateTab = "basic" | "checklist" | "links" | "materials";
const REQUIRED_TABS: ReadonlyArray<CreateTab> = ["basic"];
/**
 * 선택 탭 — **자료는 두 갈래 모두 선다** (최종 프레임 확정).
 *
 * 두 갈래가 같은 탭 한 벌을 쓰는 것은 **디자인 결정**이다: 무엇을 만들든 「기본 정보 · 체크리스트 ·
 * 업무 연결 · 자료」 넷이 같은 자리에 있어야 갈래를 바꿔도 읽던 틀이 흔들리지 않는다.
 *
 * **두 갈래 모두 실제로 저장된다** (WORK-003) — 내 업무는 생성 직후 `task_id` 로, 요청은 발송
 * 직후 `request_id` 로 붙는다. 같은 두 단계이고 자리만 다르다.
 */
const OPTIONAL_TABS: ReadonlyArray<CreateTab> = ["checklist", "links", "materials"];

/** 만들기 창이 들고 있는 링크 자료 한 줄. 파일과 달리 바이트를 들지 않는다 — 주소와 사람이 읽는 이름뿐이다. */
type MaterialLinkDraft = { url: string; label: string };

/**
 * **자료가 붙을 자리** — 업무이거나 요청이다 (WORK-003).
 *
 * 두 갈래가 **같은 두 단계**를 지난다: 만들고 → 돌아온 식별자로 붙인다. 다른 것은 입구 한 쌍뿐이라
 * (`/api/tasks/{id}/materials…` · `/api/work-requests/{id}/materials…`) 여기서 그 하나만 가른다.
 * 생성 payload 에 자료 축을 만들지 않고(`material_ids`·`attachments`·`material_draft_ids` 같은
 * 이름을 지어내지 않는다), 댓글 첨부·판단 근거로 우회하지도 않는다 — 뜻이 다른 자리다.
 */
type MaterialHost = { kind: "task"; id: string } | { kind: "request"; id: string };

/**
 * **회의 승격만은 아직 자료를 싣지 못한다** (BE API gap · 이 분기에서 backend 는 건드리지 않는다).
 *
 * 승격 어댑터(`onSubmitRequest`)가 돌려주는 것은 사람에게 보일 **문장 하나**뿐이라, 붙일 자리를
 * 가리키는 `request_id` 가 화면에 오지 않는다. 없는 식별자를 추측해 다른 요청에 붙이지 않는다 —
 * 고칠 자리는 `POST /api/meetings/{id}/todos/{todo}/promote` 의 **응답**이다.
 */
const PROMOTION_MATERIALS_GAP_TEXT =
  "회의록에서 연 승격은 아직 자료를 함께 싣지 못합니다 — 승격 응답이 요청 식별자를 돌려주지 않습니다. 여기서 고른 파일과 링크는 저장되지 않고, 창을 닫으면 사라집니다.";

/**
 * 업무를 **여럿 고르는 한 벌의 표** (새 발주 4 · 수정 발주).
 *
 * 참고 업무와 선행 업무가 이 표를 쓴다. 둘이 각자 다른 모양이면 같은 판 안에서 두 번 다른 읽기를
 * 요구하므로, 열(업무명 · 프로젝트 · 담당자)도 높이도 스크롤도 여기 한 자리에서 정한다.
 *
 * **하나만 고르는 자리는 이 표가 아니다.** 상위 업무는 한 행 2열의 왼쪽 칸에 서는데 표를 그 칸에
 * 넣으면 옆의 프로젝트 셀렉터와 높이가 세 곱절로 어긋난다 — 그 자리는 같은 `Select` 팝오버를 쓰고,
 * 목록 안에서 프로젝트·담당자를 한 줄로 보여 준다.
 *
 * **프로젝트는 비어 있을 수 있다** — 어느 묶음에도 없는 업무가 흔하다. 없는 것을 「—」로 말하고
 * 이름을 지어내지 않는다.
 */
export function TaskPickTable({
  label,
  tasks,
  selected,
  onToggle,
  projectNameOf,
  disabled = false,
  emptyText,
}: {
  label: string;
  tasks: DirectTask[];
  selected: string[];
  /** 한 줄이 켜지거나 꺼졌다. */
  onToggle: (taskId: string, next: boolean) => void;
  projectNameOf: (projectId: string | null | undefined) => string | null;
  disabled?: boolean;
  /** 고를 것이 없을 때의 한 줄. 「없다」와 「아직 고를 수 없다」는 다른 말이라 부르는 쪽이 준다. */
  emptyText: string;
}) {
  return (
    <div aria-label={label} className="scax-pick-table" role="table">
      <div className="scax-pick-table__head" role="row">
        <span className="scax-pick-table__cell--pick" role="columnheader">
          <span className="sr-only">고르기</span>
        </span>
        <span role="columnheader">업무명</span>
        <span role="columnheader">프로젝트</span>
        <span role="columnheader">담당자</span>
      </div>
      <div className="scax-pick-table__body scax-scroll">
        {tasks.length === 0 ? (
          <p className="t-meta scax-pick-table__empty">{emptyText}</p>
        ) : (
          tasks.map((task) => {
            const on = selected.includes(task.task_id);
            return (
              <label className="scax-pick-table__row" key={task.task_id} role="row">
                <span className="scax-pick-table__cell--pick" role="cell">
                  <input
                    aria-label={task.title}
                    checked={on}
                    disabled={disabled}
                    onChange={(event) => onToggle(task.task_id, event.target.checked)}
                    type="checkbox"
                  />
                </span>
                <span className="scax-pick-table__title" role="cell">{task.title}</span>
                <span className="scax-pick-table__meta" role="cell">{projectNameOf(task.project_id) ?? "—"}</span>
                <span className="scax-pick-table__meta" role="cell">
                  {task.assignee ? personName(task.assignee.display_name) : "—"}
                </span>
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}

/**
 * 업무·업무 요청을 만드는 자리.
 *
 * **바퀴 6bc (§8-B 14): 서랍에서 모달로 옮겼다** — 이름도 `CreateWorkDrawer` → `CreateWorkModal`.
 * 시안의 생성 자리는 오른쪽에서 밀려 들어오는 서랍이 아니라 가운데 서는 모달이다. 껍데기와 폭만
 * 바뀌었고 **필드 구성·모드(업무|요청)·유효성·저장 경로는 그대로**다. 여는 경로도 그대로(호출부 셋).
 * 폭은 부르는 쪽이 정한다 — 회의록의 승격은 `md`(560), 업무·오늘 화면은 기본(880).
 */
export function CreateWorkModal({
  /* `ownerName` 과 `origin` 은 **받되 읽지 않는다** — 둘이 그리던 상태·요청자 카드가 사라졌다.
     타입에 남겨 두는 것은 부르는 쪽 셋을 이 변경으로 건드리지 않기 위해서다(아래 주석 참조). */
  canCreateTask,
  canCreateRequest,
  assigneeCandidates,
  assignCandidates = [],
  ccCandidates = [],
  initial,
  onSubmitRequest,
  projectCandidates = noProjectCandidates,
  onCreated,
  onOpenTask,
  onError,
  onClose,
  size,
}: {
  /**
   * 부르는 사람의 이름. **이 창은 더 이상 읽지 않는다** (WORK-003 정정) — 「요청자」 카드가
   * 쓰던 유일한 자리였고, 요청자는 폼이 정하는 값이 아니라 서버가 기록하는 값이다.
   * 부르는 쪽 셋이 지금도 넘기고 있어 타입에는 남는다.
   */
  ownerName: string;
  canCreateTask: boolean;
  canCreateRequest: boolean;
  assigneeCandidates: Persona[];
  assignCandidates?: Persona[];
  ccCandidates?: Persona[];
  /**
   * 이미 적힌 것에서 여는 자리가 채워 주는 값 — 회의록의 후속업무 후보가 [업무 생성]으로 여는 경우다.
   * **담당은 채우지 않는다**: 사람을 고르는 것은 사람의 일이라 비운 채로 연다.
   */
  initial?: {
    title?: string;
    description?: string | null;
    dueDate?: string | null;
    checklist?: string[];
    /** 「다시 요청」이 미리 고르는 수신자 — 재요청은 같은 사람에게 다시 보내는 것이 기본이다. */
    assigneeId?: string;
    /** 하위 요청의 상위 업무 (V-9). **이 값은 `POST /api/work-requests` 로만 간다** — `POST /api/tasks` 의 수평 갈래는 거절한다(O-27). */
    parentTaskId?: string;
    /** 재요청이면 이전 요청 (V-12). 「다시 요청」과 「독촉」은 다른 것이고, 이 값이 그 둘을 가른다. */
    supersedesRequestId?: string;
  };
  /**
   * 요청을 보내는 자리를 갈아 끼운다 — 회의록의 후속업무 후보는 **승격**으로 나가야 출처 두 열이 함께 실린다.
   * 주지 않으면 지금까지대로 `createWorkRequest` 로 간다. 끝나고 낼 알림 문장을 돌려준다.
   */
  onSubmitRequest?: (
    input: {
      assignee_id: string;
      title: string;
      description?: string;
      due_date?: string | null;
      checklist?: string[];
    },
    /** 이 제출 의도의 멱등 키 — 실패 재시도 동안 같은 값이다. 승격은 그 위에 후보 잠금 층을 따로 갖는다. */
    idempotencyKey: string,
  ) => Promise<string>;
  projectCandidates?: Project[];
  /**
   * 이 요청이 «어디서 나왔는가». 회의록의 후속업무 후보에서 열렸으면 `"meeting"` 이다.
   *
   * **이 창은 더 이상 읽지 않는다** (WORK-003 정정). 이 값이 하던 일은 상태·요청자 카드를 회의
   * 승격에서만 걷는 것이었는데(§9-5 D40 · R-48), 그 카드가 **어느 갈래에서도 서지 않게** 되면서
   * 갈릴 것이 남지 않았다. 부르는 쪽(`MeetingDetailPage`)이 지금도 넘기고 있어 타입에는 남는다.
   */
  origin?: "meeting";
  /**
   * 만들어진 뒤 부르는 쪽이 목록을 다시 읽는 자리. 두 번째 인자는 **누구의 업무가 되었나** 다 —
   * 문구를 다시 읽어 갈래를 알아내지 않는다(그러면 문구를 고칠 때마다 조용히 어긋난다).
   */
  onCreated: (notice: string, outcome?: { assignedToOther: boolean }) => Promise<void> | void;
  /** 만든 업무를 여는 자리 — 첨부가 남았을 때 「업무 열기」가 이것을 쓴다. 없으면 그 단추를 그리지 않는다. */
  onOpenTask?: (taskId: string) => void;
  onError: (message: string | null) => void;
  onClose: () => void;
  /**
   * 골격 폭 (바퀴 6bc §8-B 14). 안 주면 DS 기본 880 — 두 모드·체크리스트·참고 업무·참조까지 다 서는
   * 업무/오늘 화면이 쓴다. 회의록의 승격은 요청 전용이라 필드가 적어 `md`(560)로 연다.
   */
  size?: "sm" | "md";
}) {
  /* 상위에 붙는 하위 요청과 재요청은 **요청 입구로만** 갈 수 있다 — `POST /api/tasks` 의 수평 갈래가
     `parent_task_id` 를 거절하기 때문이다(O-27). 그 의도로 열린 모달은 갈래를 고를 것이 없다. */
  const requestOnly = Boolean(initial?.parentTaskId || initial?.supersedesRequestId);
  const [kind, setKind] = useState<"task" | "request">(canCreateTask && !requestOnly ? "task" : "request");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [startDate, setStartDate] = useState("");
  const [dueDate, setDueDate] = useState(initial?.dueDate ?? "");
  // 미리 채운 값으로 열 때는 담당을 비워 둔다 — 첫 후보를 자동으로 고르지 않는다.
  const [assigneeId, setAssigneeId] = useState(initial ? initial.assigneeId ?? "" : assigneeCandidates[0]?.id ?? "");
  /** 고른 첨부 파일. **경로가 바뀌어도 조용히 버리지 않는다** — 아래 `attachSupported` 가 말만 바꾼다. */
  const [attachments, setAttachments] = useState<File[]>([]);
  /**
   * 고른 링크 자료 (최종 프레임 6 — 두 갈래 모두 파일/링크 UI 를 세운다).
   *
   * 파일과 **같은 두 단계**다: 내 업무는 `attachTaskMaterialLink`, 요청은
   * `attachWorkRequestMaterialLink` 로 만들어진 직후에 붙는다.
   */
  const [materialLinks, setMaterialLinks] = useState<MaterialLinkDraft[]>([]);
  /** 아직 목록에 들어가지 않은, 지금 적고 있는 링크 한 줄. */
  const [linkDraft, setLinkDraft] = useState<MaterialLinkDraft>({ url: "", label: "" });
  /**
   * **업무는 섰는데 첨부가 남은 자리.**
   *
   * 생성이 성공한 뒤 업로드가 실패하면 «생성부터 다시» 가 되어서는 안 된다 — 그러면 같은 업무가 둘
   * 선다. 만들어진 것을 여기 붙들어 두고, 다시 누르면 **업로드만** 다시 한다.
   *
   * 실패한 것은 **파일과 링크 두 갈래로 나눠 든다** — 다시 시도가 성공한 것을 두 번 붙이지 않는다.
   */
  const [created, setCreated] = useState<{
    /** 이미 만들어진 것 — 업무이거나 요청이다. 다시 누르면 **여기에만** 붙인다. */
    host: MaterialHost;
    title: string;
    failed: { files: File[]; links: MaterialLinkDraft[] };
    message: string;
    /** 만든 것이 남의 일이 되었나. 문구를 다시 읽어 갈래를 알아내지 않는다. */
    assignedToOther: boolean;
  } | null>(null);
  const [taskOwnerId, setTaskOwnerId] = useState("me");
  const [projectId, setProjectId] = useState("");
  const [availableProjects, setAvailableProjects] = useState<Project[]>(projectCandidates);
  const [ccIds, setCcIds] = useState<string[]>([]);
  const [steps, setSteps] = useState<string[]>(initial?.checklist ?? []);
  const [newStep, setNewStep] = useState("");
  const [linkedTasks, setLinkedTasks] = useState<DirectTask[]>([]);
  /**
   * 상위 업무 — **실제로 보내는 값이다** (`parent_task_id`).
   *
   * 여는 쪽이 정해 준 값(하위 요청 보내기)이면 그 값으로 시작하고 고치지 못한다: 어느 업무 아래에
   * 매달 것인지는 그 화면이 이미 정했다. 그 밖에서는 내가 읽을 수 있는 업무 중에서 고른다 —
   * `POST /api/tasks` 의 본인 갈래와 `POST /api/work-requests` 둘 다 이 값을 받는다.
   */
  const [parentTaskId, setParentTaskId] = useState(initial?.parentTaskId ?? "");
  /**
   * 선행업무 — **실제로 보낸다** (WORK-003 Phase 4 · SPEC-001 U-13 · §4 `preceding_task_ids`).
   *
   * 이 값이 화면에만 살던 시절이 있었다(계약이 없던 때). SPEC-001 이 `preceding_task_ids` 를
   * 생성 payload 에 고정하면서 그 임시 상태가 끝났다 — 이제 고른 것은 저장되고, 「아직 저장되지
   * 않습니다」류의 안내를 남기지 않는다. 저장된 척도, 저장 안 된 척도 하지 않는다.
   */
  const [precedingTaskIds, setPrecedingTaskIds] = useState<string[]>([]);
  /**
   * 결재자 — 계약 이름은 **승인자**(`approver_id`)이고 화면 라벨만 「결재자」다 (SPEC-001 §4 · OQ-N).
   *
   * **두 갈래 모두 보낸다** (WORK-003 정정). 요청이 업무가 될 때 결재자가 함께 넘어가야 하고,
   * 고른 값을 화면에서만 들고 버리면 고른 사람은 「저장됐다」고 읽는다.
   *
   * 한때 여기 「요청 갈래에서는 아직 422 가 돌아올 수 있다(OQ-M)」는 메모가 있었다. **그 미결은
   * 닫혔다** — `WorkRequestService.create` 가 `approver_id` 를 받아 `valid_approver` 로 검증하고
   * 요청 조회에도 같은 이름으로 낸다. 사실이 아닌 메모는 고른 값을 버리는 것만큼 나쁘다.
   */
  const [approverId, setApproverId] = useState("");
  /** 지금 보고 있는 탭. 처음은 늘 「기본 정보」다 — 제목 없이는 아무것도 만들 수 없다. */
  const [tab, setTab] = useState<CreateTab>("basic");
  const [referenceChoices, setReferenceChoices] = useState<DirectTask[] | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  /**
   * 담당 후보는 **envelope 이 허용한 경로의 목록만** 합친다 (W1 계약 §4).
   *
   * `assignCandidates` 는 배정 권한이 있을 때만 부르는 쪽이 채워 주는 관리자 배정 후보이고,
   * `assigneeCandidates` 는 `work_request.create` 를 가진 사람이 보낼 수 있는 수신 후보다. 두 목록에
   * 같은 사람이 있으면 **기존 관리자 배정 경로를 우선**한다 — 요청 출처와 완료 승인의 뜻이 달라서,
   * 관리자가 지금까지 만들던 것을 다른 종류로 바꾸지 않는다. 화면에 뜬 사람은 둘 중 한 경로로
   * 반드시 보낼 수 있다(목록 ↔ 판정 일치).
   */
  const horizontalCandidates = canCreateRequest ? assigneeCandidates : [];
  const recipientCandidates = [
    ...assignCandidates,
    ...horizontalCandidates.filter((candidate) => !assignCandidates.some((managed) => managed.id === candidate.id)),
  ];
  /** 이 대상에게 어느 명령으로 가는가. 화면과 제출이 같은 함수를 쓴다 — 갈리면 뜬 사람에게 보냈다 거절된다. */
  function routeFor(ownerId: string): "self" | "managed" | "horizontal" | null {
    if (ownerId === "me" || !ownerId) return "self";
    if (assignCandidates.some((candidate) => candidate.id === ownerId)) return "managed";
    if (horizontalCandidates.some((candidate) => candidate.id === ownerId)) return "horizontal";
    return null;
  }
  const assignTarget = taskOwnerId === "me" ? null : recipientCandidates.find((candidate) => candidate.id === taskOwnerId) ?? null;
  const ownerRoute = routeFor(taskOwnerId);
  /**
   * **수평 생성은 시작일을 받지 않는다** — 서버가 조용히 버리지 않고 422 로 거절한다
   * (`creation_commands.py` `_refuse_unsupported_horizontal_fields`). 그러니 그 대상에서는 줄을
   * 세우지도, payload 에 싣지도 않는다. 본인·관리자 배정은 지금까지대로 받는다.
   *
   * 값 자체는 **지우지 않는다**: 본인으로 적어 둔 시작일이 담당을 바꿨다 되돌리면 그대로 살아 있어야 한다.
   * 대신 아래 `effectiveStartDate` 로 «지금 이 경로가 실제로 보내는 값» 을 하나로 좁혀, 숨긴 날짜가
   * 지문·전송·유효성 어디에도 섞이지 않게 한다.
   */
  const startDateSupported = ownerRoute !== "horizontal";
  const effectiveStartDate = startDateSupported ? startDate : "";
  /**
   * 한 제출 의도에 키 하나. 쓴 것이 그대로면 재시도·연타가 **같은 키**를 다시 보내 서버가 첫 결과를
   * 영수증으로 돌려주고, 쓴 것이 달라지면 그것은 새 의도라 **새 키**를 만든다. 경로도 여기 함께 박아
   * 둔다 — 재시도 도중 endpoint 가 바뀌지 않는다(W1 계약 §5).
   */
  const submitAttempt = useRef<{ fingerprint: string; key: string; route: "self" | "managed" | "horizontal" } | null>(null);
  /** 같은 tick 에 두 번째로 들어온 제출은 React 가 단추를 비활성으로 다시 그리기 전에 여기서 막힌다. */
  const submitting = useRef(false);

  /* 연관 업무 판의 세 줄(상위 업무·선행 업무·연관 업무)이 모두 이 목록을 읽는다 — 갈래를 가리지 않는다.
     예전에는 업무 갈래에서만 읽어서, 요청 갈래는 「참고 업무 연결」을 누를 때까지 목록이 없었다. */
  useEffect(() => {
    if (referenceChoices !== null) return;
    let cancelled = false;
    void (async () => {
      try {
        const rows = await getTasks(true);
        if (!cancelled) setReferenceChoices(rows ?? []);
      } catch {
        if (!cancelled) setReferenceChoices([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [referenceChoices]);

  useEffect(() => {
    if (projectCandidates.length > 0) {
      setAvailableProjects(projectCandidates);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const rows = await listProjects();
        if (!cancelled) setAvailableProjects(rows ?? []);
      } catch {
        if (!cancelled) setAvailableProjects([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectCandidates]);

  /* 최종 발주 2·3: 업무 갈래는 `TaskDraftFields`(계약 기반 폼)로 그렸었다. 이제 왼쪽 세로 탭이
     배치를 정하고 필드도 DS 부품(`Select`·`DateField`)으로 서므로 그 계약 폼과 어댑터 둘을 지웠다 —
     호출부 0. **보내는 값과 경로는 한 줄도 바뀌지 않았다**: 아래 `submit()` 이 읽는 상태 그대로다. */

  /**
   * **자료를 붙일 수 있는 갈래인가** — 서버의 «쓰기 권한» 이 정한다.
   *
   * 업무 자료 업로드는 `materials.upload` → `_upload_task` → `work_tasks.task(task_id, principal)` 로
   * 가고, 그 조회는 **활성 담당** 에게만 연다. 그래서 갈래마다 답이 다르다.
   *
   * · **본인 업무**: 만드는 순간 내가 활성 담당이다 → 생성 응답의 `task_id` 로 바로 붙는다.
   * · **관리자 배정**: 활성 담당이 상대다 → 내가 붙일 수 없다.
   * · **요청 발송 · 회의 승격**: 수락 전에는 **아무도 활성 담당이 아니다** → 붙일 사람이 없다.
   *
   * 그 셋에서 화면이 업로드를 시도하면 404 다. **새 권한을 만들지 않고**, 누가 어디서 붙일 수 있는지를
   * 말한다 — 「생성 뒤 상세에서」라고만 쓰면 보내는 사람도 할 수 있는 것처럼 읽혀 틀린다.
   */
  const attachSupported = kind === "task" && ownerRoute === "self";
  /**
   * **자료 판은 두 갈래 모두 선다** (최종 프레임 6) — 다만 **저장되는가는 갈래마다 다르다.**
   *
   * 판을 내려 버리면 사람이 디자인을 볼 수 없고, 판을 세운 채 저장되는 척하면 고른 것이 조용히
   * 사라진다. 그 사이를 이 값 하나가 가른다: 판은 언제나 서고, 이 값이 `false` 면 화면이 **왜
   * 저장되지 않는지**를 말한다.
   */
  /**
   * **요청 갈래는 이제 저장된다** (WORK-003) — 발송이 돌려주는 `request_id` 로 붙는다.
   *
   * 예외는 회의 승격 한 곳이다: 그 어댑터는 식별자를 돌려주지 않아 붙일 자리를 모른다
   * (`PROMOTION_MATERIALS_GAP_TEXT`).
   */
  const requestMaterialsSupported = kind === "request" && !onSubmitRequest;
  const materialsSaved = kind === "task" ? attachSupported : requestMaterialsSupported;
  const optionalTabs = OPTIONAL_TABS;
  /** 저장되지 않는 이유 — 갈래마다 다른 사실이라 문구도 다르다. 「나중에」로 뭉뚱그리지 않는다. */
  const materialsGapText =
    kind === "request"
      ? PROMOTION_MATERIALS_GAP_TEXT
      : ownerRoute === "horizontal"
        ? "자료는 담당자가 업무 상세에서 첨부할 수 있습니다 — 요청은 상대가 수락해 담당자가 된 뒤입니다."
        : "자료는 담당자가 업무 상세에서 첨부할 수 있습니다 — 배정한 업무는 그 담당자입니다.";

  /**
   * 만들어진 것에 고른 파일과 링크를 붙인다. **실패한 것만** 돌려준다 — 성공한 것을 다시 올리지 않는다.
   *
   * 링크도 파일과 **같은 두 단계**다: 무엇인가 서야 식별자가 생기고, 그 뒤에 `…/materials` ·
   * `…/materials/links` 로 붙는다. 업무와 요청이 **같은 걸음**을 걷고 입구 한 쌍만 갈린다.
   */
  async function attachTo(
    host: MaterialHost,
    files: File[],
    links: MaterialLinkDraft[],
  ): Promise<{ files: File[]; links: MaterialLinkDraft[] }> {
    const failedFiles: File[] = [];
    for (const file of files) {
      try {
        if (host.kind === "task") await uploadTaskMaterial(host.id, "input", file);
        else await uploadWorkRequestMaterial(host.id, file);
      } catch {
        failedFiles.push(file);
      }
    }
    const failedLinks: MaterialLinkDraft[] = [];
    for (const link of links) {
      try {
        if (host.kind === "task") await attachTaskMaterialLink(host.id, "input", link);
        else await attachWorkRequestMaterialLink(host.id, link);
      } catch {
        failedLinks.push(link);
      }
    }
    return { files: failedFiles, links: failedLinks };
  }

  /** 생성은 끝났고 첨부만 남은 자리에서 다시 누르는 길. **업무도 요청도 다시 만들지 않는다.** */
  async function retryAttach() {
    if (!created) return;
    setIsWorking(true);
    onError(null);
    try {
      const pending = created.failed.files.length + created.failed.links.length;
      const failed = await attachTo(created.host, created.failed.files, created.failed.links);
      const left = failed.files.length + failed.links.length;
      if (left === 0) {
        await onCreated(`${created.message} 첨부 ${pending}건을 모두 올렸습니다.`, { assignedToOther: created.assignedToOther });
        setCreated(null);
        setAttachments([]);
        setMaterialLinks([]);
        onClose();
        return;
      }
      setCreated({ ...created, failed });
      onError(
        created.host.kind === "task"
          ? `첨부 ${left}건을 아직 올리지 못했습니다. 다시 시도하거나 업무 상세에서 붙일 수 있습니다.`
          : `첨부 ${left}건을 아직 올리지 못했습니다. 다시 시도하거나 요청 상세에서 붙일 수 있습니다.`,
      );
    } finally {
      setIsWorking(false);
    }
  }

  async function submit() {
    if (submitting.current) return;
    // 이미 만들어진 뒤라면 남은 일은 첨부뿐이다 — 생성 경로로 되돌아가지 않는다.
    if (created) {
      await retryAttach();
      return;
    }
    const trimmed = title.trim();
    if (!trimmed) {
      onError("업무 제목을 입력해 주세요.");
      return;
    }
    if (kind === "request" && !assigneeId) {
      onError("담당 후보를 선택해 주세요.");
      return;
    }
    /*
     * 날짜의 앞뒤는 **두 갈래 모두** 본다 (최종 프레임 FE 정리).
     *
     * 요청 갈래에도 시작일이 서고 `start_date` 로 실려 나가므로(SPEC-001 U-6-a), 뒤집힌 두 날짜를
     * 업무 갈래에서만 막던 것은 갈래마다 다른 규칙이 아니라 **빠뜨린 것**이었다. 숨긴 시작일로
     * 제출을 막지는 않는다 — 안 보내는 값이 사람을 세우면 고칠 자리가 없다.
     */
    if (effectiveStartDate && dueDate && effectiveStartDate > dueDate) {
      onError(kind === "task" ? "시작일은 기한보다 늦을 수 없습니다." : "시작일은 희망 기한보다 늦을 수 없습니다.");
      return;
    }
    /*
     * **같은 업무가 상위이면서 선행일 수는 없다** — 「무엇 아래인가」와 「무엇 다음인가」는 다른
     * 관계라 같은 업무가 둘 다이면 뜻이 서지 않는다. 화면이 후보에서 이미 서로를 빼지만, 여는 쪽이
     * 정해 준 상위(`initial.parentTaskId`)처럼 고르기를 거치지 않는 길이 있어 여기서 한 번 더 굳힌다.
     */
    if (parentTaskId && precedingTaskIds.includes(parentTaskId)) {
      onError("상위 업무는 선행 업무로 함께 고를 수 없습니다.");
      return;
    }
    const route = ownerRoute;
    if (kind === "task" && route === null) {
      // 후보 목록에 없는 대상이다 — 임의로 다른 경로를 골라 보내지 않는다.
      onError("이 사람에게는 업무를 보낼 수 없습니다.");
      return;
    }
    // Steps written here belong to the work from the start, in the order they were written.
    const checklist = steps.length > 0 ? steps : undefined;
    // Earlier work pointed at here travels with the request into the Task it becomes.
    const reference_task_ids = linkedTasks.length > 0 ? linkedTasks.map((row) => row.task_id) : undefined;
    const payload = {
      kind,
      route,
      title: trimmed,
      description: description.trim(),
      // 이 경로가 실제로 보내는 값만 지문에 든다 — 숨긴 시작일이 새 의도를 만들지 않는다.
      startDate: effectiveStartDate,
      dueDate,
      checklist: steps,
      reference_task_ids: reference_task_ids ?? [],
      projectId,
      /* 실제로 실려 나가는 값만 지문에 든다. 선행업무도 결재자도 이제 **두 갈래 모두** 실리므로
         갈래를 가리지 않고 그대로 든다 — 고친 값이 지문을 흔들어야 재시도가 새 키를 받는다. */
      parentTaskId,
      precedingTaskIds,
      approverId,
      assigneeId,
      taskOwnerId,
      ccIds,
    };
    const fingerprint = JSON.stringify(payload);
    if (submitAttempt.current?.fingerprint !== fingerprint) {
      submitAttempt.current = { fingerprint, key: createIdempotencyKey(), route: (route ?? "self") as "self" | "managed" | "horizontal" };
    }
    const attempt = submitAttempt.current;
    submitting.current = true;
    setIsWorking(true);
    onError(null);
    try {
      if (kind === "task" && assignTarget && attempt.route === "managed") {
        await assignTask(trimmed, assignTarget.id, {
          description: description.trim() || undefined,
          start_date: effectiveStartDate || undefined,
          due_date: dueDate || undefined,
          checklist,
        }, attempt.key);
        await onCreated(`'${trimmed}' 업무가 ${personName(assignTarget.display_name)}의 업무가 되었습니다. 수락을 기다리지 않습니다.`, { assignedToOther: true });
      } else if (kind === "task" && assignTarget) {
        /* 수평 생성에는 `start_date` 를 싣지 않는다 — 서버가 받지 않는 값이라 실으면 422 다.
           위에서 줄을 세우지 않았으므로 `effectiveStartDate` 는 언제나 빈 값이고, 여기서 한 번 더 굳힌다. */
        await createDirectTask(trimmed, {
          description: description.trim() || undefined,
          due_date: dueDate || undefined,
          checklist,
          reference_task_ids,
          assignee_id: assignTarget.id,
        }, attempt.key);
        /* 이 갈래는 원래부터 **요청**이다(O-26) — v2 가 바꾸는 것은 그 요청의 «응답 단계» 뿐이다. */
        await onCreated(`'${trimmed}' 업무를 ${personName(assignTarget.display_name)}에게 보냈습니다. 상대가 수락하면 그 사람의 업무가 됩니다.`, { assignedToOther: true });
      } else if (kind === "task") {
        const madeTask = await createDirectTask(trimmed, {
          description: description.trim() || undefined,
          start_date: effectiveStartDate || undefined,
          due_date: dueDate || undefined,
          checklist,
          reference_task_ids,
          /* 본인 갈래는 `parent_task_id` 를 받는다 — 거절하는 것은 담당을 지정한 수평 갈래뿐이다
             (`creation_commands.py` `_refuse_unsupported_horizontal_fields`). */
          parent_task_id: parentTaskId || undefined,
          project_id: projectId || undefined,
          /* 참조자는 **두 갈래 모두** 저장된다 (SPEC-001 U-6-a). 「내 업무로 만들면 저장되지
             않는다」는 사실이 아니었고, 그 문구도 함께 걷었다. */
          cc_member_ids: ccIds.length > 0 ? ccIds : undefined,
          /* 결재자는 **두 갈래 공통 한 벌**이다 (WORK-003 정정) — 아래 요청 갈래도 같은 값을 싣는다. */
          approver_id: approverId || undefined,
          preceding_task_ids: precedingTaskIds.length > 0 ? precedingTaskIds : undefined,
        }, attempt.key);
        const notice = `'${trimmed}' 업무를 만들었습니다.`;
        /*
         * **두 단계다** — 업무가 서야 붙일 자리가 생긴다(자료는 업무에 매달린다). 여기서부터는
         * 생성이 이미 끝났으므로, 붙이다 실패해도 **생성으로 되돌아가지 않는다.**
         */
        const picked = attachments.length + materialLinks.length;
        if (picked > 0) {
          const failed = await attachTo({ kind: "task", id: madeTask.task_id }, attachments, materialLinks);
          const left = failed.files.length + failed.links.length;
          // 만들어진 사실은 어느 쪽이든 먼저 알린다 — 목록이 그 업무를 들고 있어야 한다.
          await onCreated(left === 0 ? `${notice} 첨부 ${picked}건을 올렸습니다.` : notice, { assignedToOther: false });
          if (left > 0) {
            // 닫지 않는다. 「업무는 섰고 첨부가 남았다」는 사실을 사람이 보고 고를 수 있어야 한다.
            submitAttempt.current = null;
            setCreated({
              host: { kind: "task", id: madeTask.task_id },
              title: madeTask.title || trimmed,
              failed,
              message: notice,
              assignedToOther: false,
            });
            onError(`업무는 만들어졌지만 첨부 ${left}건을 올리지 못했습니다. 다시 시도하거나 업무 상세에서 붙일 수 있습니다.`);
            return;
          }
        } else {
          await onCreated(notice, { assignedToOther: false });
        }
      } else if (onSubmitRequest) {
        /*
         * 회의 승격 어댑터 — **이 입구가 나르는 것은 다섯뿐이다** (`promoteMeetingTodo`:
         * `assignee_id` · `title` · `description` · `due_date` · `checklist`).
         *
         * ⚠️ **BE API gap (승격 입력 확장 필요).** 폼이 받아 든 나머지는 여기서 멈춘다:
         * `start_date` · `cc_member_ids` · `approver_id` · `reference_task_ids` · `project_id` ·
         * `parent_task_id` · `preceding_task_ids` 일곱이다. 같은 값들이 **일반 요청 경로
         * (`POST /api/work-requests`)로는 그대로 실려 나가므로**, 회의에서 연 창만 조용히 적게
         * 보낸다 — 고른 사람은 그 차이를 볼 수 없다.
         *
         * ⚠️ **자료도 여기서만 멈춘다.** 이 어댑터가 돌려주는 것은 사람에게 보일 문장뿐이라 붙일
         * 자리(`request_id`)가 오지 않는다. 그래서 이 갈래에서는 자료 판이 그 사실을 그대로 말하고
         * (`PROMOTION_MATERIALS_GAP_TEXT`), 다른 요청에 추측으로 붙이지 않는다. 고칠 자리는 승격
         * 입구의 **응답**이다.
         *
         * 이 화면이 임의로 다른 입구로 새지 않는다(그러면 출처 두 열이 빠진다). 고칠 자리는
         * `POST /api/meetings/{id}/todos/{todo}/promote` 의 입력 모델이고, 그 확장이 이 작업의
         * BE 요구 목록에 올라 있다. 여기서 backend 는 건드리지 않는다.
         */
        await onCreated(
          await onSubmitRequest({
            assignee_id: assigneeId,
            title: trimmed,
            description: description.trim() || undefined,
            due_date: dueDate || null,
            checklist,
          }, attempt.key),
          { assignedToOther: true },
        );
      } else {
        const request = await createWorkRequest(trimmed, assigneeId, {
          description: description.trim() || undefined,
          /* **요청 갈래에도 시작일이 간다** (SPEC-001 U-6-a) — 요청 생성 입력은 원래 이 값을 받고
             있었고 화면이 접고 있었을 뿐이다. */
          start_date: effectiveStartDate || undefined,
          due_date: dueDate || undefined,
          cc_member_ids: ccIds.filter((id) => id !== assigneeId),
          checklist,
          reference_task_ids,
          project_id: projectId || undefined,
          ...(parentTaskId ? { parent_task_id: parentTaskId } : {}),
          preceding_task_ids: precedingTaskIds.length > 0 ? precedingTaskIds : undefined,
          /* **결재자를 그대로 싣는다** (WORK-003 정정) — 요청이 업무가 될 때 함께 넘어갈 값이고,
             서버의 요청 생성이 `approver_id` 를 받아 검증한다(`valid_approver`). */
          approver_id: approverId || undefined,
          ...(initial?.supersedesRequestId ? { supersedes_request_id: initial.supersedesRequestId } : {}),
          /*
           * **자료는 이 payload 로 가지 않는다 — 다음 걸음으로 간다** (WORK-003).
           *
           * 고른 파일(`attachments`)과 링크(`materialLinks`)는 요청 생성 입력에 **어느 키로도
           * 실리지 않는다**: 이름을 지어내 싣는 것은 「저장됐다」는 거짓말을 만든다. 아래에서
           * 돌아온 `request_id` 로 `…/materials` · `…/materials/links` 에 붙는다 — 내 업무가 지나는
           * 길과 같은 두 단계다. 댓글 첨부·evidence 로 우회하지 않는다(뜻이 다른 자리다).
           */
        }, attempt.key);
        const assignee = assigneeCandidates.find((candidate) => candidate.id === assigneeId);
        /* v2: 보내는 것으로 담당이 서지 않는다 — 상대가 수락해야 그 사람의 업무가 된다(V-9·V-10). */
        const notice = `'${request.title}' 업무를 ${assignee ? personName(assignee.display_name) : "담당 후보"}에게 보냈습니다. 상대가 수락하면 그 사람의 업무가 됩니다.`;
        /*
         * **두 단계의 두 번째다** — 요청이 서야 자료가 매달릴 자리(`request_id`)가 생긴다. 여기서
         * 부터는 발송이 이미 끝났으므로, 붙이다 실패해도 **요청을 다시 보내지 않는다.**
         */
        const pickedForRequest = attachments.length + materialLinks.length;
        if (pickedForRequest > 0) {
          const failed = await attachTo({ kind: "request", id: request.request_id }, attachments, materialLinks);
          const left = failed.files.length + failed.links.length;
          // 보냈다는 사실은 어느 쪽이든 먼저 알린다 — 목록이 그 요청을 들고 있어야 한다.
          await onCreated(left === 0 ? `${notice} 자료 ${pickedForRequest}건을 함께 보냈습니다.` : notice, { assignedToOther: true });
          if (left > 0) {
            // 닫지 않는다. 「요청은 갔고 자료가 남았다」는 사실을 사람이 보고 고를 수 있어야 한다.
            submitAttempt.current = null;
            setCreated({
              host: { kind: "request", id: request.request_id },
              title: request.title || trimmed,
              failed,
              message: notice,
              assignedToOther: true,
            });
            onError(`요청은 보냈지만 첨부 ${left}건을 올리지 못했습니다. 다시 시도하거나 요청 상세에서 붙일 수 있습니다.`);
            return;
          }
        } else {
          await onCreated(notice, { assignedToOther: true });
        }
      }
      // 여기까지 오면 그 의도는 끝났다 — 다음에 만드는 업무는 새 의도이므로 새 키를 받는다.
      submitAttempt.current = null;
      onClose();
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 만들지 못했습니다.");
    } finally {
      submitting.current = false;
      setIsWorking(false);
    }
  }

  /* 새 발주 3·4: 「참고 업무 연결」로 칸을 열고 한 건씩 잇던 세 걸음(`openReferences`·`linkReference`)이
     여기 있었다. 세 자리(상위·참고·선행)가 같은 표를 쓰게 되면서 «열고 · 고르고 · 연결하는» 것이 체크
     한 번으로 줄었다 — 호출부 0 이라 지웠다. 담는 값(`linkedTasks` → `reference_task_ids`)은 그대로다. */

  function appendStep() {
    const step = newStep.trim();
    if (!step) return;
    setSteps((current) => [...current, step]);
    setNewStep("");
  }

  const titleInputId = kind === "task" ? "task-title" : "work-request-title";
  /**
   * 만들 수 있는 것이 한 가지뿐이면 고를 것이 없다 — 토글을 두지 않고 모달 이름이 그 한 가지를 말한다.
   *
   * 한 칸짜리 세그먼트는 늘 「선택됨」이라 누를 수 있는 것처럼 보이는데 실은 바뀌지 않는다. 회의록의
   * 후속업무 후보에서 여는 자리가 그랬다 — 승격은 언제나 업무 요청이라(D19·D24) 「요청」 하나가 검은
   * 단추처럼 남아 있었다.
   */
  const oneKind = canCreateTask !== canCreateRequest;
  /**
   * 모달 이름은 **지금 고른 갈래를 따른다** (최종 발주 1).
   *
   * 지금까지는 「새 업무 추가」 하나로 두고 갈래는 토글만 말했다 — 요청을 고른 뒤에도 머리는 계속
   * 「업무 추가」라서, 보내는 것이 무엇인지 머리와 발(제출 단추)이 서로 다른 말을 했다.
   * 토글은 그대로 두고 **이름만** 따라 움직인다.
   */
  const drawerTitle = kind === "task" ? "새 업무 추가" : "새 업무 요청";
  /* 상태·요청자 카드는 어느 갈래에도 서지 않는다 (WORK-003 정정) — 둘 다 생성 입력값이 아니라
     서버가 정하는 값이고, 그래서 그것을 그리던 `showOriginMeta`·`metaGridShown` 도 함께 지웠다.
     한때 회의 승격에서만 걷던 두 줄이다(§9-5 D40) — 이제 «회의에서만» 이 아니라 «어디서도» 다. */

  const tabPanelId = (id: CreateTab) => `create-panel-${id}`;
  const tabButtonId = (id: CreateTab) => `create-tab-${id}`;
  /** 세로 탭의 화살표 이동 — 목록 안에서 위·아래로 돈다 (WAI-ARIA tabs 패턴). */
  function moveTab(event: React.KeyboardEvent, group: ReadonlyArray<CreateTab>) {
    const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const at = group.indexOf(tab);
    const next = group[(at + step + group.length) % group.length];
    setTab(next);
    document.getElementById(tabButtonId(next))?.focus();
  }
  function tabButton(id: CreateTab, label: string, group: ReadonlyArray<CreateTab>) {
    const on = tab === id;
    return (
      <button
        aria-controls={tabPanelId(id)}
        aria-selected={on}
        className={on ? "scax-create-tabs__tab is-on" : "scax-create-tabs__tab"}
        id={tabButtonId(id)}
        key={id}
        onClick={() => setTab(id)}
        onKeyDown={(event) => moveTab(event, group)}
        role="tab"
        tabIndex={on ? 0 : -1}
        type="button"
      >
        {label}
      </button>
    );
  }
  function panel(id: CreateTab, children: React.ReactNode) {
    return (
      <div
        aria-labelledby={tabButtonId(id)}
        className="scax-create-tabs__panel scax-scroll"
        hidden={tab !== id}
        id={tabPanelId(id)}
        key={id}
        role="tabpanel"
        tabIndex={0}
      >
        {children}
      </div>
    );
  }

  /**
   * 결재자 후보 — 참조 후보와 수신 후보를 겹치지 않게 합친다.
   *
   * 아직 보내지 않는 값이라 «누가 결재자가 될 수 있나» 를 정하는 계약이 없다. 그래서 **이 사람이
   * 이미 고를 수 있는 사람** 을 그대로 쓴다 — 없는 명단을 지어내지 않는다.
   */
  /**
   * 「상위 업무 없음」이 고르는 값 (최종 발주 2).
   *
   * 빈 문자열을 항목 값으로 두면 **아무것도 안 고른 상태와 구별되지 않는다** — 트리거가
   * placeholder(「상위 업무 선택」) 대신 「상위 업무 없음」을 띄워, 고른 적 없는 사람에게 고른 것처럼
   * 읽힌다. 그래서 항목에는 표시용 값을 주고, 나가는 값은 여기서 빈 값으로 되돌린다.
   */
  const NO_PARENT = "__no_parent__";
  const approverCandidates = [
    ...ccCandidates,
    ...assigneeCandidates.filter((candidate) => !ccCandidates.some((cc) => cc.id === candidate.id)),
  ];
  const projectNameOf = (id: string | null | undefined) =>
    id ? availableProjects.find((project) => project.project_id === id)?.name ?? null : null;
  /**
   * 선행 업무로 고를 수 있는 것 — **고른 프로젝트 안에서 내가 읽을 수 있는 업무**다 (새 발주 3).
   *
   * 새 조회를 열지 않는다: 이 판이 이미 읽어 둔 목록(`getTasks(include_closed`)을 프로젝트로 거른다.
   * 그래서 여기 서는 것은 언제나 서버가 내게 내준 것뿐이고, 못 읽는 업무는 셈에도 목록에도 없다.
   */
  const precedingChoices = projectId
    ? (referenceChoices ?? []).filter(
        (row) =>
          row.project_id === projectId &&
          /* **상위로 고른 업무는 선행 후보가 아니다.** 「무엇 아래인가」와 「무엇 다음인가」는 다른
             관계라 같은 업무가 둘 다이면 뜻이 서지 않는다. 예전에는 여는 쪽이 준 값
             (`initial.parentTaskId`)만 뺐고, 창 안에서 고른 상위는 그대로 후보에 남아 있었다. */
          row.task_id !== parentTaskId &&
          // 이미 고른 것은 후보에서 빠진다 — 빼는 길은 아래 칩의 「빼기」다.
          !precedingTaskIds.includes(row.task_id),
      )
    : [];
  /**
   * 상위 업무 후보 — **이미 선행으로 고른 업무는 빠진다** (위와 같은 이유의 반대 방향).
   *
   * 고른 것을 조용히 옮기거나 지우지 않고 **고를 수 없게** 한다: 선행에서 빼면 상위 목록에 다시 선다.
   */
  const parentChoices = (referenceChoices ?? []).filter((row) => !precedingTaskIds.includes(row.task_id));
  /** 고른 선행의 요약 — 칩으로 서고 칩마다 「빼기」가 있다 (SPEC-001 U-13). */
  const precedingTasks = precedingTaskIds.flatMap((id) => {
    const found = (referenceChoices ?? []).find((row) => row.task_id === id);
    return found ? [found] : [];
  });

  return (
    <Modal
      className="scax-modal--create"
      closeLabel="닫기"
      footer={
        created ? (
          /* **만들어진 것은 이미 섰다**(업무든 요청이든). 남은 것은 첨부뿐이라, 여기서 무엇을 눌러도
             업무도 요청도 다시 만들어지지 않는다. */
          <>
            <Button variant="text" disabled={isWorking} onClick={onClose} type="button">
              나중에 붙이기
            </Button>
            {/* 「업무 열기」는 **업무가 섰을 때만** 선다 — 보낸 요청은 아직 업무가 아니라 열 상세가 없다. */}
            {onOpenTask && created.host.kind === "task" && (
              <Button
                disabled={isWorking}
                onClick={() => {
                  const taskId = created.host.id;
                  onClose();
                  onOpenTask(taskId);
                }}
                type="button"
              >
                업무 열기
              </Button>
            )}
            <Button variant="solid" tone="primary" disabled={isWorking} onClick={() => void retryAttach()} type="button">
              {isWorking ? "올리는 중…" : `첨부 다시 시도 (${created.failed.files.length + created.failed.links.length})`}
            </Button>
          </>
        ) : (
          <>
            <Button variant="text" disabled={isWorking} onClick={onClose} type="button">
              닫기
            </Button>
            <Button variant="solid" tone="primary" disabled={isWorking || (kind === "request" && assigneeCandidates.length === 0)} onClick={() => void submit()}
              type="button"
            >
              {isWorking ? "만드는 중…" : kind === "task" ? "업무 추가" : "업무 요청 보내기"}
            </Button>
          </>
        )
      }
      headerExtra={
        /*
          * 머리는 **한 줄**이다 (최종 발주 7) — 왼쪽에 이름, 오른쪽에 갈래 토글. 지금까지 여기 있던
          * 「내가 할 업무를 만듭니다…」류의 설명 문구는 걷었다: 갈래가 무엇을 하는지는 토글과 아래 폼이
          * 이미 말하고, 그 문구들이 머리를 두 줄·세 줄로 늘려 본문을 밀고 있었다.
          */
        canCreateTask && canCreateRequest ? (
          <SegmentedControl
            ariaLabel="생성 유형"
            onChange={setKind}
            /* 갈래 이름은 **무엇이 만들어지는가**로 읽힌다 (사용자 확정) — 「업무」·「요청」은 동사처럼
               읽혀서 무엇이 서는지가 모호했다. 값(`task`·`request`)도 제목도 payload 도 그대로다. */
            options={[
              { value: "task", label: "내 업무" },
              { value: "request", label: "요청 업무" },
            ]}
            value={kind}
          />
        ) : null
      }
      label={drawerTitle}
      onClose={onClose}
      size={size}
      title={drawerTitle}
    >
      {/*
        * 최종 발주 1·2 — **크기가 고정된 모달 안의 왼쪽 세로 탭.**
        *
        * 지금까지 필드는 한 기둥에 길게 이어 붙어 있었고(제목 → 메타표 → 참조자 → 참고 업무 → 시작 단계
        * → 내용 → 첨부), 갈래를 바꾸거나 필드가 늘어날 때마다 모달이 세로로 자랐다. 이제 골격이
        * **머리 · 왼쪽 탭 · 스크롤하는 판 · 발**로 고정되고, 늘어나는 것은 판 안쪽뿐이다.
        *
        * 판은 **넷 다 그려 둔 채 숨긴다** — 탭을 옮겨도 적어 둔 값과 스크롤 위치가 그대로 남는다.
        */}
      <div className="scax-create-tabs">
        <div className="scax-create-tabs__nav">
          {/* 필수와 선택을 나눠 세운다 — 무엇을 반드시 채워야 하는지가 목록 자체로 읽힌다. */}
          <p className="scax-create-tabs__group" id="create-tabs-required">필수</p>
          <div aria-labelledby="create-tabs-required" aria-orientation="vertical" className="scax-create-tabs__list" role="tablist">
            {tabButton("basic", "기본 정보", REQUIRED_TABS)}
          </div>
          <p className="scax-create-tabs__group" id="create-tabs-optional">선택</p>
          <div aria-labelledby="create-tabs-optional" aria-orientation="vertical" className="scax-create-tabs__list" role="tablist">
            {tabButton("checklist", "체크리스트", optionalTabs)}
            {tabButton("links", "업무 연결", optionalTabs)}
            {/* 자료는 **두 갈래 모두** 선다 — 다만 요청 갈래는 아직 저장되지 않고, 그 사실을 판이 말한다. */}
            {tabButton("materials", "자료", optionalTabs)}
          </div>
        </div>

        <div className="scax-create-tabs__panels">
          {panel(
            "basic",
            <>
              <div className="scax-field">
                {/* `.scax-field__label` 이 `display:flex`(=블록)라 라벨이 한 줄을 통째로 먹고 별표가 다음 줄로
                    내려가 있었다. 감싸는 줄을 flex 로 세워 둘이 나란히 선다 —
                    **별표는 라벨 «밖»에 그대로 둔다**: 안으로 넣으면 접근 이름이 「요청할 업무 *」로 바뀐다. */}
                <span className="scax-field__label-row">
                  <label className="scax-field__label" htmlFor={titleInputId}>{kind === "task" ? "업무 제목" : "요청할 업무"}</label>
                  {/* 별표는 눈으로만 읽히는 표시다 — 필수라는 사실은 입력칸의 `aria-required` 가 진다 */}
                  <span aria-hidden className="danger-text">*</span>
                </span>
                <input
                  aria-required="true"
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

              {/*
                * **상태 · 요청자 카드는 여기 없다** (WORK-003 정정).
                *
                * 둘 다 **생성 입력값이 아니다** — 요청자는 서버가 «지금 부르는 사람» 으로 기록하고,
                * 상태는 서버가 「판단 대기」로 세운다. 폼이 그것을 카드로 내면 고칠 수 있는 값처럼
                * 읽히고, 실제로는 `status`·`requester_id` 어느 쪽도 payload 에 실리지 않는다.
                *
                * 상태와 요청자를 **보여 주는** 자리는 그대로다 — 요청 상세 모달과 수신함 카드다.
                * 거기서는 서버가 정한 값을 읽어 내는 것이라 말이 된다.
                */}

              {/* 날짜 둘은 한 줄이다 — 시작과 끝은 함께 읽힌다. 요청 갈래에는 시작일이 없어서
                  (서버가 받지 않는다) 한 칸이 되고, 그때는 빈 칸을 남기지 않는다. */}
              {/* **요청 갈래에도 시작일이 선다** (SPEC-001 U-6-a) — 요청 생성 입력은 원래 이 값을
                  받고 있었고 화면이 접고 있었을 뿐이다. 그래서 두 갈래가 같은 두 칸을 쓴다. */}
              <div className="scax-field-row">
                {(
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor="new-task-start">시작일</label>
                    <DateField
                      formatMonth={formatMonthLong}
                      labels={datePickerLabel}
                      today={seoulToday()}
                      weekdayNames={weekdayNames}
                      hideLabel
                      id="new-task-start"
                      label="시작일"
                      onChange={setStartDate}
                      value={startDate}
                    />
                  </div>
                )}
                <div className="scax-field">
                  <label className="scax-field__label" htmlFor="new-task-due">{kind === "task" ? "마감일" : "희망 기한"}</label>
                  <DateField
                    formatMonth={formatMonthLong}
                    labels={datePickerLabel}
                    today={seoulToday()}
                    weekdayNames={weekdayNames}
                    hideLabel
                    id="new-task-due"
                    label={kind === "task" ? "마감일" : "희망 기한"}
                    onChange={setDueDate}
                    value={dueDate}
                  />
                </div>
              </div>

              {/* 확정 프레임 4 — 요청 갈래의 차례는 «무엇을 · 언제까지 · 누구에게» 다: 담당 후보는
                  두 날짜 «뒤» 에 선다. 내 업무에는 이 칸이 아예 없다(서버가 현재 사용자를 담당자로 기록한다). */}
              {kind === "request" && (
                <div className="scax-field">
                  <span className="scax-field__label-row">
                    <label className="scax-field__label" htmlFor="work-request-assignee">담당 후보</label>
                    <span aria-hidden className="danger-text">*</span>
                  </span>
                  <Select
                    emptyActionLabel={emptyActionLabel.filter}
                    labels={selectLabel}
                    disabled={assigneeCandidates.length === 0}
                    id="work-request-assignee"
                    label="담당 후보"
                    onChange={setAssigneeId}
                    options={assigneeCandidates.map((candidate) => ({ value: candidate.id, label: candidate.display_name }))}
                    // 고를 사람이 있는데 아직 안 고른 것과, 고를 사람이 아예 없는 것은 다른 말이다.
                    placeholder={assigneeCandidates.length === 0 ? "요청 가능한 동료가 없습니다." : undefined}
                    value={assigneeId}
                  />
                </div>
              )}

              <div className="scax-field">
                <label className="scax-field__label" htmlFor="new-task-description">{kind === "task" ? "업무 내용" : "요청 내용"}</label>
                {/*
                  * 시안의 `Composer` — 테두리 상자 + 글자 수 (7-A).
                  *
                  * **세기만 하고 막지 않는다.** 시안은 200자에서 입력을 끊지만 그 제한은 **계약에 없고**
                  * 서버도 더 긴 내용을 받는다. 화면이 스스로 상한을 만들면 적던 글이 조용히 잘린다 —
                  * 그래서 넘어가면 «넘었다» 고 말하기만 한다.
                  */}
                <div className={description.length >= COMPOSER_HINT_LENGTH ? "scax-composer scax-composer--full" : "scax-composer"}>
                  <textarea
                    className="scax-composer__input scax-scroll"
                    id="new-task-description"
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder={kind === "task" ? "무엇을, 왜, 어디까지 할지 적어 두세요." : "상대가 판단할 수 있게 배경과 기대 결과를 적어 주세요."}
                    value={description}
                  />
                  <span className="scax-composer__count">
                    {description.length}/{COMPOSER_HINT_LENGTH}
                  </span>
                </div>
              </div>

              {/*
                * 참조자 — 여럿 고르고 **두 갈래 모두 저장된다** (SPEC-001 U-6-a).
                *
                * 「내 업무로 만들면 함께 저장되지 않습니다」라고 적어 둔 때가 있었다 — 그때는 업무
                * 생성 payload 에 참조 축이 없었다. `cc_member_ids` 가 공통 한 벌로 묶이면서 그 문장은
                * **사실이 아니게** 됐고, 사실이 아닌 안내는 고른 값을 버리는 것만큼 나쁘다.
                */}
              {ccCandidates.length > 0 && (
                <fieldset className="scax-field cc-picker">
                  <legend>참조자</legend>
                  <ChipRow>
                    {ccCandidates
                      .filter((candidate) => candidate.id !== assigneeId)
                      .map((candidate) => (
                        <ChipToggle
                          checked={ccIds.includes(candidate.id)}
                          key={candidate.id}
                          onChange={(next) => setCcIds((current) => (next ? [...current, candidate.id] : current.filter((id) => id !== candidate.id)))}
                        >
                          {personName(candidate.display_name)}
                        </ChipToggle>
                      ))}
                  </ChipRow>
                  <p className="t-meta">참조자는 읽고 논의할 수 있지만 판단하지 않습니다. 두 갈래 모두 함께 저장됩니다.</p>
                </fieldset>
              )}

              {/*
                * 결재자 — 계약 이름은 **승인자**(`approver_id`)다 (SPEC-001 §4 · OQ-N).
                *
                * **두 갈래 모두 저장된다** (WORK-003 정정). 같은 칸이 갈래마다 다른 일을 하던
                * 시절이 끝났으므로, 「이 갈래에서는 저장되지 않습니다」라는 안내도 함께 걷는다.
                */}
              {approverCandidates.length > 0 && (
                <div className="scax-field">
                  <label className="scax-field__label" htmlFor="new-task-approver">결재자</label>
                  <Select
                    emptyActionLabel={emptyActionLabel.filter}
                    labels={selectLabel}
                    id="new-task-approver"
                    label="결재자"
                    onChange={(value) => setApproverId(value ?? "")}
                    options={approverCandidates.map((candidate) => ({ value: candidate.id, label: personName(candidate.display_name) }))}
                    placeholder="결재자 고르기"
                    value={approverId}
                  />
                  <FieldMessage
                    help={
                      kind === "task"
                        ? "담당자 본인은 결재자가 될 수 없습니다. 승인 대기 뒤에는 바꿀 수 없습니다."
                        : "상대가 수락해 업무가 서면 이 사람이 그 업무의 결재자가 됩니다."
                    }
                  />
                </div>
              )}
            </>,
          )}

          {panel(
            "checklist",
            <fieldset aria-label="시작 단계" className="scax-field cc-picker">
              <legend>체크리스트</legend>
              {steps.length > 0 && (
                <ul className="checklist">
                  {steps.map((step, index) => (
                    <li className="scax-checklist__row" key={`${step}-${index}`}>
                      <span>{step}</span>
                      <Button variant="text" size="sm" aria-label={`${step} 빼기`} onClick={() => setSteps((current) => current.filter((_, position) => position !== index))}
                        type="button"
                      >
                        빼기
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="scax-create-add-row">
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
                <Button size="sm" disabled={!newStep.trim()} onClick={appendStep} type="button">
                  단계 추가
                </Button>
              </div>
              <p className="t-meta">
                {kind === "task"
                  ? "지금 아는 단계만 적어도 됩니다. 나중에 업무 상세에서 더할 수 있습니다."
                  : "여기 적은 단계는 그 사람의 업무에 체크리스트로 그대로 섭니다."}
              </p>
            </fieldset>,
          )}

          {panel(
            "links",
            /* 최종 발주 3: 이 판의 제목·라벨은 **한 벌의 글자**다 — `legend`(참고·선행)와
               `.scax-field__label`(상위·프로젝트)이 각자 다른 크기·굵기로 서면 같은 줄의 두 칸이
               서로 다른 층으로 읽힌다. 기준은 선행 업무 제목이고, 그 값을 이 통이 걸어 준다. */
            <div className="scax-links-fields">
              {/*
                * 새 발주 3 — 판의 차례가 곧 **일의 차례**다: 무엇 «아래» 이고 어느 «묶음» 인가(한 행 2열)
                * → 무엇과 «함께» 읽히는가(참고) → 무엇 «다음» 인가(선행).
                *
                * 선행이 맨 뒤인 이유는 그것이 **프로젝트에 매달리기 때문**이다 — 묶음을 먼저 정해야
                * 고를 것이 생긴다.
                */}
              <div className="scax-field-row">
                <div className="scax-field">
                  <label className="scax-field__label" htmlFor="new-task-parent">상위 업무</label>
                  {/*
                    * **하나만 고르는 자리는 표가 아니라 셀렉터다** (수정 발주).
                    *
                    * 이 칸은 한 행 2열의 왼쪽에 서고 오른쪽에는 프로젝트 셀렉터가 있다. 여기에 표를
                    * 넣으면 같은 줄의 두 칸이 세 곱절로 어긋나 「한 행」으로 읽히지 않는다. 대신
                    * 팝오버 목록 안에서 **프로젝트 · 담당자**를 한 줄로 딸려 보여 준다 — 같은 제목이
                    * 여럿일 때 무엇으로 갈리는지는 여전히 필요하다(`SelectOption.description`).
                    *
                    * 여는 쪽이 정해 준 상위는 고치지 못한다: 어느 업무 아래에 매달 것인지는 그 화면이
                    * 이미 정했고, 여기서 바꾸면 그 화면이 보낸 뜻과 어긋난다.
                    */}
                  <Select
                    emptyActionLabel={emptyActionLabel.filter}
                    labels={selectLabel}
                    disabled={Boolean(initial?.parentTaskId)}
                    id="new-task-parent"
                    label="상위 업무"
                    onChange={(value) => setParentTaskId(!value || value === NO_PARENT ? "" : value)}
                    options={[
                      /* 잘못 고른 뒤 되돌릴 자리 — 고르기는 «되돌릴 수 있어야» 고르는 일이 된다. */
                      { value: NO_PARENT, label: "상위 업무 없음" },
                      ...parentChoices.map((choice) => ({
                        value: choice.task_id,
                        label: choice.title,
                        description: `${projectNameOf(choice.project_id) ?? "프로젝트 없음"} · ${
                          choice.assignee ? personName(choice.assignee.display_name) : "담당자 없음"
                        }`,
                      })),
                    ]}
                    placeholder="상위 업무 선택"
                    value={parentTaskId}
                  />
                  {initial?.parentTaskId && (
                    <p className="t-meta">
                      {initial?.supersedesRequestId
                        ? "이전 요청을 잇는 다시 요청입니다 — 새 요청·새 업무가 서고 이전 기록은 남습니다."
                        : "상위 업무 아래의 하위 요청입니다."}
                    </p>
                  )}
                </div>

                <div className="scax-field">
                  <label className="scax-field__label" htmlFor="new-task-project">프로젝트</label>
                  <Select
                    emptyActionLabel={emptyActionLabel.filter}
                    labels={selectLabel}
                    id="new-task-project"
                    label="프로젝트"
                    onChange={(value) => setProjectId(value ?? "")}
                    /* **선행이 남아 있으면 프로젝트를 바꾸지 못한다** (SPEC-001 U-13 · §4
                       `WORK_PROJECT_LOCKED_BY_PREDECESSORS`). 바꾸게 두면 고른 선행이 다른 묶음의
                       업무가 되어 서버가 422 로 거절한다 — 누른 뒤에 막지 않는다. */
                    disabled={precedingTaskIds.length > 0}
                    options={availableProjects.map((project) => ({ value: project.project_id, label: project.name }))}
                    placeholder={availableProjects.length === 0 ? "참여 중인 프로젝트가 없습니다." : undefined}
                    value={projectId}
                  />
                  {precedingTaskIds.length > 0 && <FieldMessage help={projectLockedByPredecessorsText} />}
                </div>
              </div>

              {/*
                * 참고 업무 — **여럿 고르고 `reference_task_ids` 로 실려 나간다**
                * (WORK-003 정정 · DB `task_references` · DEC-001 D-16).
                *
                * 한때 이 판에서 내려 「업무 상세에서 달라」고 했던 자리다. 관계도 계약도 그대로
                * 살아 있었으므로 **만들 때 고를 수 있어야 한다** — 만들고 나서야 이을 수 있으면
                * 고르는 일이 두 걸음으로 갈린다.
                *
                * **선행 업무와 다른 관계다.** 선행은 「무엇 다음인가」(같은 프로젝트 안, 순서)이고
                * 참고는 「무엇과 함께 읽히는가」(프로젝트를 가리지 않는다)다. 두 관계가 같은 표를
                * 쓰되 후보가 다른 이유가 그것이다 — 참고는 프로젝트를 먼저 고를 필요가 없다.
                */}
              <fieldset aria-label="참고 업무" className="scax-field cc-picker">
                <legend>참고 업무</legend>
                <TaskPickTable
                  emptyText="고를 수 있는 업무가 없습니다."
                  label="참고 업무"
                  onToggle={(taskId, next) =>
                    setLinkedTasks((current) => {
                      if (!next) return current.filter((row) => row.task_id !== taskId);
                      if (current.some((row) => row.task_id === taskId)) return current;
                      const found = (referenceChoices ?? []).find((row) => row.task_id === taskId);
                      return found ? [...current, found] : current;
                    })
                  }
                  projectNameOf={projectNameOf}
                  selected={linkedTasks.map((row) => row.task_id)}
                  tasks={referenceChoices ?? []}
                />
                <p className="t-meta">참고 업무는 함께 읽히는 업무입니다 — 순서를 정하는 선행 업무와 다릅니다.</p>
              </fieldset>

              {/*
                * 선행업무 — **실제로 저장된다** (SPEC-001 U-13 · §4 `preceding_task_ids`).
                *
                * **프로젝트를 먼저 고르게 한다**: 선행은 같은 묶음 안에서만 말이 되는 관계라, 묶음이
                * 없으면 고를 목록 자체가 없다. 빈 표를 내는 대신 무엇이 먼저인지 말한다.
                *
                * 후보에서 **자기 자신과 이미 고른 것을 뺀다** — 서버가 거절하기 전에 화면이 먼저
                * 뺀다(U-4 「누른 뒤에 막지 않는다」). 고른 것은 아래 칩으로 서고 칩마다 「빼기」가 있다.
                */}
              <fieldset aria-label="선행 업무" className="scax-field cc-picker">
                <legend>선행 업무</legend>
                {projectId ? (
                  <>
                    <TaskPickTable
                      emptyText="이 프로젝트에서 고를 수 있는 업무가 없습니다."
                      label="선행 업무"
                      onToggle={(taskId, next) =>
                        setPrecedingTaskIds((current) =>
                          next ? (current.includes(taskId) ? current : [...current, taskId]) : current.filter((id) => id !== taskId),
                        )
                      }
                      projectNameOf={projectNameOf}
                      selected={precedingTaskIds}
                      tasks={precedingChoices}
                    />
                    {precedingTasks.length > 0 && (
                      <ChipRow>
                        {precedingTasks.map((row) => (
                          <Chip
                            key={row.task_id}
                            label={row.title}
                            on
                            onClick={() => setPrecedingTaskIds((current) => current.filter((id) => id !== row.task_id))}
                          />
                        ))}
                      </ChipRow>
                    )}
                  </>
                ) : (
                  <p className="t-meta">프로젝트를 먼저 선택하면 그 프로젝트의 업무 중에서 고를 수 있습니다.</p>
                )}
              </fieldset>
            </div>,
          )}

          {panel(
            "materials",
            /*
             * 자료 판 — **두 갈래가 같은 한 벌을 쓴다** (최종 프레임 6).
             *
             * 파일 고르기와 링크 적기가 갈래를 가리지 않고 같은 자리에 선다. 그래야 토글을 옮겨도
             * 읽던 틀이 흔들리지 않는다.
             *
             * 붙이는 것은 **두 단계다** — 업무가 서야 자료가 매달릴 자리가 생긴다(`task_id`). 그래서
             * 여기서는 «고르기» 까지이고, 실제 업로드는 생성 성공 직후에 일어난다.
             *
             * **두 갈래 모두 실제로 붙는다** (WORK-003): 업무는 `task_id` 로, 요청은 `request_id` 로.
             * 아직 붙일 수 없는 자리(관리자 배정 · 수평 생성 · 회의 승격)에서만 `materialsSaved` 가
             * `false` 이고, 그때는 판 맨 위에서 **왜 저장되지 않는지**를 먼저 말한다 — 고른 것을
             * 조용히 버리지도, 저장된 척하지도 않는다.
             */
            <fieldset aria-label="첨부파일" className="scax-field cc-picker">
              <legend>참고 자료</legend>
              {!materialsSaved && (
                /* 경고 톤이다. 「나중에 붙습니다」라는 안내가 아니라 «지금 이 창에서는 저장되지
                   않는다» 는 사실이라, 고르기 전에 읽혀야 한다. */
                <FieldMessage error={materialsGapText} />
              )}
              <DropZone
                disabled={isWorking || Boolean(created)}
                drop="첨부할 파일을 끌어다 놓거나 추가하세요"
                hint={
                  materialsSaved
                    ? kind === "task"
                      ? "업무를 만든 직후 참고 자료로 붙습니다. 한 건당 25MB."
                      : "요청을 보낸 직후 참고 자료로 함께 붙습니다. 한 건당 25MB."
                    : "한 건당 25MB. 지금은 고르기까지이고 이 요청과 함께 저장되지 않습니다."
                }
                onFiles={(files) => setAttachments((current) => [...current, ...files])}
                pickLabel="파일 추가"
              >
                {attachments.length > 0 && (
                  <FileList
                    label="첨부할 파일"
                    rows={attachments.map((file, index) => ({
                      key: `${file.name}-${index}`,
                      name: file.name,
                      size: formatBytes(file.size),
                      reason: created?.failed.files.includes(file)
                        ? "올리지 못했습니다"
                        : materialsSaved
                          ? null
                          : "저장되지 않습니다",
                      removeLabel: `${file.name} 빼기`,
                      onRemove: created ? undefined : () => setAttachments((current) => current.filter((_, position) => position !== index)),
                    }))}
                  />
                )}
              </DropZone>

              {/*
                * 링크 자료 — **만들기 창에서도 적는다.**
                *
                * 한때 「업무를 만든 뒤 상세에서」라고만 적어 두었다. 그러나 링크도 파일과 똑같이 두
                * 단계로 붙일 수 있고(`POST /api/tasks/{id}/materials/links`), 붙일 수 있는 것을 다른
                * 화면으로 미루면 만드는 일이 두 걸음으로 갈린다. 새 서버 API 는 만들지 않는다.
                */}
              <div className="scax-create-add-row">
                <div className="scax-field">
                  <label className="scax-field__label" htmlFor="new-task-material-link-url">링크 주소</label>
                  <input
                    id="new-task-material-link-url"
                    inputMode="url"
                    onChange={(event) => setLinkDraft((current) => ({ ...current, url: event.target.value }))}
                    placeholder="https://"
                    value={linkDraft.url}
                  />
                </div>
                <div className="scax-field">
                  <label className="scax-field__label" htmlFor="new-task-material-link-label">링크 이름</label>
                  <input
                    id="new-task-material-link-label"
                    onChange={(event) => setLinkDraft((current) => ({ ...current, label: event.target.value }))}
                    placeholder="사람이 읽는 이름"
                    value={linkDraft.label}
                  />
                </div>
                <Button
                  size="sm"
                  /* 주소만 있고 이름이 없으면 목록에 주소가 그대로 서서 읽히지 않는다 — 둘 다 받는다. */
                  disabled={!linkDraft.url.trim() || !linkDraft.label.trim() || isWorking || Boolean(created)}
                  onClick={() => {
                    setMaterialLinks((current) => [...current, { url: linkDraft.url.trim(), label: linkDraft.label.trim() }]);
                    setLinkDraft({ url: "", label: "" });
                  }}
                  type="button"
                >
                  링크 추가
                </Button>
              </div>
              {materialLinks.length > 0 && (
                <FileList
                  label="첨부할 링크"
                  rows={materialLinks.map((link, index) => ({
                    key: `${link.url}-${index}`,
                    name: link.label,
                    size: link.url,
                    reason: created?.failed.links.includes(link)
                      ? "붙이지 못했습니다"
                      : materialsSaved
                        ? null
                        : "저장되지 않습니다",
                    removeLabel: `${link.label} 빼기`,
                    onRemove: created ? undefined : () => setMaterialLinks((current) => current.filter((_, position) => position !== index)),
                  }))}
                />
              )}
            </fieldset>,
          )}
        </div>
      </div>
    </Modal>
  );
}
