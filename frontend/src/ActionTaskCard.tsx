import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { actionSubject } from "./ActionPreview";
import { discardActionMaterialDraft, stageActionMaterialFile, stageActionMaterialLink } from "./api";
import { DateField } from "./DateField";
import { formatDate, formatDateTime } from "./labels";
import { Icon } from "./Icon";
import { useEscape } from "./Modal";
import { useActionDraft } from "./useActionDraft";
import type { ActionEditContract, ActionEditField, ActionEditOption, ActionItem, ActionMaterialDraft } from "./viewModels";

export type TaskDraft = {
  title: string;
  description: string | null;
  start_date: string | null;
  due_date: string | null;
  checklist: string[];
  reference_task_ids: string[];
  parent_task_id: string | null;
  project_id: string | null;
  assignee_id?: string | null;
  cc_member_ids?: string[];
};

function taskPreviewValue(kind: string, value: string): string {
  if (kind === "date") return formatDate(value).replaceAll("/", ".");
  if (kind === "datetime") return formatDateTime(value);
  if (kind === "person") return personDisplayName(value);
  return value;
}

function personDisplayName(value: unknown): string {
  return String(value ?? "").replace(/\s*\([^()]*\)\s*$/, "").trim();
}

function taskChecklist(action: ActionItem): string[] {
  const values = action.edit_contract?.values.checklist;
  if (Array.isArray(values)) return values.map(String).map((value) => value.trim()).filter(Boolean);
  const preview = action.preview?.find((row) => row.id === "checklist")?.value.trim();
  if (!preview) return [];
  return preview.replace(/^\d+단계\s*·\s*/, "").split(/\s*→\s*/).map((value) => value.trim()).filter(Boolean);
}

function TaskSummary({ action }: { action: ActionItem }) {
  const description = action.preview?.find((row) => row.id === "description");
  const visibleRowIds = action.action_type === "task.assign" || action.action_type === "work_request.create"
    ? new Set(["assignee", "requester", "due_date"])
    : new Set(["assignee", "due_date"]);
  const rows = (action.preview ?? []).filter((row) => visibleRowIds.has(row.id) && row.value.trim());
  const checklist = taskChecklist(action);
  return (
    <div className="action-task-summary">
      <b>{actionSubject(action)}</b>
      {description?.value && <p>{description.value}</p>}
      {(rows.length > 0 || checklist.length > 0) && (
        <dl>
          {rows.map((row) => (
            <div key={row.id}>
              <dt>{row.id === "assignee" && action.action_type !== "work_request.create" ? "담당자" : row.label}</dt>
              <dd>{taskPreviewValue(row.kind, row.value)}</dd>
            </div>
          ))}
          {checklist.length > 0 && (
            <div className="action-task-summary-checklist">
              <dt>체크리스트</dt>
              <dd>
                <ul aria-label="체크리스트 항목">
                  {checklist.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
                </ul>
              </dd>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

function taskDraft(values: Record<string, unknown>): TaskDraft {
  return {
    title: String(values.title ?? ""),
    description: values.description ? String(values.description) : null,
    start_date: values.start_date ? String(values.start_date) : null,
    due_date: values.due_date ? String(values.due_date) : null,
    checklist: Array.isArray(values.checklist) ? values.checklist.map(String) : [],
    reference_task_ids: Array.isArray(values.reference_task_ids) ? values.reference_task_ids.map(String) : [],
    parent_task_id: values.parent_task_id ? String(values.parent_task_id) : null,
    project_id: values.project_id ? String(values.project_id) : null,
    ...(values.assignee_id ? { assignee_id: String(values.assignee_id) } : {}),
    ...(Object.hasOwn(values, "cc_member_ids")
      ? { cc_member_ids: Array.isArray(values.cc_member_ids) ? values.cc_member_ids.map(String) : [] }
      : {}),
  };
}

function submittedDraft(contract: ActionEditContract, draft: TaskDraft, workRequest: boolean): Record<string, unknown> {
  if (!workRequest) return draft;
  return Object.fromEntries(contract.fields.map((field) => [field.id, draft[field.id as keyof TaskDraft]]));
}

function replace(draft: TaskDraft, field: string, value: unknown): TaskDraft {
  return { ...draft, [field]: value };
}

export function TaskDraftFields({
  contract,
  draft,
  disabled,
  hideReferenceField = false,
  onChange,
}: {
  contract: ActionEditContract;
  draft: TaskDraft;
  disabled?: boolean;
  hideReferenceField?: boolean;
  onChange: (next: TaskDraft) => void;
}) {
  const [newItems, setNewItems] = useState<Record<string, string>>({});

  function fieldControl(field: ActionEditField) {
    const id = `action-task-${field.id}`;
    if (!field.editable) {
      const value = field.label_value ?? field.value ?? "—";
      return <output id={id}>{field.type === "person" ? personDisplayName(value) : value}</output>;
    }
    if (field.type === "date") {
      return (
        <DateField
          disabled={disabled}
          displaySeparator="."
          id={id}
          label={field.label}
          onChange={(value) => onChange(replace(draft, field.id, value || null))}
          pickerIcon="chevron-down"
          required={field.required}
          value={String(draft[field.id as keyof TaskDraft] ?? "")}
        />
      );
    }
    if (field.type === "textarea") {
      return (
        <textarea
          aria-label={field.label}
          disabled={disabled}
          id={id}
          onChange={(event) => onChange(replace(draft, field.id, event.target.value || null))}
          value={String(draft[field.id as keyof TaskDraft] ?? "")}
        />
      );
    }
    if (field.type === "select") {
      return (
        <select
          aria-label={field.label}
          disabled={disabled}
          id={id}
          onChange={(event) => onChange(replace(draft, field.id, event.target.value || null))}
          value={String(draft[field.id as keyof TaskDraft] ?? "")}
        >
          <option value="">선택 안 함</option>
          {(field.options ?? []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      );
    }
    if (field.type === "multi_select") {
      const current = draft[field.id as keyof TaskDraft];
      const selected = Array.isArray(current) ? current.map(String) : [];
      return (
        <div className="action-task-options" id={id}>
          {(field.options ?? []).length === 0 ? <small>연결할 수 있는 업무가 없습니다.</small> : (field.options ?? []).map((option) => (
            <label key={option.value}>
              <input
                checked={selected.includes(option.value)}
                disabled={disabled}
                onChange={(event) => onChange(replace(
                  draft,
                  field.id,
                  event.target.checked ? [...selected, option.value] : selected.filter((value) => value !== option.value),
                ))}
                type="checkbox"
              />
              {option.label}
            </label>
          ))}
        </div>
      );
    }
    if (field.type === "string_list") {
      const items = draft.checklist;
      const pending = newItems[field.id] ?? "";
      return (
        <div className="action-task-list" id={id}>
          {items.length > 0 && <ul>{items.map((item, index) => (
            <li key={`${item}-${index}`}>
              <span>{item}</span>
              <button
                aria-label={`${item} 빼기`}
                className="btn h30 ghost"
                disabled={disabled}
                onClick={() => onChange(replace(draft, field.id, items.filter((_, position) => position !== index)))}
                type="button"
              >
                빼기
              </button>
            </li>
          ))}</ul>}
          <div className="action-task-add-row">
            <input
              aria-label="추가할 단계"
              disabled={disabled}
              onChange={(event) => setNewItems((current) => ({ ...current, [field.id]: event.target.value }))}
              value={pending}
            />
            <button
              className="btn h30"
              disabled={disabled || !pending.trim()}
              onClick={() => {
                if (!pending.trim()) return;
                onChange(replace(draft, field.id, [...items, pending.trim()]));
                setNewItems((current) => ({ ...current, [field.id]: "" }));
              }}
              type="button"
            >
              단계 추가
            </button>
          </div>
        </div>
      );
    }
    return (
      <input
        aria-label={field.label}
        disabled={disabled}
        id={id}
        onChange={(event) => onChange(replace(draft, field.id, event.target.value || ""))}
        required={field.required}
        type="text"
        value={String(draft[field.id as keyof TaskDraft] ?? "")}
      />
    );
  }

  return (
    <div className="action-task-fields">
      {contract.fields.filter((field) => !hideReferenceField || field.id !== "reference_task_ids").map((field) => {
        const label = <>{field.label}{field.required && <span aria-hidden className="danger-text"> *</span>}</>;
        if (field.type === "date") {
          return (
            <div className="action-task-field date" data-field-id={field.id} key={field.id}>
              {fieldControl(field)}
            </div>
          );
        }
        if (field.type === "string_list" || field.type === "multi_select") {
          return (
            <fieldset aria-label={field.label} className={`action-task-field ${field.type}`} data-field-id={field.id} key={field.id}>
              <legend>{label}</legend>
              {fieldControl(field)}
            </fieldset>
          );
        }
        return (
          <div className={`action-task-field ${field.type}`} data-field-id={field.id} key={field.id}>
            <label htmlFor={`action-task-${field.id}`}>{label}</label>
            {fieldControl(field)}
          </div>
        );
      })}
    </div>
  );
}

export function ActionTaskCard({
  action,
  onCommand,
  onOpenTask,
  principalId = "",
}: {
  action: ActionItem;
  onCommand: (commandId: string, payload?: Record<string, unknown>) => Promise<void>;
  onOpenTask?: (taskId: string) => void;
  principalId?: string;
}) {
  const contract = action.edit_contract?.editor === "task" ? action.edit_contract : null;
  const workRequest = action.action_type === "work_request.create";
  const base = useMemo(() => taskDraft(contract?.values ?? {}), [contract]);
  const recovered = useActionDraft<TaskDraft>({
    principalId,
    actionId: action.action_id,
    baseSubmissionVersion: contract?.base_submission_version ?? 0,
    baseDraft: base,
    pending: action.state === "pending",
    sanitize: taskDraft,
  });
  const draft = recovered.draft;
  const [editing, setEditing] = useState(recovered.restored);
  const [startAttachmentPicker, setStartAttachmentPicker] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [materials, setMaterials] = useState<ActionMaterialDraft[]>(action.material_drafts ?? []);
  const locallyStagedMaterialIds = useRef(new Set<string>());
  const [transfers, setTransfers] = useState<LocalMaterialTransfer[]>([]);
  const cardRef = useRef<HTMLElement>(null);
  const focusAfterOpening = useRef<string | null>(null);
  const nestedTask = action.result?.task;
  const nestedTaskId = nestedTask && typeof nestedTask === "object" && "task_id" in nestedTask
    ? (nestedTask as { task_id?: unknown }).task_id
    : null;
  const taskId = typeof action.result?.task_id === "string"
    ? action.result.task_id
    : typeof nestedTaskId === "string"
      ? nestedTaskId
      : null;
  const requestId = typeof action.result?.request_id === "string" ? action.result.request_id : null;
  const requested = action.action_type === "task.assign" || workRequest;
  const awaitingAssignee = requested && (action.result?.status === "pending" || action.result?.state === "pending");
  const pendingAssignment = action.action_type === "task.assign" && action.state === "pending";
  const requestTarget = action.preview?.find((row) => row.id === "assignee");
  const requestTargetName = requestTarget ? personDisplayName(requestTarget.value) : undefined;
  const confirm = action.commands?.find((command) => command.id === "confirm");
  const changed = JSON.stringify(draft) !== JSON.stringify(base);
  const incompleteMaterial = transfers.some((item) => item.state === "uploading" || item.state === "failed");

  useEffect(() => {
    const projected = action.material_drafts ?? [];
    setMaterials((current) => {
      const projectedIds = new Set(projected.map((item) => item.material_draft_id));
      const awaitingProjection = current.filter((item) => (
        locallyStagedMaterialIds.current.has(item.material_draft_id)
        && !projectedIds.has(item.material_draft_id)
      ));
      projectedIds.forEach((id) => locallyStagedMaterialIds.current.delete(id));
      return [...projected, ...awaitingProjection];
    });
  }, [action.material_drafts]);

  useEffect(() => {
    if (action.state !== "pending") setEditing(false);
  }, [action.state]);

  useEffect(() => {
    if (!editing) return;
    const field = focusAfterOpening.current ?? "title";
    focusAfterOpening.current = null;
    focusField(field);
  }, [editing]);

  function focusField(fieldId: string) {
    cardRef.current?.querySelector<HTMLElement>(`#action-task-${fieldId}`)?.focus();
  }

  function validationIssue(): { field: string; message: string } | null {
    for (const field of contract?.fields ?? []) {
      if (!field.editable || !field.required) continue;
      const value = draft[field.id as keyof TaskDraft];
      if (value === null || value === undefined || (typeof value === "string" && !value.trim())) {
        return { field: field.id, message: `${field.label}을(를) 입력해 주세요.` };
      }
    }
    if (draft.start_date && draft.due_date && draft.start_date > draft.due_date) {
      return { field: "due_date", message: "기한은 시작일보다 빠를 수 없습니다." };
    }
    return null;
  }

  function fieldFromError(message: string): string | null {
    if (/제목|title/i.test(message)) return "title";
    if (/담당|assignee/i.test(message)) return "assignee_id";
    if (/시작|start_date/i.test(message)) return "start_date";
    if (/기한|due_date|schedule/i.test(message)) return "due_date";
    if (/project|프로젝트/i.test(message)) return "project_id";
    if (/reference|참고 업무/i.test(message)) return "reference_task_ids";
    return null;
  }

  async function run(commandId: string, payload?: Record<string, unknown>, clearDraft = false) {
    setBusy(true);
    setError(null);
    try {
      await onCommand(commandId, payload);
      if (clearDraft) recovered.clear();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "업무 제안을 처리하지 못했습니다.";
      setError(/stale/i.test(message) ? "AX 초안이 갱신되었습니다. 수정한 내용은 유지했으니 최신안을 확인한 뒤 다시 시도해 주세요." : message);
      const field = fieldFromError(message);
      if (field) focusField(field);
    } finally {
      setBusy(false);
    }
  }

  async function addLink(url: string, label: string) {
    setError(null);
    try {
      const staged = await stageActionMaterialLink(action.action_id, { url, label });
      locallyStagedMaterialIds.current.add(staged.material_draft_id);
      setMaterials((current) => [...current.filter((item) => item.material_draft_id !== staged.material_draft_id), staged]);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "링크를 첨부하지 못했습니다.";
      setError(message);
      throw reason;
    }
  }

  async function addFile(file: File) {
    const localId = `upload-${Date.now()}-${file.name}`;
    setTransfers((current) => [...current, { id: localId, name: file.name, state: "uploading" }]);
    setError(null);
    try {
      const staged = await stageActionMaterialFile(action.action_id, file);
      locallyStagedMaterialIds.current.add(staged.material_draft_id);
      setTransfers((current) => current.filter((item) => item.id !== localId));
      setMaterials((current) => [...current.filter((item) => item.material_draft_id !== staged.material_draft_id), staged]);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "파일을 업로드하지 못했습니다.";
      setTransfers((current) => current.map((item) => item.id === localId ? { ...item, state: "failed", error: message } : item));
      setError(message);
    }
  }

  async function removeMaterial(materialDraftId: string) {
    setError(null);
    try {
      await discardActionMaterialDraft(action.action_id, materialDraftId);
      locallyStagedMaterialIds.current.delete(materialDraftId);
      setMaterials((current) => current.filter((item) => item.material_draft_id !== materialDraftId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "첨부를 제외하지 못했습니다.");
    }
  }

  const statusBadge = action.state === "pending"
    ? pendingAssignment ? { label: "요청", tone: "requested" } : { label: "초안", tone: "draft" }
    : awaitingAssignee
      ? { label: "요청", tone: "requested" }
      : requested && action.result?.status === "cancelled"
        ? { label: "취소됨", tone: "rejected" }
        : requested && action.result?.status === "declined"
          ? { label: "거절됨", tone: "rejected" }
          : requested && action.result?.status === "active"
            ? { label: "수락됨", tone: "approved" }
            : taskId
              ? { label: "내 업무", tone: "task" }
              : action.state === "approved"
                ? { label: "승인됨", tone: "approved" }
                : { label: "거절됨", tone: "rejected" };

  return (
    <>
      {pendingAssignment && requestTarget?.value && (
        <div aria-label="요청 대상" className="action-task-request-target">
          <span aria-hidden className="avatar sm">{requestTargetName?.slice(0, 1)}</span>
          <span>{requestTargetName}</span>
        </div>
      )}
      <section className="ax-action-card action-task-card" data-action-id={action.action_id} data-state={action.state} data-view={editing ? "editing" : action.state} ref={cardRef}>
      <div className="action-task-content">
        <div className="action-task-badges">
          <span className="ax-card-kicker">SC AX</span>
          <span className={`action-task-state-badge ${statusBadge.tone}`}>{statusBadge.label}</span>
        </div>
        {editing && contract ? (
          <>
            {recovered.stale && (
              <StaleTaskDraft
                contract={contract}
                latest={base}
                local={recovered.stale.draft}
                onContinue={recovered.continueWithLocal}
                onRestart={recovered.startFromLatest}
              />
            )}
            <TaskDraftFields
              contract={contract}
              disabled={busy || Boolean(recovered.stale)}
              draft={draft}
              hideReferenceField
              onChange={recovered.setDraft}
            />
            <TaskAttachmentGroup
              contract={contract}
              disabled={busy || Boolean(recovered.stale)}
              draft={draft}
              editable
              materials={materials}
              onAddFile={addFile}
              onAddLink={addLink}
              onChangeDraft={recovered.setDraft}
              onRemoveMaterial={removeMaterial}
              onRemoveTransfer={(id) => setTransfers((current) => current.filter((item) => item.id !== id))}
              onStartedAdding={() => setStartAttachmentPicker(false)}
              referencesOnly={workRequest}
              startAdding={startAttachmentPicker}
              transfers={transfers}
            />
          </>
        ) : (
          <>
            <TaskSummary action={action} />
            {contract && (
              <TaskAttachmentGroup
                contract={contract}
                disabled={busy}
                draft={base}
                materials={materials}
                onAddFile={addFile}
                onAddLink={addLink}
                onChangeDraft={() => undefined}
                onRemoveMaterial={removeMaterial}
                onRemoveTransfer={() => undefined}
                onStartAdding={action.state === "pending" ? () => {
                  setStartAttachmentPicker(true);
                  setEditing(true);
                } : undefined}
                referencesOnly={workRequest}
                transfers={[]}
              />
            )}
          </>
        )}
        {error && <p className="action-task-error" role="alert">{error}</p>}
      </div>
      <div className="action-task-actions">
        {action.state === "pending" && contract && !editing && (
          <button className="btn h30 ghost" disabled={busy} onClick={() => setEditing(true)} type="button">수정</button>
        )}
        {action.state === "pending" && contract && editing && (
          <>
            <button className="btn h30 ghost action-task-cancel" disabled={busy} onClick={() => {
              recovered.reset();
              setError(null);
              setStartAttachmentPicker(false);
              setEditing(false);
            }} type="button">취소</button>
            <button className="btn h30 ghost action-task-reset" disabled={busy} onClick={recovered.reset} type="button"><Icon name="refresh" size={14} />초기화</button>
          </>
        )}
        {confirm && contract && (
          <button
            className="btn h30 primary"
            disabled={busy || Boolean(recovered.stale) || incompleteMaterial}
            onClick={() => {
              const issue = validationIssue();
              if (issue) {
                setError(issue.message);
                if (editing) focusField(issue.field);
                else {
                  focusAfterOpening.current = issue.field;
                  setEditing(true);
                }
                return;
              }
              void run(confirm.id, {
                base_submission_version: contract.base_submission_version,
                ...(editing && changed ? { draft: submittedDraft(contract, draft, workRequest) } : {}),
                ...(materials.some((item) => item.state === "staged")
                  ? { attachment_draft_ids: materials.filter((item) => item.state === "staged").map((item) => item.material_draft_id) }
                  : {}),
              }, true);
            }}
            type="button"
          >
            {busy ? (editing ? "저장 중…" : "등록 중…") : editing ? "저장" : "등록"}
          </button>
        )}
        {(action.commands ?? []).filter((command) => !["confirm", "reject"].includes(command.id)).map((command) => (
          <button
            className={`btn h30 ${command.tone === "danger" ? "danger" : "ghost"}`}
            disabled={busy}
            key={command.id}
            onClick={() => void run(command.id, undefined, command.id === "reject")}
            type="button"
          >
            {command.label}
          </button>
        ))}
        {taskId && onOpenTask && (
          <button className="btn h30" onClick={() => onOpenTask(taskId)} type="button">
            {requested ? "요청내용 보기" : "업무 상세보기"}
          </button>
        )}
      </div>
      </section>
      {action.action_type === "task.create_self" && action.state === "approved" && taskId && (
        <p className="action-task-completion-note" role="status">업무가 등록되었습니다.</p>
      )}
      {requested && action.state === "approved" && (taskId || requestId) && (
        <p className="action-task-completion-note request" role="status">
          {awaitingAssignee ? (
            <>
              <span>{requestTargetName ?? "담당자"}님에게 업무가 요청되었습니다.</span>
              <span>상대방이 요청을 확인하기 전까지 취소할 수 있어요.</span>
            </>
          ) : workRequest ? (
            <span>{requestTargetName ?? "담당자"}님에게 업무 요청을 보냈습니다.</span>
          ) : action.result?.status === "cancelled" ? (
            <span>업무 요청을 취소했습니다.</span>
          ) : action.result?.status === "declined" ? (
            <span>{requestTargetName ?? "담당자"}님이 업무 요청을 거절했습니다.</span>
          ) : (
            <span>{requestTargetName ?? "담당자"}님이 업무 요청을 수락했습니다.</span>
          )}
        </p>
      )}
    </>
  );
}

export type LocalMaterialTransfer = {
  id: string;
  name: string;
  state: "uploading" | "failed";
  error?: string;
};

export function TaskAttachmentGroup<TDraft extends { reference_task_ids: string[] }>({
  contract,
  draft,
  materials,
  transfers,
  editable = false,
  removable = editable,
  disabled,
  onChangeDraft,
  onAddLink,
  onAddFile,
  onRemoveMaterial,
  onRemoveTransfer,
  onStartAdding,
  onStartedAdding,
  startAdding = false,
  showClaimStatus = true,
  referencesOnly = false,
  searchReferences,
}: {
  contract: ActionEditContract;
  draft: TDraft;
  materials: ActionMaterialDraft[];
  transfers: LocalMaterialTransfer[];
  editable?: boolean;
  removable?: boolean;
  disabled?: boolean;
  onChangeDraft: (draft: TDraft) => void;
  onAddLink: (url: string, label: string) => Promise<void>;
  onAddFile: (file: File) => Promise<void>;
  onRemoveMaterial: (id: string) => Promise<void>;
  onRemoveTransfer: (id: string) => void;
  onStartAdding?: () => void;
  onStartedAdding?: () => void;
  startAdding?: boolean;
  showClaimStatus?: boolean;
  referencesOnly?: boolean;
  searchReferences?: (query: string) => Promise<ActionEditOption[]>;
}) {
  const [adding, setAdding] = useState<"task" | "link" | "file" | null>(null);
  const [linkUrl, setLinkUrl] = useState("");
  const [linkLabel, setLinkLabel] = useState("");
  const [linkBusy, setLinkBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const referenceField = contract.fields.find((field) => field.id === "reference_task_ids");
  const options = useMemo(() => referenceField?.options ?? [], [referenceField]);
  const labels = new Map(options.map((option) => [option.value, option.label]));
  const selectedReferences = draft.reference_task_ids.map((id) => ({ id, label: labels.get(id) ?? "볼 수 없는 업무" }));
  const empty = selectedReferences.length === 0 && materials.length === 0 && transfers.length === 0;

  useEffect(() => {
    if (!editable || !startAdding) return;
    setAdding("task");
    onStartedAdding?.();
  }, [editable, onStartedAdding, startAdding]);

  const closePicker = () => {
    setAdding(null);
    queueMicrotask(() => triggerRef.current?.focus());
  };

  const submitLink = async () => {
    if (!linkUrl.trim() || !linkLabel.trim()) return;
    setLinkBusy(true);
    try {
      await onAddLink(linkUrl, linkLabel);
      setLinkUrl("");
      setLinkLabel("");
      closePicker();
    } catch {
      // The card-level alert keeps the server message and the form stays open for correction.
    } finally {
      setLinkBusy(false);
    }
  };

  return (
    <section aria-label={referencesOnly ? "참고 업무" : "첨부"} className="action-task-attachments">
      <div className="action-task-attachment-heading">
        <b>{referencesOnly ? "참고 업무" : "첨부"}</b>
      </div>
      {!empty && (
        <ul className="action-task-attachment-list">
          {selectedReferences.map((reference) => (
            <li data-attachment-kind="task" key={`task-${reference.id}`}>
              <span aria-hidden><Icon name="folder" size={16} /></span><span>{reference.label}</span>
              {removable && <button aria-label={`${reference.label} 첨부 제외`} disabled={disabled} onClick={() => onChangeDraft({
                ...draft,
                reference_task_ids: draft.reference_task_ids.filter((id) => id !== reference.id),
              })} type="button"><Icon name="close" size={14} /></button>}
            </li>
          ))}
          {materials.map((material) => (
            <li data-attachment-kind={material.source_kind} key={material.material_draft_id}>
              <span aria-hidden><Icon name={material.source_kind === "file" ? "file" : "link"} size={16} /></span>
              <span>
                {material.name}
                {showClaimStatus && material.state === "claimed" && <em>연결됨</em>}
              </span>
              {removable && material.state === "staged" && (
                <button aria-label={`${material.name} 첨부 제외`} disabled={disabled} onClick={() => void onRemoveMaterial(material.material_draft_id)} type="button"><Icon name="close" size={14} /></button>
              )}
            </li>
          ))}
          {transfers.map((transfer) => (
            <li data-attachment-kind="file" data-upload-state={transfer.state} key={transfer.id}>
              <span aria-hidden><Icon name="file" size={16} /></span>
              <span>{transfer.name}<em>{transfer.state === "uploading" ? "업로드 중…" : `실패 · ${transfer.error ?? "다시 시도해 주세요"}`}</em></span>
              {transfer.state === "failed" && <button aria-label={`${transfer.name} 실패 항목 제외`} onClick={() => onRemoveTransfer(transfer.id)} type="button"><Icon name="close" size={14} /></button>}
            </li>
          ))}
        </ul>
      )}
      {(editable || onStartAdding) && (
        <button
          aria-expanded={editable ? adding !== null : undefined}
          className={`action-task-attachment-trigger${empty ? " empty" : ""}`}
          disabled={disabled}
          onClick={() => {
            if (editable) setAdding((current) => current === null ? "task" : null);
            else onStartAdding?.();
          }}
          ref={triggerRef}
          type="button"
        >
          <Icon name="plus" size={16} />
          {referencesOnly ? "참고 업무 연결" : "업무나 자료 첨부"}
        </button>
      )}
      {editable && adding !== null && createPortal(
        <AttachmentPickerModal
          disabled={disabled}
          initialMode={adding}
          linkBusy={linkBusy}
          linkLabel={linkLabel}
          linkUrl={linkUrl}
          onAddFile={(file) => {
            void onAddFile(file);
            closePicker();
          }}
          onChangeLinkLabel={setLinkLabel}
          onChangeLinkUrl={setLinkUrl}
          onClose={closePicker}
          onConfirm={(ids) => {
            onChangeDraft({ ...draft, reference_task_ids: [...new Set([...draft.reference_task_ids, ...ids])] });
            closePicker();
          }}
          onModeChange={setAdding}
          onSubmitLink={() => void submitLink()}
          options={options}
          referencesOnly={referencesOnly}
          searchReferences={searchReferences}
          selectedIds={draft.reference_task_ids}
          subtitle={referencesOnly
            ? "요청에 참고할 업무를 찾아 연결하세요."
            : contract.editor === "meeting"
              ? "회의에 필요한 자료를 찾아 연결하세요."
              : "업무에 필요한 자료를 찾아 연결하세요."}
        />,
        document.body,
      )}
    </section>
  );
}

function AttachmentPickerModal({
  options,
  selectedIds,
  referencesOnly,
  subtitle,
  initialMode,
  disabled,
  linkBusy,
  linkUrl,
  linkLabel,
  searchReferences,
  onModeChange,
  onChangeLinkUrl,
  onChangeLinkLabel,
  onSubmitLink,
  onAddFile,
  onConfirm,
  onClose,
}: {
  options: ActionEditOption[];
  selectedIds: string[];
  referencesOnly: boolean;
  subtitle: string;
  initialMode: "task" | "link" | "file";
  disabled?: boolean;
  linkBusy: boolean;
  linkUrl: string;
  linkLabel: string;
  searchReferences?: (query: string) => Promise<ActionEditOption[]>;
  onModeChange: (mode: "task" | "link" | "file") => void;
  onChangeLinkUrl: (value: string) => void;
  onChangeLinkLabel: (value: string) => void;
  onSubmitLink: () => void;
  onAddFile: (file: File) => void;
  onConfirm: (ids: string[]) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ActionEditOption[]>([]);
  const [searchState, setSearchState] = useState<"idle" | "searching" | "ready" | "failed">("idle");
  const [pending, setPending] = useState<string[]>([]);
  const requestSequence = useRef(0);
  const searchRef = useRef<HTMLInputElement>(null);
  useEscape(onClose);

  useEffect(() => {
    if (initialMode === "task") searchRef.current?.focus();
  }, [initialMode]);

  useEffect(() => {
    const needle = query.trim();
    const sequence = ++requestSequence.current;
    if (!needle) {
      setResults([]);
      setSearchState("idle");
      return;
    }
    setSearchState("searching");
    const lookup = searchReferences ?? (async (value: string) => {
      const lowered = value.toLocaleLowerCase();
      return options.filter((option) => option.label.toLocaleLowerCase().includes(lowered));
    });
    void lookup(needle).then((found) => {
      if (sequence !== requestSequence.current || !query.trim()) return;
      setResults(found);
      setSearchState("ready");
    }).catch(() => {
      if (sequence !== requestSequence.current || !query.trim()) return;
      setResults([]);
      setSearchState("failed");
    });
  }, [options, query, searchReferences]);

  const toggle = (id: string) => setPending((current) => (
    current.includes(id) ? current.filter((value) => value !== id) : [...current, id]
  ));

  return (
    <div className="modal-backdrop attachment-picker-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section aria-label={referencesOnly ? "참고 업무 연결" : "업무나 자료 첨부"} aria-modal="true" className="modal attachment-picker-modal" role="dialog">
        <header className="attachment-picker-head">
          <div className="attachment-picker-title">
            <div><h3>{referencesOnly ? "참고 업무 연결" : "업무나 자료 첨부"}</h3><p>{subtitle}</p></div>
            <button aria-label="첨부 선택 닫기" className="modal-close" onClick={onClose} type="button"><Icon name="close" /></button>
          </div>
          <div aria-label="첨부 방법" className="attachment-picker-tabs" role="tablist">
            {(referencesOnly ? (["task"] as const) : (["task", "link", "file"] as const)).map((mode) => (
              <button aria-selected={initialMode === mode} key={mode} onClick={() => onModeChange(mode)} role="tab" type="button">
                {mode === "task" ? "검색" : mode === "link" ? "링크 추가" : "파일 추가"}
              </button>
            ))}
          </div>
        </header>
        <div className="attachment-picker-body">
          {initialMode === "task" && (
            <>
              <label className="attachment-picker-search">
                <Icon name="search" />
                <input
                  aria-label="업무나 자료 검색"
                  autoComplete="off"
                  disabled={disabled}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="제목이나 키워드로 검색"
                  ref={searchRef}
                  value={query}
                />
                {query && <button aria-label="검색어 지우기" onClick={() => setQuery("")} type="button"><Icon name="close" size={14} /></button>}
              </label>
              <div aria-live="polite" className="attachment-picker-results">
                {searchState === "idle" && <AttachmentSearchEmpty title={referencesOnly ? "찾고 싶은 업무를 검색하세요" : "찾고 싶은 업무나 자료를 검색하세요"} detail="검색한 결과만 여기에 표시됩니다." />}
                {searchState === "searching" && <AttachmentSearchEmpty title="검색 중…" detail="권한이 있는 항목만 확인하고 있습니다." />}
                {searchState === "failed" && <AttachmentSearchEmpty title="검색하지 못했습니다" detail="잠시 후 다시 입력해 주세요." />}
                {searchState === "ready" && results.length === 0 && <AttachmentSearchEmpty title="검색 결과가 없습니다" detail={referencesOnly ? "다른 키워드로 검색해 보세요." : "다른 키워드로 검색하거나 링크·파일을 직접 추가해 보세요."} />}
                {searchState === "ready" && results.length > 0 && (
                  <>
                    <div className="attachment-picker-result-title"><span>검색 결과 {results.length}개</span><span>여러 항목 선택 가능</span></div>
                    {results.map((option) => {
                      const attached = selectedIds.includes(option.value);
                      const picked = pending.includes(option.value);
                      return (
                        <button
                          aria-pressed={picked}
                          className="attachment-picker-result"
                          disabled={attached}
                          key={option.value}
                          onClick={() => toggle(option.value)}
                          type="button"
                        >
                          <span className="attachment-picker-result-icon"><Icon name="folder" /></span>
                          <span><b>{option.label}</b><small>업무{attached ? " · 이미 첨부됨" : ""}</small></span>
                          <span className="attachment-picker-check"><Icon name="check" size={12} /></span>
                        </button>
                      );
                    })}
                  </>
                )}
              </div>
            </>
          )}
          {initialMode === "link" && (
            <div className="attachment-picker-form">
              <label>링크 주소<input aria-label="링크 주소" disabled={disabled || linkBusy} onChange={(event) => onChangeLinkUrl(event.target.value)} placeholder="https://" value={linkUrl} /></label>
              <label>표시할 이름<input aria-label="링크 이름" disabled={disabled || linkBusy} onChange={(event) => onChangeLinkLabel(event.target.value)} placeholder="예: SC AX 디자인 가이드" value={linkLabel} /></label>
              <button className="btn h40" disabled={disabled || linkBusy || !linkUrl.trim() || !linkLabel.trim()} onClick={onSubmitLink} type="button">링크 추가</button>
            </div>
          )}
          {initialMode === "file" && (
            <label className="attachment-picker-upload">
              <Icon name="arrow-up" size={20} />
              <b>파일을 끌어놓거나 선택하세요</b>
              <span>파일 선택</span>
              <input aria-label="첨부할 파일" disabled={disabled} onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onAddFile(file);
                event.target.value = "";
              }} type="file" />
            </label>
          )}
        </div>
        {pending.length > 0 && (
          <div className="attachment-picker-selection">
            <small>선택한 항목 {pending.length}개</small>
            <div>{pending.map((id) => {
              const option = [...results, ...options].find((candidate) => candidate.value === id);
              return <button key={id} onClick={() => toggle(id)} type="button">{option?.label ?? id}<Icon name="close" size={12} /></button>;
            })}</div>
          </div>
        )}
        <footer className="attachment-picker-foot">
          <span>필요한 항목을 여러 개 선택하세요.</span>
          <div><button className="btn h40 ghost" onClick={onClose} type="button">취소</button><button className="btn h40 primary" disabled={pending.length === 0} onClick={() => onConfirm(pending)} type="button">첨부</button></div>
        </footer>
      </section>
    </div>
  );
}

function AttachmentSearchEmpty({ title, detail }: { title: string; detail: string }) {
  return <div className="attachment-picker-empty"><span><Icon name="search" /></span><b>{title}</b><p>{detail}</p></div>;
}

function draftValue(value: unknown): string {
  if (Array.isArray(value)) return value.length > 0 ? value.map(String).join(", ") : "비어 있음";
  if (value === null || value === undefined || value === "") return "비어 있음";
  return String(value);
}

function StaleTaskDraft({
  contract,
  local,
  latest,
  onContinue,
  onRestart,
}: {
  contract: ActionEditContract;
  local: TaskDraft;
  latest: TaskDraft;
  onContinue: () => void;
  onRestart: () => void;
}) {
  const differences = contract.fields.flatMap((field) => {
    const localValue = local[field.id as keyof TaskDraft];
    const latestValue = latest[field.id as keyof TaskDraft];
    return JSON.stringify(localValue) === JSON.stringify(latestValue)
      ? []
      : [{ id: field.id, label: field.label, local: draftValue(localValue), latest: draftValue(latestValue) }];
  });
  return (
    <div aria-label="저장 초안 충돌" className="action-task-stale" role="status">
      <b>AX 제안이 새 버전으로 갱신되었습니다.</b>
      <p>저장된 초안은 자동으로 합치지 않았습니다. 차이를 확인하고 다시 시작할 기준을 선택해 주세요.</p>
      {differences.length > 0 && (
        <dl>
          {differences.map((difference) => (
            <div key={difference.id}>
              <dt>{difference.label}</dt>
              <dd>내 초안: {difference.local}</dd>
              <dd>최신안: {difference.latest}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="action-task-actions">
        <button className="btn h30" onClick={onRestart} type="button">최신안으로 다시 시작</button>
        <button className="btn h30 ghost" onClick={onContinue} type="button">내 초안으로 다시 편집</button>
      </div>
    </div>
  );
}
