import { useEffect, useMemo, useRef, useState } from "react";

import { actionSubject } from "./ActionPreview";
import { discardActionMaterialDraft, stageActionMaterialFile, stageActionMaterialLink } from "./api";
import { DateField } from "./DateField";
import { formatDateTime } from "./labels";
import { Icon } from "./Icon";
import { MultiSelect } from "./Select";
import { TaskAttachmentGroup, type LocalMaterialTransfer } from "./ActionTaskCard";
import { TimeRangeField } from "./TimeField";
import { useActionDraft } from "./useActionDraft";
import type { ActionEditContract, ActionItem, ActionMaterialDraft } from "./viewModels";


export type MeetingDraft = {
  organization_id: string;
  title: string;
  description: string | null;
  starts_at: string;
  ends_at: string;
  visibility: "public" | "private";
  attendee_ids: string[];
  reference_task_ids: string[];
  include_initial_note: boolean;
  initial_note_body: string | null;
};

type SourceEvidence = {
  source_type?: unknown;
  source_id?: unknown;
  label?: unknown;
  excerpt?: unknown;
};

function localDateTime(value: unknown): string {
  if (!value) return "";
  const instant = new Date(String(value));
  if (Number.isNaN(instant.valueOf())) return "";
  const shifted = new Date(instant.valueOf() - instant.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function isoDateTime(value: string): string {
  return new Date(value).toISOString();
}

function datePart(value: string): string {
  return value.slice(0, 10);
}

function timePart(value: string): string {
  return value.slice(11, 16);
}

function addDays(value: string, amount: number): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
}

function meetingDraft(values: Record<string, unknown>): MeetingDraft {
  return {
    organization_id: String(values.organization_id ?? ""),
    title: String(values.title ?? ""),
    description: values.description ? String(values.description) : null,
    starts_at: localDateTime(values.starts_at),
    ends_at: localDateTime(values.ends_at),
    visibility: values.visibility === "public" ? "public" : "private",
    attendee_ids: Array.isArray(values.attendee_ids) ? values.attendee_ids.map(String) : [],
    reference_task_ids: Array.isArray(values.reference_task_ids) ? values.reference_task_ids.map(String) : [],
    include_initial_note: values.include_initial_note === true,
    initial_note_body: values.initial_note_body ? String(values.initial_note_body) : null,
  };
}

function MeetingSummary({ action }: { action: ActionItem }) {
  const preview = new Map((action.preview ?? []).map((row) => [row.id, row]));
  const description = preview.get("description")?.value;
  const startsAt = preview.get("starts_at")?.value;
  const endsAt = preview.get("ends_at")?.value;
  const startParts = startsAt ? formatDateTime(startsAt).split(" ") : [];
  const endParts = endsAt ? formatDateTime(endsAt).split(" ") : [];
  const rows = [
    ["주최자", preview.get("host")?.value],
    ["참석자", preview.get("attendees")?.value ?? "없음"],
    ["날짜", startParts[0]?.replaceAll("/", ".")],
    ["시간", startParts[1] && endParts[1] ? `${startParts[1]} - ${endParts[1]}` : undefined],
  ].filter((row): row is [string, string] => Boolean(row[1]));
  return (
    <div className="action-task-summary action-meeting-summary">
      <b>{actionSubject(action)}</b>
      {description && <p>{description}</p>}
      <dl>
        {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
    </div>
  );
}

function commandDraft(draft: MeetingDraft): MeetingDraft {
  return {
    ...draft,
    starts_at: isoDateTime(draft.starts_at),
    ends_at: isoDateTime(draft.ends_at),
    initial_note_body: draft.include_initial_note ? draft.initial_note_body : null,
  };
}

export function ActionMeetingCard({
  action,
  onCommand,
  onOpenMeeting,
  principalId = "",
}: {
  action: ActionItem;
  onCommand: (commandId: string, payload?: Record<string, unknown>) => Promise<void>;
  onOpenMeeting?: (meetingId: string) => void;
  principalId?: string;
}) {
  const contract = action.edit_contract?.editor === "meeting" ? action.edit_contract : null;
  const base = useMemo(() => meetingDraft(contract?.values ?? {}), [contract]);
  const recovered = useActionDraft<MeetingDraft>({
    principalId,
    actionId: action.action_id,
    baseSubmissionVersion: contract?.base_submission_version ?? 0,
    baseDraft: base,
    pending: action.state === "pending",
    sanitize: meetingDraft,
  });
  const [editing, setEditing] = useState(recovered.restored);
  const [noteBodyOpen, setNoteBodyOpen] = useState(false);
  const [startAttachmentPicker, setStartAttachmentPicker] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [materials, setMaterials] = useState<ActionMaterialDraft[]>(action.material_drafts ?? []);
  const locallyStagedMaterialIds = useRef(new Set<string>());
  const [transfers, setTransfers] = useState<LocalMaterialTransfer[]>([]);
  const cardRef = useRef<HTMLElement>(null);
  const confirm = action.commands?.find((command) => command.id === "confirm");
  const changed = JSON.stringify(recovered.draft) !== JSON.stringify(base);
  const incompleteMaterial = transfers.some((item) => item.state === "uploading" || item.state === "failed");
  const meetingId = meetingResultId(action.result);
  const sourceStatus = String(contract?.values.initial_note_source_status ?? "not_requested");
  const noteIncluded = recovered.draft.include_initial_note;
  const sources = Array.isArray(contract?.values.initial_note_source_evidence)
    ? contract.values.initial_note_source_evidence as SourceEvidence[]
    : [];

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
    if (action.state !== "pending") {
      setEditing(false);
      setNoteBodyOpen(false);
    }
  }, [action.state]);

  useEffect(() => {
    if (editing) focusField("title");
  }, [editing]);

  function focusField(field: string) {
    cardRef.current?.querySelector<HTMLElement>(`#action-meeting-${field}`)?.focus();
  }

  function issue(): { field: string; message: string } | null {
    for (const field of contract?.fields ?? []) {
      if (!field.editable || !field.required) continue;
      const value = recovered.draft[field.id as keyof MeetingDraft];
      if (value === null || value === undefined || (typeof value === "string" && !value.trim())) {
        return { field: field.id, message: `${field.label}을(를) 입력해 주세요.` };
      }
    }
    if (recovered.draft.starts_at && recovered.draft.ends_at
      && new Date(recovered.draft.starts_at) >= new Date(recovered.draft.ends_at)) {
      return { field: "ends_at", message: "종료 시각은 시작 시각보다 늦어야 합니다." };
    }
    if (recovered.draft.include_initial_note && !recovered.draft.initial_note_body?.trim()) {
      return { field: "initial_note_body", message: "포함할 회의록 초안 내용을 입력해 주세요." };
    }
    return null;
  }

  async function run(command: string, payload?: Record<string, unknown>, clear = false) {
    setBusy(true);
    setError(null);
    try {
      await onCommand(command, payload);
      if (clear) recovered.clear();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "회의 제안을 처리하지 못했습니다.";
      setError(/stale/i.test(message)
        ? "AX 초안이 갱신되었습니다. 수정한 내용은 유지했으니 최신안을 확인한 뒤 다시 시도해 주세요."
        : message);
      const field = /참석|attendee/i.test(message) ? "attendee_ids"
        : /종료|end/i.test(message) ? "ends_at"
        : /시작|start/i.test(message) ? "starts_at"
        : /조직|organization/i.test(message) ? "organization_id"
        : /회의록|note/i.test(message) ? "initial_note_body"
        : /제목|title/i.test(message) ? "title"
        : null;
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
      setError(reason instanceof Error ? reason.message : "링크를 첨부하지 못했습니다.");
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

  return (
    <section className="ax-action-card action-task-card action-meeting-card" data-action-id={action.action_id} data-state={action.state} data-view={editing ? "editing" : action.state} ref={cardRef}>
      <div className="action-task-content">
        <div className="action-task-badges">
          <span className="ax-card-kicker">회의</span>
        </div>
        {editing && contract ? (
          <>
            {recovered.stale && (
              <div className="action-task-stale" role="status">
                <b>AX 제안이 새 버전으로 갱신되었습니다.</b>
                <p>수정한 회의 초안은 보존했습니다. 최신안을 기준으로 다시 시작하거나 내 초안을 이어서 편집하세요.</p>
                <div className="action-task-actions">
                  <button className="btn h30" onClick={recovered.startFromLatest} type="button">최신안으로 다시 시작</button>
                  <button className="btn h30 ghost" onClick={recovered.continueWithLocal} type="button">내 초안으로 다시 편집</button>
                </div>
              </div>
            )}
            <MeetingDraftFields
              contract={contract}
              disabled={busy || Boolean(recovered.stale)}
              draft={recovered.draft}
              onChange={recovered.setDraft}
            />
            <TaskAttachmentGroup
              contract={contract}
              disabled={busy || Boolean(recovered.stale)}
              draft={recovered.draft}
              editable
              materials={materials}
              onAddFile={addFile}
              onAddLink={addLink}
              onChangeDraft={recovered.setDraft}
              onRemoveMaterial={removeMaterial}
              onRemoveTransfer={(id) => setTransfers((current) => current.filter((item) => item.id !== id))}
              onStartedAdding={() => setStartAttachmentPicker(false)}
              startAdding={startAttachmentPicker}
              transfers={transfers}
            />
          </>
        ) : (
          <>
            <MeetingSummary action={action} />
            {contract && (
              <TaskAttachmentGroup
                contract={contract}
                disabled={busy}
                draft={recovered.draft}
                materials={materials}
                onAddFile={addFile}
                onAddLink={addLink}
                onChangeDraft={recovered.setDraft}
                onRemoveMaterial={removeMaterial}
                onRemoveTransfer={() => undefined}
                removable={action.state === "pending"}
                onStartAdding={action.state === "pending" ? () => {
                  setStartAttachmentPicker(true);
                  setEditing(true);
                } : undefined}
                showClaimStatus={action.state !== "approved"}
                transfers={[]}
              />
            )}
          </>
        )}
        {(noteIncluded || (action.state === "pending" && contract)) && (
          <section className="action-meeting-note-section">
            <b>회의록</b>
            {!noteIncluded && action.state === "pending" && contract && (
              <button aria-label="회의록 추가" className="action-task-attachment-trigger" disabled={busy} onClick={() => {
                recovered.setDraft({ ...recovered.draft, include_initial_note: true });
                setNoteBodyOpen(true);
                setEditing(true);
              }} type="button">＋ 회의록 추가</button>
            )}
            {noteIncluded && (
              <ul className="action-task-attachment-list action-meeting-note-list">
                <li data-attachment-kind="note">
                  <span aria-hidden><Icon name="file" size={16} /></span>
                  {editing ? (
                    <button
                      aria-expanded={noteBodyOpen}
                      aria-label="새 회의록 내용 수정"
                      className="action-meeting-note-edit"
                      disabled={busy}
                      onClick={() => setNoteBodyOpen((open) => !open)}
                      type="button"
                    >새 회의록</button>
                  ) : <span>새 회의록</span>}
                  {action.state === "pending" && (
                    <button aria-label="새 회의록 제외" disabled={busy} onClick={() => {
                      recovered.setDraft({ ...recovered.draft, include_initial_note: false });
                      setNoteBodyOpen(false);
                    }} type="button"><Icon name="close" size={14} /></button>
                  )}
                </li>
              </ul>
            )}
            {noteIncluded && editing && noteBodyOpen && (
              <div className="action-meeting-note-editor">
                <label htmlFor="action-meeting-initial_note_body">회의록 초안</label>
                <textarea
                  aria-label="회의록 초안"
                  disabled={busy || Boolean(recovered.stale)}
                  id="action-meeting-initial_note_body"
                  onChange={(event) => recovered.setDraft({
                    ...recovered.draft,
                    initial_note_body: event.target.value || null,
                  })}
                  value={recovered.draft.initial_note_body ?? ""}
                />
                <MeetingNoteSources sources={sources} status={sourceStatus} />
              </div>
            )}
          </section>
        )}
        {action.state === "pending" && (contract?.warnings ?? []).length > 0 && (
          <ul aria-label="일정 경고" className="action-meeting-warnings">
            {(contract?.warnings ?? []).map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        )}
        {error && <p className="action-task-error" role="alert">{error}</p>}
      </div>
      <div className="action-task-actions">
        {action.state === "pending" && contract && !editing && (
          <button className="btn h30 ghost" disabled={busy} onClick={() => {
            setNoteBodyOpen(false);
            setEditing(true);
          }} type="button">수정</button>
        )}
        {action.state === "pending" && contract && editing && (
          <>
            <button className="btn h30 ghost" disabled={busy} onClick={() => {
              recovered.reset();
              setStartAttachmentPicker(false);
              setNoteBodyOpen(false);
              setEditing(false);
            }} type="button">취소</button>
            <button className="btn h30 ghost action-task-reset" disabled={busy} onClick={() => {
              recovered.reset();
              setNoteBodyOpen(false);
            }} type="button"><Icon name="refresh" size={14} />초기화</button>
          </>
        )}
        {confirm && contract && (
          <button
            className="btn h30 primary"
            disabled={busy || Boolean(recovered.stale) || incompleteMaterial}
            onClick={() => {
              const problem = issue();
              if (problem) {
                setError(problem.message);
                if (!editing) setEditing(true);
                if (problem.field === "initial_note_body") {
                  setNoteBodyOpen(true);
                  queueMicrotask(() => focusField(problem.field));
                } else {
                  focusField(problem.field);
                }
                return;
              }
              void run(confirm.id, {
                base_submission_version: contract.base_submission_version,
                ...(changed ? { draft: commandDraft(recovered.draft) } : {}),
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
          <button className={`btn h30 ${command.tone === "danger" ? "danger" : "ghost"}`} disabled={busy} key={command.id} onClick={() => void run(command.id)} type="button">{command.label}</button>
        ))}
        {meetingId && onOpenMeeting && (
          <button className="btn h30" onClick={() => onOpenMeeting(meetingId)} type="button">회의 상세 보기</button>
        )}
      </div>
    </section>
  );
}

function MeetingDraftFields({
  contract,
  draft,
  disabled,
  onChange,
}: {
  contract: ActionEditContract;
  draft: MeetingDraft;
  disabled?: boolean;
  onChange: (draft: MeetingDraft) => void;
}) {
  const field = (id: string) => contract.fields.find((candidate) => candidate.id === id);
  const title = field("title");
  const description = field("description");
  const host = field("host_id");
  const attendees = field("attendee_ids");
  const startsAt = datePart(draft.starts_at);
  const endsAt = datePart(draft.ends_at);
  const sameDay = !startsAt || !endsAt || startsAt === endsAt;
  const attendeeOptions = (attendees?.options ?? [])
    .filter((option) => !option.organization_ids || option.organization_ids.includes(draft.organization_id))
    .map((option) => ({ value: option.value, label: option.label }));

  const replaceDate = (nextDate: string) => {
    if (!nextDate) {
      onChange({ ...draft, starts_at: "", ends_at: "" });
      return;
    }
    const dayOffset = startsAt && endsAt
      ? Math.round((Date.parse(`${endsAt}T00:00:00Z`) - Date.parse(`${startsAt}T00:00:00Z`)) / 86_400_000)
      : 0;
    onChange({
      ...draft,
      starts_at: `${nextDate}T${timePart(draft.starts_at) || "09:00"}`,
      ends_at: `${addDays(nextDate, Math.max(0, dayOffset))}T${timePart(draft.ends_at) || "10:00"}`,
    });
  };

  return (
    <div className="action-task-fields action-meeting-fields">
      <div className="action-task-field text">
        <label htmlFor="action-meeting-title">{title?.label ?? "회의 명"}<span aria-hidden className="danger-text"> *</span></label>
        <input
          aria-label={title?.label ?? "회의 명"}
          disabled={disabled}
          id="action-meeting-title"
          onChange={(event) => onChange({ ...draft, title: event.target.value })}
          required
          type="text"
          value={draft.title}
        />
      </div>
      <div className="action-task-field textarea">
        <label htmlFor="action-meeting-description">{description?.label ?? "내용"}</label>
        <textarea
          aria-label={description?.label ?? "내용"}
          disabled={disabled}
          id="action-meeting-description"
          onChange={(event) => onChange({ ...draft, description: event.target.value || null })}
          value={draft.description ?? ""}
        />
      </div>
      <div className="action-meeting-people">
        <div className="action-task-field person">
          <label htmlFor="action-meeting-host_id">{host?.label ?? "주최자"}<span aria-hidden className="danger-text"> *</span></label>
          <output aria-label={host?.label ?? "주최자"} className="action-meeting-host" id="action-meeting-host_id">
            <span>{String(host?.label_value ?? host?.value ?? "—")}</span>
            <Icon name="chevron-down" size={12} />
          </output>
        </div>
        <div className="action-task-field multi_select">
          <label htmlFor="action-meeting-attendee_ids">{attendees?.label ?? "참석자"}</label>
          <MultiSelect
            disabled={disabled}
            id="action-meeting-attendee_ids"
            label={attendees?.label ?? "참석자"}
            maxChips={2}
            onChange={(attendeeIds) => onChange({ ...draft, attendee_ids: attendeeIds })}
            options={attendeeOptions}
            placeholder="참석자를 선택해 주세요."
            selectAll={false}
            value={draft.attendee_ids}
          />
        </div>
      </div>
      {sameDay ? (
        <div className="action-meeting-schedule">
          <DateField
            displaySeparator="."
            disabled={disabled}
            id="action-meeting-starts_at"
            label="날짜"
            onChange={replaceDate}
            pickerIcon="chevron-down"
            required
            value={startsAt}
          />
          <div className="action-task-field action-meeting-time">
            <label htmlFor="action-meeting-ends_at">시간<span aria-hidden className="danger-text"> *</span></label>
            <TimeRangeField
              disabled={disabled}
              id="action-meeting-ends_at"
              label="시간"
              onChange={({ start, end }) => onChange({
                ...draft,
                starts_at: `${startsAt}T${start}`,
                ends_at: `${startsAt}T${end}`,
              })}
              start={timePart(draft.starts_at)}
              end={timePart(draft.ends_at)}
            />
          </div>
        </div>
      ) : (
        <div className="action-meeting-cross-day">
          {(["starts_at", "ends_at"] as const).map((key) => (
            <div className="action-task-field datetime" key={key}>
              <label htmlFor={`action-meeting-${key}`}>{key === "starts_at" ? "시작" : "종료"}<span aria-hidden className="danger-text"> *</span></label>
              <input aria-label={key === "starts_at" ? "시작" : "종료"} disabled={disabled} id={`action-meeting-${key}`} onChange={(event) => onChange({ ...draft, [key]: event.target.value })} type="datetime-local" value={draft[key]} />
            </div>
          ))}
        </div>
      )}
      {attendeeOptions.length === 0 && <small>선택할 수 있는 참석자가 없습니다.</small>}
    </div>
  );
}

function MeetingNoteSources({ status, sources }: { status: string; sources: SourceEvidence[] }) {
  return (
    <section aria-label="회의록 초안 근거" className="action-meeting-sources">
      {status === "not_found" && <p role="status">지난 논의 기록을 찾지 못해 현재 대화만 사용했습니다.</p>}
      {status === "current_turn" && <p>현재 대화 기반 초안입니다.</p>}
      {status === "resolved" && <p>확인된 대화·자료를 바탕으로 준비한 초안입니다.</p>}
      <ul>
        {sources.map((source) => (
          <li key={`${String(source.source_type)}:${String(source.source_id)}`}>
            <b>{String(source.label ?? "근거")}</b>
            {source.excerpt ? <span>{String(source.excerpt)}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function meetingResultId(result: Record<string, unknown> | null): string | null {
  if (!result) return null;
  if (typeof result.meeting_id === "string") return result.meeting_id;
  const nested = result.meeting;
  return nested && typeof nested === "object" && "meeting_id" in nested && typeof nested.meeting_id === "string"
    ? nested.meeting_id
    : null;
}
