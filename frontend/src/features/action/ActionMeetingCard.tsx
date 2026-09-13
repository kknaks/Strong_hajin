import { useEffect, useMemo, useRef, useState } from "react";
import { useBrowserOperationGuard } from "../../lib/browserOperationGuard";

import { actionSubject } from "./ActionPreview";
import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { discardActionMaterialDraft, stageActionMaterialFile, stageActionMaterialLink } from "../../lib/api";
import { DateField } from "../../ds/DateField";
import { datePickerLabel, emptyActionLabel, formatDateTime, formatMonthLong, selectLabel, seoulToday, timeFieldLabel, weekdayNames } from "../../lib/labels";
import { Icon } from "../../ds/icons/Icon";
import { MultiSelect } from "../../ds/Select";
import { TaskAttachmentGroup, type LocalMaterialTransfer } from "./ActionTaskCard";
import { TimeRangeField } from "../../ds/TimeField";
import { useActionDraft } from "../work/useActionDraft";
import type { ActionEditContract, ActionItem, ActionMaterialDraft } from "../../lib/viewModels";


/**
 * The reservation contract is authored by the server. The card names only the values it edits;
 * everything else the contract carried — external attendees, agendas, the carried-over meeting,
 * the chosen room — travels back unchanged instead of being silently dropped on confirm.
 */
export type MeetingDraft = {
  title: string;
  purpose: string | null;
  starts_at: string;
  ends_at: string;
  location: string | null;
  attendee_ids: string[];
  /** Named only so the shared attachment group can read it; the reservation contract does not carry it. */
  reference_task_ids?: string[];
  [carried: string]: unknown;
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
    ...values,
    title: String(values.title ?? ""),
    purpose: values.purpose ? String(values.purpose) : null,
    starts_at: localDateTime(values.starts_at),
    ends_at: localDateTime(values.ends_at),
    location: values.location ? String(values.location) : null,
    attendee_ids: Array.isArray(values.attendee_ids) ? values.attendee_ids.map(String) : [],
  };
}

function MeetingSummary({ action }: { action: ActionItem }) {
  const preview = new Map((action.preview ?? []).map((row) => [row.id, row]));
  const purpose = preview.get("purpose")?.value;
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
      {purpose && <p>{purpose}</p>}
      <dl>
        {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
    </div>
  );
}

function commandDraft(draft: MeetingDraft): MeetingDraft {
  return { ...draft, starts_at: isoDateTime(draft.starts_at), ends_at: isoDateTime(draft.ends_at) };
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
  const uploadingMaterial = transfers.some((item) => item.state === "uploading");
  useBrowserOperationGuard(uploadingMaterial);
  const meetingId = meetingResultId(action.result);
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
        : /장소|회의실|location|room/i.test(message) ? "location"
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
    <section className="scax-actioncard action-task-card action-meeting-card" data-action-id={action.action_id} data-state={action.state} data-view={editing ? "editing" : action.state} ref={cardRef}>
      <div className="action-task-content">
        <div className="scax-actioncard__badges">
          <Badge tone="positive">회의</Badge>
        </div>
        {editing && contract ? (
          <>
            {recovered.stale && (
              <div className="action-task-stale" role="status">
                <b>AX 제안이 새 버전으로 갱신되었습니다.</b>
                <p>수정한 회의 초안은 보존했습니다. 최신안을 기준으로 다시 시작하거나 내 초안을 이어서 편집하세요.</p>
                <div className="action-task-actions">
                  <Button size="sm" onClick={recovered.startFromLatest} type="button">최신안으로 다시 시작</Button>
                  <Button variant="text" size="sm" onClick={recovered.continueWithLocal} type="button">내 초안으로 다시 편집</Button>
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
                disabled={busy || uploadingMaterial}
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
        {action.state === "pending" && (contract?.warnings ?? []).length > 0 && (
          <ul aria-label="일정 경고" className="action-meeting-warnings">
            {(contract?.warnings ?? []).map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        )}
        {error && <p className="action-task-error" role="alert">{error}</p>}
      </div>
      <div className="action-task-actions">
        {action.state === "pending" && contract && !editing && (
          <Button variant="text" size="sm" disabled={busy || uploadingMaterial} onClick={() => setEditing(true)} type="button">수정</Button>
        )}
        {action.state === "pending" && contract && editing && (
          <>
            <Button variant="text" size="sm" disabled={busy || uploadingMaterial} onClick={() => {
              recovered.reset();
              setStartAttachmentPicker(false);
              setEditing(false);
            }} type="button">취소</Button>
            <Button variant="text" size="sm" className="action-task-reset" disabled={busy || uploadingMaterial} onClick={recovered.reset} type="button"><Icon name="reset" size={14} />초기화</Button>
          </>
        )}
        {confirm && contract && (
          <Button variant="solid" tone="primary" size="sm" disabled={busy || Boolean(recovered.stale) || incompleteMaterial} onClick={() => {
              const problem = issue();
              if (problem) {
                setError(problem.message);
                if (!editing) setEditing(true);
                focusField(problem.field);
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
          </Button>
        )}
        {(action.commands ?? []).filter((command) => !["confirm", "reject"].includes(command.id)).map((command) => (
          <Button disabled={busy || uploadingMaterial} key={command.id} onClick={() => void run(command.id)} size="sm" tone={command.tone === "danger" ? "danger" : "neutral"} type="button" variant={command.tone === "danger" ? "solid" : "text"}>{command.label}</Button>
        ))}
        {meetingId && onOpenMeeting && (
          <Button size="sm" onClick={() => onOpenMeeting(meetingId)} type="button">회의 상세 보기</Button>
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
  const purpose = field("purpose");
  const location = field("location");
  const host = field("host_id");
  const attendees = field("attendee_ids");
  const startsAt = datePart(draft.starts_at);
  const endsAt = datePart(draft.ends_at);
  const sameDay = !startsAt || !endsAt || startsAt === endsAt;
  const attendeeOptions = (attendees?.options ?? []).map((option) => ({ value: option.value, label: option.label }));

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
        <label htmlFor="action-meeting-purpose">{purpose?.label ?? "목적"}</label>
        <textarea
          aria-label={purpose?.label ?? "목적"}
          disabled={disabled}
          id="action-meeting-purpose"
          onChange={(event) => onChange({ ...draft, purpose: event.target.value || null })}
          value={draft.purpose ?? ""}
        />
      </div>
      <div className="action-task-field text">
        <label htmlFor="action-meeting-location">{location?.label ?? "장소"}</label>
        <input
          aria-label={location?.label ?? "장소"}
          disabled={disabled}
          id="action-meeting-location"
          onChange={(event) => onChange({ ...draft, location: event.target.value || null })}
          type="text"
          value={draft.location ?? ""}
        />
      </div>
      <div className="action-meeting-people">
        {host && (
          <div className="action-task-field person">
            <label htmlFor="action-meeting-host_id">{host.label}<span aria-hidden className="danger-text"> *</span></label>
            <output aria-label={host.label} className="action-meeting-host" id="action-meeting-host_id">
              <span>{String(host.label_value ?? host.value ?? "—")}</span>
              <Icon name="chevron-down" size={12} />
            </output>
          </div>
        )}
        <div className="action-task-field multi_select">
          <label htmlFor="action-meeting-attendee_ids">{attendees?.label ?? "참석자"}</label>
          <MultiSelect
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
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
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames}
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
            emptyActionLabel={emptyActionLabel.filter}
            endLabel={timeFieldLabel.end}
            labels={timeFieldLabel}
            selectLabels={selectLabel}
            startLabel={timeFieldLabel.start}
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


function meetingResultId(result: Record<string, unknown> | null): string | null {
  if (!result) return null;
  if (typeof result.meeting_id === "string") return result.meeting_id;
  const nested = result.meeting;
  return nested && typeof nested === "object" && "meeting_id" in nested && typeof nested.meeting_id === "string"
    ? nested.meeting_id
    : null;
}
