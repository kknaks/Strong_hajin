import { useEffect, useRef, useState, type KeyboardEvent, type WheelEvent } from "react";
import { createPortal } from "react-dom";

import { Badge } from "../../ds/Badge";
import { Button, IconButton } from "../../ds/Button";
import { FieldMessage } from "../../ds/FormControls";
import { Modal } from "../../ds/Modal";
import { runActionCommand } from "../../lib/api";
import { Icon } from "../../ds/icons/Icon";
import { axDraftCard, dayDifference, formatDate, isoDateInSeoul, personName, seoulToday } from "../../lib/labels";
import type {
  ActionCommand,
  ActionEditContract,
  ActionEditField,
  ActionItem,
  ActionItemEnvelope,
  ActionMaterialDraft,
  Persona,
  Project,
} from "../../lib/viewModels";
import { CreateWorkModal, ReasonPrompt } from "../work/WorkModals";

/**
 * AX 업무 초안 요약 카드 (SPEC-002 §2.9 · WORK-008 D-02 · A-01).
 *
 * 대상은 두 kind 뿐이다 — `ax.task.create_self`(업무 생성) · `ax.work_request.create`(업무 요청).
 * 카드는 **읽기 전용 요약**이고 고치는 일은 「새 업무 추가」 창(`CreateWorkModal`)이 한다(P-1). 채팅·홈 판단 대기·
 * 내 업무의 「AX 제안」 칩 세 자리가 **같은 카드**를 쓴다 — 어디서 답해도 같은 항목이 바뀐다(§2.4).
 *
 * 값은 서버가 내려 준 편집 계약(`edit_contract.values`·`fields`)에서만 읽는다. 사람 이름·프로젝트 이름도 그
 * 계약의 선택지(`options`)에서 찾는다 — 클라이언트가 추론하지 않는다(S-7 2).
 */

/** 이 카드가 맡는 kind 둘. 채팅의 `action_type` 은 `ax.` 접두사가 없다(서버: `kind = f"ax.{action_type}"`). */
const AX_DRAFT_KINDS = new Set(["ax.task.create_self", "ax.work_request.create"]);

export function isAxDraftKind(kind: string): boolean {
  return AX_DRAFT_KINDS.has(kind);
}

export function isAxDraftAction(action: ActionItem): boolean {
  return action.edit_contract?.editor === "task" && isAxDraftKind(`ax.${action.action_type}`);
}

/** 카드가 읽는 한 벌 — 채팅의 Action 과 판단 대기의 봉투를 같은 모양으로 맞춘다. */
export type AxDraftSource = {
  actionId: string;
  kind: "task" | "request";
  title: string;
  round: number;
  state: "pending" | "approved" | "rejected";
  contract: ActionEditContract;
  materials: ActionMaterialDraft[];
  commands: ActionCommand[];
  createdAt?: string | null;
  resultTaskId?: string | null;
};

function kindOf(type: string): "task" | "request" {
  return type.endsWith("work_request.create") ? "request" : "task";
}

/** 채팅 카드 자리의 Action → 카드. */
export function axDraftFromAction(action: ActionItem): AxDraftSource | null {
  if (!isAxDraftAction(action) || !action.edit_contract) return null;
  const nested = action.result?.task;
  const nestedId = nested && typeof nested === "object" && "task_id" in nested ? (nested as { task_id?: unknown }).task_id : null;
  const resultTaskId = typeof action.result?.task_id === "string" ? action.result.task_id : typeof nestedId === "string" ? nestedId : null;
  return {
    actionId: action.action_id,
    kind: kindOf(action.action_type),
    title: action.subject ?? String(action.edit_contract.values.title ?? action.title),
    round: action.edit_contract.base_submission_version,
    state: action.state,
    contract: action.edit_contract,
    materials: action.material_drafts ?? [],
    commands: action.commands ?? [],
    /* 채팅 뷰도 만든 시각을 싣는다(3b fix1) — 없으면 「만든 지 며칠」을 세우지 않는다. */
    createdAt: action.created_at ?? null,
    resultTaskId,
  };
}

/** 판단 대기 봉투(`GET /api/action-items`) → 카드. 봉투가 초안 필드 전체와 만든 시각을 싣는다(3a). */
export function axDraftFromEnvelope(item: ActionItemEnvelope): AxDraftSource | null {
  if (!isAxDraftKind(item.kind) || item.edit_contract?.editor !== "task") return null;
  return {
    actionId: item.action_item_id,
    kind: kindOf(item.kind),
    title: item.subject,
    round: item.submission_version,
    state: item.status === "resolved" ? (item.derived_task_id ? "approved" : "rejected") : "pending",
    contract: item.edit_contract,
    materials: item.material_drafts ?? [],
    commands: item.allowed_commands,
    createdAt: item.created_at ?? null,
    resultTaskId: item.derived_task_id ?? null,
  };
}

/** 만든 지 며칠 — 서울 날짜로 센다(서버 시각은 tz 포함 ISO 8601). */
export function axDraftAgeDays(createdAt: string | null | undefined, today = seoulToday()): number | null {
  const made = isoDateInSeoul(createdAt ?? null);
  return made ? dayDifference(made, today) : null;
}

const text = (value: unknown): string => (value === null || value === undefined ? "" : String(value).trim());
const list = (value: unknown): string[] => (Array.isArray(value) ? value.map(String).filter(Boolean) : []);

function field(contract: ActionEditContract, id: string): ActionEditField | undefined {
  return contract.fields.find((candidate) => candidate.id === id);
}

/** 값 하나를 사람이 읽는 이름으로 — 그 칸의 선택지에서 찾고, 없으면 계약이 준 표시값, 그것도 없으면 값 그대로. */
function nameOf(contract: ActionEditContract, fieldId: string, value: string): string {
  const owner = field(contract, fieldId);
  const option = owner?.options?.find((candidate) => candidate.value === value);
  const label = option?.label ?? (owner?.value === value ? owner.label_value : undefined) ?? value;
  return owner?.type === "person" || owner?.type === "multi_select" ? personName(label) : label;
}

function people(contract: ActionEditContract, fieldId: string): Persona[] {
  return (field(contract, fieldId)?.options ?? []).map((option) => ({ id: option.value, display_name: option.label }) as Persona);
}

const PAGE_COUNT = axDraftCard.pages.length;

function PageBody({ source, page }: { source: AxDraftSource; page: number }) {
  const { contract, kind } = source;
  const values = contract.values;
  if (page === 0) {
    const start = text(values.start_date);
    const due = text(values.due_date);
    const cc = list(values.cc_member_ids).map((id) => nameOf(contract, "cc_member_ids", id));
    const approver = text(values.approver_id);
    const assignee = text(values.assignee_id);
    const description = text(values.description);
    return (
      <dl className="ax-draft-card__rows">
        <div>
          <dt>{axDraftCard.branch[kind]}</dt>
          <dd>
            {axDraftCard.period} {start ? formatDate(start) : "—"} → {due ? formatDate(due) : "—"}
          </dd>
        </div>
        {kind === "request" && (
          <div>
            <dt>{axDraftCard.assignee}</dt>
            <dd>{assignee ? nameOf(contract, "assignee_id", assignee) : "—"}</dd>
          </div>
        )}
        {description && (
          <div>
            <dt>{axDraftCard.description}</dt>
            <dd className="ax-draft-card__clamp">{description}</dd>
          </div>
        )}
        {cc.length > 0 && (
          <div>
            <dt>{axDraftCard.cc}</dt>
            <dd>{cc.join(", ")}</dd>
          </div>
        )}
        {approver && (
          <div>
            <dt>{axDraftCard.approver}</dt>
            <dd>{nameOf(contract, "approver_id", approver)}</dd>
          </div>
        )}
      </dl>
    );
  }
  if (page === 1) {
    const steps = list(values.checklist);
    return steps.length === 0 ? <p className="ax-draft-card__none">{axDraftCard.none}</p> : <p>{axDraftCard.checklist(steps.length, steps[0])}</p>;
  }
  if (page === 2) {
    const parent = text(values.parent_task_id);
    const project = text(values.project_id);
    const references = list(values.reference_task_ids).length;
    const preceding = list(values.preceding_task_ids).length;
    const parts = [
      parent ? `${axDraftCard.parent} ${nameOf(contract, "parent_task_id", parent)}` : null,
      project ? `${axDraftCard.project} ${nameOf(contract, "project_id", project)}` : null,
      references > 0 ? axDraftCard.references(references) : null,
      preceding > 0 ? axDraftCard.preceding(preceding) : null,
    ].filter((part): part is string => Boolean(part));
    return parts.length === 0 ? <p className="ax-draft-card__none">{axDraftCard.none}</p> : <p>{parts.join(" · ")}</p>;
  }
  const files = source.materials.filter((item) => item.source_kind === "file").length;
  const links = source.materials.length - files;
  return source.materials.length === 0 ? <p className="ax-draft-card__none">{axDraftCard.none}</p> : <p>{axDraftCard.materials(files, links)}</p>;
}

export function AxDraftCard({
  source,
  onCommand,
  onOpenTask,
  locked = false,
}: {
  source: AxDraftSource;
  /** 명령을 보낸다. 확인(`confirm`)은 `base_submission_version`·`draft`·`attachment_draft_ids` 를 싣는다. */
  onCommand: (commandId: string, payload?: Record<string, unknown>) => Promise<void>;
  onOpenTask?: (taskId: string) => void;
  /** 기억한 봉투로 그린 카드 — 그 화면의 갱신 응답 전에는 명령을 열지 않는다 (WORK-008 Phase 2). */
  locked?: boolean;
}) {
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<ActionCommand | null>(null);
  const swipe = useRef({ distance: 0, at: 0 });
  const { contract, kind } = source;
  /* 자료 초안 — 수정 창에서 붙이고 빼면 서버에 바로 남으므로 카드의 요약도 그 값을 따른다(3b fix1 · WARN-1).
     봉투가 새로 오면(목록을 다시 읽으면) 그 값이 이긴다. */
  const [materials, setMaterials] = useState<ActionMaterialDraft[]>(source.materials);
  useEffect(() => setMaterials(source.materials), [source.materials]);
  const view: AxDraftSource = { ...source, materials };
  const confirm = source.commands.find((command) => command.id === "confirm");
  const reject = source.commands.find((command) => command.id === "reject");
  /* [수정]은 명령이 아니라 편집 계약이 있을 때 선다 — 고칠 수 있는 칸이 하나라도 있어야 한다(§2.9). */
  const editable = source.state === "pending" && contract.fields.some((candidate) => candidate.editable);
  const staged = materials.filter((item) => item.state === "staged").map((item) => item.material_draft_id);
  const ageDays = axDraftAgeDays(source.createdAt);
  const go = (next: number) => setPage(Math.max(0, Math.min(PAGE_COUNT - 1, next)));

  async function send(commandId: string, payload?: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await onCommand(commandId, payload);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 초안을 처리하지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const confirmPayload = (draft?: Record<string, unknown>, attachmentIds: string[] = staged) => ({
    base_submission_version: contract.base_submission_version,
    ...(draft ? { draft } : {}),
    ...(attachmentIds.length > 0 ? { attachment_draft_ids: attachmentIds } : {}),
  });

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      go(page - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      go(page + 1);
    }
  }

  /* 트랙패드 좌우 스와이프 — 가로 움직임이 세로보다 크고 일정 거리를 넘을 때 한 장 넘긴다. */
  function onWheel(event: WheelEvent<HTMLDivElement>) {
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
    const now = Date.now();
    if (now - swipe.current.at > 300) swipe.current.distance = 0;
    swipe.current.at = now;
    swipe.current.distance += event.deltaX;
    if (Math.abs(swipe.current.distance) < 40) return;
    const direction = swipe.current.distance > 0 ? 1 : -1;
    swipe.current.distance = 0;
    go(page + direction);
  }

  if (source.state === "approved") {
    /* 등록 뒤 — 한 줄 요약(제목 · 기한 · 담당)과 [업무 열기]로 접힌다(§2.9). */
    const due = text(contract.values.due_date);
    const assignee = text(contract.values.assignee_id);
    return (
      <section className="scax-actioncard ax-draft-card ax-draft-card--done" data-action-id={source.actionId} data-state="approved">
        <p className="ax-draft-card__line">
          <Badge tone="info">{axDraftCard.registered}</Badge>
          <b>{source.title}</b>
          <span>{due ? formatDate(due) : "—"}</span>
          {assignee && <span>{nameOf(contract, "assignee_id", assignee)}</span>}
        </p>
        {source.resultTaskId && onOpenTask && (
          <Button onClick={() => onOpenTask(source.resultTaskId!)} size="sm" type="button">
            {axDraftCard.openTask}
          </Button>
        )}
      </section>
    );
  }

  return (
    <section
      aria-label={`${axDraftCard.badge} ${axDraftCard.kind[kind]} · ${source.title}`}
      className="scax-actioncard ax-draft-card"
      data-action-id={source.actionId}
      data-page={page}
      data-state={source.state}
    >
      <header className="ax-draft-card__head">
        <div className="scax-actioncard__badges">
          <Badge tone="info">{axDraftCard.badge}</Badge>
          <Badge tone="neutral">{source.state === "rejected" ? axDraftCard.rejected : axDraftCard.round(source.round)}</Badge>
          <span className="ax-draft-card__kind">{axDraftCard.kind[kind]}</span>
        </div>
        <b className="ax-draft-card__title">{source.title}</b>
        <div aria-label="초안 페이지" className="ax-draft-card__bar" role="tablist">
          {axDraftCard.pages.map((name, index) => (
            <button
              aria-label={name}
              aria-selected={index === page}
              className={index === page ? "ax-draft-card__step ax-draft-card__step--on" : "ax-draft-card__step"}
              key={name}
              onClick={() => go(index)}
              role="tab"
              type="button"
            />
          ))}
        </div>
        <span className="ax-draft-card__page">{axDraftCard.pages[page]}</span>
      </header>
      <div
        aria-label={axDraftCard.pages[page]}
        className="ax-draft-card__body"
        onKeyDown={onKeyDown}
        onWheel={onWheel}
        role="tabpanel"
        tabIndex={0}
      >
        {page > 0 && (
          <span className="ax-draft-card__nav ax-draft-card__nav--prev">
            <IconButton label={axDraftCard.previous} name="chevron-left" onClick={() => go(page - 1)} size={16} />
          </span>
        )}
        <PageBody page={page} source={view} />
        {page < PAGE_COUNT - 1 && (
          <span className="ax-draft-card__nav ax-draft-card__nav--next">
            <IconButton label={axDraftCard.next} name="chevron-right" onClick={() => go(page + 1)} size={16} />
          </span>
        )}
      </div>
      {error && <FieldMessage error={error} />}
      {source.state === "pending" && (
        <footer className="ax-draft-card__foot">
          {ageDays !== null && <span className="t-meta">{axDraftCard.age(ageDays)}</span>}
          {reject && (
            <Button
              disabled={busy || locked}
              onClick={() => (reject.requires_reason ? setRejecting(reject) : void send(reject.id))}
              size="sm"
              type="button"
            >
              {axDraftCard.reject}
            </Button>
          )}
          {editable && confirm && (
            <Button
              disabled={busy || locked}
              onClick={() => {
                setEditError(null);
                setEditing(true);
              }}
              size="sm"
              type="button"
            >
              <Icon name="pencil" size={14} />
              {axDraftCard.edit}
            </Button>
          )}
          {confirm && (
            <Button disabled={busy || locked} onClick={() => void send(confirm.id, confirmPayload())} size="sm" tone="primary" type="button" variant="solid">
              {busy ? axDraftCard.confirming : axDraftCard.confirm}
            </Button>
          )}
        </footer>
      )}
      {editing &&
        confirm &&
        createPortal(
          /* [수정] = AI 초안이 채워진 「새 업무 추가」 창 그대로(§2.9). 닫으면 고친 것을 버리고 카드는 초안 그대로다. */
          <CreateWorkModal
            assigneeCandidates={kind === "request" ? people(contract, "assignee_id") : []}
            axDraft={{
              kind,
              actionId: source.actionId,
              baseValues: contract.values,
              materials,
              onMaterialsChange: setMaterials,
              approverOptions: people(contract, "approver_id"),
              error: editError,
              /* 실패는 창이 받아 창 안에 낸다(`onError` → `error`) — 쓰던 값은 남는다. */
              onSubmit: async (draft, attachmentIds) => {
                await onCommand(confirm.id, confirmPayload(draft, attachmentIds));
                setEditing(false);
              },
            }}
            canCreateRequest={kind === "request"}
            canCreateTask={kind === "task"}
            ccCandidates={people(contract, "cc_member_ids")}
            initial={{
              title: text(contract.values.title),
              description: text(contract.values.description) || null,
              dueDate: text(contract.values.due_date) || null,
              startDate: text(contract.values.start_date),
              checklist: list(contract.values.checklist),
              assigneeId: kind === "request" ? text(contract.values.assignee_id) || undefined : undefined,
              parentTaskId: text(contract.values.parent_task_id) || undefined,
              projectId: text(contract.values.project_id),
              ccIds: list(contract.values.cc_member_ids),
              approverId: text(contract.values.approver_id),
              precedingTaskIds: list(contract.values.preceding_task_ids),
              referenceTaskIds: list(contract.values.reference_task_ids),
            }}
            onClose={() => setEditing(false)}
            onCreated={() => undefined}
            onError={setEditError}
            ownerName=""
            projectCandidates={(field(contract, "project_id")?.options ?? []).map((option) => ({ project_id: option.value, name: option.label }) as Project)}
          />,
          document.body,
        )}
      {rejecting && (
        <ReasonPrompt
          busy={busy}
          confirmLabel={axDraftCard.reject}
          danger
          fieldLabel={axDraftCard.rejectField}
          heading={axDraftCard.rejectTitle}
          label={axDraftCard.rejectTitle}
          onClose={() => setRejecting(null)}
          onSubmit={async (reason) => {
            if (await send(rejecting.id, { reason })) setRejecting(null);
            else return false;
          }}
        />
      )}
    </section>
  );
}

/**
 * 판단 대기·「AX 제안」 칩에서 여는 **같은 요약 카드** (SPEC-002 §2.4). 카드를 DS 작은 모달에 담는다.
 *
 * 명령은 판단 원장 입구(`POST /api/action-items/{id}/commands/{command}`)로 간다 — 채팅의 같은 항목과 같은
 * 기록이 바뀐다. 끝나면 부르는 쪽이 목록을 다시 읽고(`onDone`) 이 창은 닫힌다.
 */
export function AxDraftModal({
  item,
  onClose,
  onDone,
  onError,
  onNotice,
  onOpenTask,
  locked = false,
}: {
  item: ActionItemEnvelope;
  onClose: () => void;
  onDone: () => Promise<unknown> | void;
  onError?: (message: string | null) => void;
  onNotice?: (message: string) => void;
  onOpenTask?: (taskId: string) => void;
  locked?: boolean;
}) {
  const source = axDraftFromEnvelope(item);
  if (!source) return null;
  return (
    <Modal closeLabel="닫기" label={axDraftCard.modalTitle} onClose={onClose} size="sm" title={axDraftCard.modalTitle}>
      <AxDraftCard
        locked={locked}
        onCommand={async (commandId, payload) => {
          await runActionCommand(item.action_item_id, commandId, { expected_version: item.expected_version, ...payload });
          onError?.(null);
          await onDone();
          onNotice?.(axDraftCard.decided(item.subject));
          onClose();
        }}
        onOpenTask={onOpenTask}
        source={source}
      />
    </Modal>
  );
}
