import { useMemo, useState } from "react";

import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { DateField } from "../../ds/DateField";
import { FieldMessage } from "../../ds/FormControls";
import { MultiSelect } from "../../ds/Select";
import { TimeRangeField } from "../../ds/TimeField";
import {
  axDraftCard,
  datePickerLabel,
  emptyActionLabel,
  formatDate,
  formatMonthLong,
  meetingClock,
  meetingDateInput,
  meetingIsoAt,
  selectLabel,
  seoulToday,
  timeFieldLabel,
  weekdayNames,
} from "../../lib/labels";
import type { ActionCommand, ActionItem, MeetingRoom } from "../../lib/viewModels";
import { meetingErrorText, roomRejectionOf } from "../meetings/BookingModal";
import { RoomSelect, roomChoiceId, type RoomChoice, type RoomSelectStatus } from "../meetings/RoomSelect";
import { ReasonPrompt } from "../work/WorkModals";

/**
 * AX 회의 수정 카드(SPEC-010 §2.4 · WP3 계약 고정 1) — `meeting.info.update` 의 **고칠 수 있는 카드**.
 *
 * 예전에는 이 종류에 편집 계약이 없어 결과 카드(미리보기 + 승인)로만 섰다. 이제 편집 계약(`editor="meeting_update"`)의 값으로
 * 칸을 채우고, 사람이 그 자리에서 고쳐 [등록]한다. **초안 저장·회차는 없다**(쪽 나눔은 생성 카드에만).
 *
 * - 칸: 회의명 · 목적 · 날짜·시작·종료 · 참석자(사내·사외) · 회의실(공용 셀렉트 · 수정 모양 — 맨 위 기존 줄). **장소 글자 칸은 없다**(OQ-1005)
 * - [등록]의 `draft` = **바뀐 칸만** + 방은 PATCH 와 같은 `room: {room_id}`(안 바꾸면 `room` 없음)
 * - 저장 직전 그새 방이 찼으면 `409 ROOM_BOOKING_REFUSED` — 셀렉트를 그때 가능한 방으로 다시 세우고 사람이 다시 고른다(자동 대체 없음)
 */

type Values = Record<string, unknown>;

const text = (value: unknown): string => (value === null || value === undefined ? "" : String(value).trim());
const list = (value: unknown): string[] => (Array.isArray(value) ? value.map(String).filter(Boolean) : []);
const splitGuests = (value: string): string[] =>
  value
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);

type Draft = { title: string; purpose: string; date: string; from: string; to: string; attendeeIds: string[]; guests: string; room: RoomChoice };

const hasValue = (value: unknown) => value !== null && value !== undefined && value !== "";

/**
 * AX 가 제안한 방(WP3 계약 고정 §5 · §6). **「제안했는가」 는 `values.room_proposed === true` 하나로 본다** —
 * `proposed_room_id: null` 만으로는 「예약 없음 제안」 과 「제안 없음」 을 가를 수 없다(검수 F-r2-1).
 * 제안이면 `proposed_room_id` 가 `null` = 「회의실 예약 없음」 제안 · 숫자 = 그 방. 지금 방과 같으면 바꿀 것이 없어 제안으로 치지 않는다.
 * 제안이 아니면 `undefined`(카드는 기존 방 그대로 · 표지 없음).
 */
export function proposedRoomOf(values: Values): RoomChoice | undefined {
  if (values.room_proposed !== true) return undefined;
  const proposed = values.proposed_room_id;
  if (!hasValue(proposed)) return hasValue(values.room_id) ? "none" : undefined;
  if (hasValue(values.room_id) && String(values.room_id) === String(proposed)) return undefined;
  return String(proposed) as RoomChoice;
}

/** 편집 계약의 «지금» 값 — 제안을 빼고 본 방(기존이면 「keep」, 없던 회의면 「none」). */
function currentRoomOf(values: Values): RoomChoice {
  return hasValue(values.room_id) ? "keep" : "none";
}

function draftOf(values: Values): Draft {
  const start = text(values.starts_at);
  const end = text(values.ends_at);
  return {
    title: text(values.title),
    purpose: text(values.purpose),
    date: start ? meetingDateInput(start) : seoulToday(),
    from: start ? meetingClock(start) : "",
    to: end ? meetingClock(end) : "",
    attendeeIds: list(values.attendee_ids),
    guests: list(values.external_attendees).join(", "),
    /* AX 가 방을 제안했으면 그 방을 **미리 골라 둔다**(SPEC-010 §2.4 · 계약 고정 §5 — 「AX 제안」 표지가 붙는다).
       제안이 없으면 방이 잡힌 회의는 「기존 (변경 안 함)」, 없던 회의는 「회의실 예약 없음」.
       AX 가 사외 장소를 글자로 제안해도 저장하지 않는다(W-r2-6) */
    room: proposedRoomOf(values) ?? currentRoomOf(values),
  };
}

/** 바뀐 칸만(WP3 계약 고정 1). 시각은 날짜·시작·종료 중 하나라도 바뀌면 둘 다 싣는다. */
export function meetingUpdateDraft(values: Values, draft: Draft): Record<string, unknown> {
  const base = draftOf(values);
  const changed: Record<string, unknown> = {};
  if (draft.title.trim() !== base.title) changed.title = draft.title.trim();
  if (draft.purpose.trim() !== base.purpose) changed.purpose = draft.purpose.trim() || null;
  if (draft.date !== base.date || draft.from !== base.from || draft.to !== base.to) {
    changed.starts_at = meetingIsoAt(draft.date, draft.from);
    changed.ends_at = meetingIsoAt(draft.date, draft.to);
  }
  if (draft.attendeeIds.join() !== base.attendeeIds.join()) changed.attendee_ids = draft.attendeeIds;
  if (splitGuests(draft.guests).join() !== splitGuests(base.guests).join()) changed.external_attendees = splitGuests(draft.guests);
  /*
   * 회의실 — 비교 기준은 **지금 방**이지 AX 제안이 아니다(제안을 고른 채 [등록]하면 그 방이 «바뀐 칸» 으로 실린다).
   * 서버는 draft 의 사람 선택이 언제나 AX 제안을 이기고, draft 에 `room` 이 없으면 「바꾸지 않음」 이다(계약 고정 §5).
   * 그래도 제안이 있었는데 사람이 「기존 (변경 안 함)」 으로 되돌렸으면 **명시적으로** `room: {keep: true}` 를 싣는다.
   */
  const current = currentRoomOf(values);
  const proposed = proposedRoomOf(values);
  if (draft.room === "keep") {
    if (proposed !== undefined) changed.room = { keep: true };
  } else if (draft.room === "none") {
    if (current !== "none" || proposed !== undefined) changed.room = { room_id: null };
  } else {
    changed.room = { room_id: roomChoiceId(draft.room) };
  }
  return changed;
}

export function ActionMeetingUpdateCard({
  action,
  onCommand,
  onOpenMeeting,
}: {
  action: ActionItem;
  onCommand: (commandId: string, payload?: Record<string, unknown>) => Promise<void>;
  onOpenMeeting?: (meetingId: string) => void;
}) {
  const contract = action.edit_contract?.editor === "meeting_update" ? action.edit_contract : null;
  const values = useMemo(() => contract?.values ?? {}, [contract]);
  const [draft, setDraft] = useState<Draft>(() => draftOf(values));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<ActionCommand | null>(null);
  const [roomStatus, setRoomStatus] = useState<RoomSelectStatus>({ state: "loading", blocked: false, reason: null });
  const [refused, setRefused] = useState<MeetingRoom[] | null>(null);
  const confirm = action.commands?.find((command) => command.id === "confirm");
  const reject = action.commands?.find((command) => command.id === "reject");
  const meetingId = text(values.meeting_id) || null;
  const roomName = text(values.room_name) || null;
  const proposedRoom = proposedRoomOf(values);
  const proposedRoomName = proposedRoom === "none" ? axDraftCard.noRoomProposal : text(values.proposed_room_name) || null;
  const attendeeField = contract?.fields.find((field) => field.id === "attendee_ids");
  const attendeeOptions = (attendeeField?.options ?? []).map((option) => ({ value: option.value, label: option.label }));
  const pending = action.state === "pending";
  const ready = Boolean(draft.title.trim()) && Boolean(draft.from) && Boolean(draft.to) && draft.from !== draft.to && !roomStatus.blocked;

  async function send(commandId: string, payload?: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await onCommand(commandId, payload);
      return true;
    } catch (reason) {
      const rejected = roomRejectionOf(reason);
      if (rejected) {
        /* 그새 방이 찼다 — 넣은 값은 그대로 · 셀렉트를 지금 가능한 방으로 · 사람이 다시 고를 때까지 [등록]이 막힌다 */
        setRefused(rejected.rooms);
        setDraft((current) => ({ ...current, room: "unset" }));
        setError(rejected.message);
      } else {
        setError(meetingErrorText(reason, "회의 수정을 처리하지 못했습니다."));
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (action.state === "approved") {
    /* 등록 뒤 — 한 줄 + [회의 열기] */
    const start = text(values.starts_at);
    return (
      <section className="scax-actioncard ax-draft-card ax-draft-card--done action-meeting-update-card" data-action-id={action.action_id} data-state="approved">
        <p className="ax-draft-card__line">
          <Badge tone="neutral">{axDraftCard.registered}</Badge>
          <b>{text(values.title) || action.title}</b>
          <span>{start ? `${formatDate(meetingDateInput(start))} ${meetingClock(start)}` : "—"}</span>
        </p>
        {meetingId && onOpenMeeting && (
          <Button onClick={() => onOpenMeeting(meetingId)} size="sm" type="button">
            {axDraftCard.openMeeting}
          </Button>
        )}
      </section>
    );
  }

  const disabled = !pending || busy;
  return (
    <section
      aria-label={`${axDraftCard.badge} ${axDraftCard.updateKind} · ${text(values.title) || action.title}`}
      className="scax-actioncard action-task-card action-meeting-card action-meeting-update-card"
      data-action-id={action.action_id}
      data-state={action.state}
    >
      <header className="ax-draft-card__head">
        <div className="scax-actioncard__badges">
          <Badge tone="neutral">{axDraftCard.badge}</Badge>
          {action.state === "rejected" && <Badge tone="neutral">{axDraftCard.rejected}</Badge>}
          <span className="ax-draft-card__kind">{axDraftCard.updateKind}</span>
        </div>
        <b className="ax-draft-card__title">{text(values.title) || action.title}</b>
      </header>
      <div className="action-task-fields action-meeting-fields">
        <div className="action-task-field text">
          <label htmlFor={`meeting-update-title-${action.action_id}`}>
            {axDraftCard.meetingTitle}
            <span aria-hidden className="danger-text"> *</span>
          </label>
          <input
            disabled={disabled}
            id={`meeting-update-title-${action.action_id}`}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            type="text"
            value={draft.title}
          />
        </div>
        <div className="action-task-field textarea">
          <label htmlFor={`meeting-update-purpose-${action.action_id}`}>{axDraftCard.meetingPurpose}</label>
          <textarea
            disabled={disabled}
            id={`meeting-update-purpose-${action.action_id}`}
            onChange={(event) => setDraft({ ...draft, purpose: event.target.value })}
            value={draft.purpose}
          />
        </div>
        <div className="action-meeting-schedule">
          <DateField
            disabled={disabled}
            displaySeparator="."
            formatMonth={formatMonthLong}
            id={`meeting-update-date-${action.action_id}`}
            label={axDraftCard.meetingDate}
            labels={datePickerLabel}
            onChange={(next) => setDraft({ ...draft, date: next })}
            pickerIcon="chevron-down"
            required
            today={seoulToday()}
            value={draft.date}
            weekdayNames={weekdayNames}
          />
          <div className="action-task-field action-meeting-time">
            <label htmlFor={`meeting-update-time-${action.action_id}`}>
              {axDraftCard.meetingTime}
              <span aria-hidden className="danger-text"> *</span>
            </label>
            <TimeRangeField
              disabled={disabled}
              emptyActionLabel={emptyActionLabel.filter}
              end={draft.to}
              endLabel={timeFieldLabel.end}
              id={`meeting-update-time-${action.action_id}`}
              label={axDraftCard.meetingTime}
              labels={timeFieldLabel}
              onChange={({ start, end }) => setDraft({ ...draft, from: start, to: end })}
              selectLabels={selectLabel}
              start={draft.from}
              startLabel={timeFieldLabel.start}
            />
          </div>
        </div>
        <div className="action-meeting-people">
          <div className="action-task-field multi_select">
            <label htmlFor={`meeting-update-attendees-${action.action_id}`}>{axDraftCard.meetingAttendees}</label>
            <MultiSelect
              disabled={disabled}
              emptyActionLabel={emptyActionLabel.filter}
              id={`meeting-update-attendees-${action.action_id}`}
              label={axDraftCard.meetingAttendees}
              labels={selectLabel}
              maxChips={2}
              onChange={(attendeeIds) => setDraft({ ...draft, attendeeIds })}
              options={attendeeOptions}
              placeholder="참석자를 선택해 주세요."
              selectAll={false}
              value={draft.attendeeIds}
            />
          </div>
          <div className="action-task-field text">
            <label htmlFor={`meeting-update-guests-${action.action_id}`}>{axDraftCard.meetingGuests}</label>
            <input
              disabled={disabled}
              id={`meeting-update-guests-${action.action_id}`}
              onChange={(event) => setDraft({ ...draft, guests: event.target.value })}
              placeholder={axDraftCard.updateGuestsHint}
              type="text"
              value={draft.guests}
            />
          </div>
        </div>
        <div className="action-task-field">
          <label>
            {axDraftCard.meetingRoom}
            {/* AX 가 제안한 방을 지금 골라 두고 있다 — 사람이 그것을 알고 [등록]하게 표지를 단다(계약 고정 §5) */}
            {proposedRoom !== undefined && draft.room === proposedRoom && (
              <Badge className="ax-room-proposed" tone="neutral">
                {axDraftCard.proposedRoom(proposedRoomName)}
              </Badge>
            )}
          </label>
          <RoomSelect
            currentName={roomName}
            disabled={disabled}
            meetingId={meetingId}
            name={`ax-update-room-${action.action_id}`}
            onChange={(room) => setDraft({ ...draft, room })}
            onStatus={setRoomStatus}
            query={
              draft.from && draft.to
                ? {
                    starts_at: meetingIsoAt(draft.date, draft.from),
                    ends_at: meetingIsoAt(draft.date, draft.to),
                    people: draft.attendeeIds.length + splitGuests(draft.guests).length,
                  }
                : null
            }
            refused={refused}
            value={draft.room}
          />
        </div>
      </div>
      {error && <FieldMessage error={error} />}
      {pending && roomStatus.reason && <FieldMessage error={roomStatus.reason} />}
      {pending && (
        <footer className="ax-draft-card__foot">
          {reject && (
            <Button disabled={busy} onClick={() => (reject.requires_reason ? setRejecting(reject) : void send(reject.id))} size="sm" type="button">
              {axDraftCard.reject}
            </Button>
          )}
          {confirm && contract && (
            <Button
              disabled={busy || !ready}
              onClick={() => {
                const changed = meetingUpdateDraft(values, draft);
                void send(confirm.id, {
                  base_submission_version: contract.base_submission_version,
                  ...(Object.keys(changed).length > 0 ? { draft: changed } : {}),
                });
              }}
              size="sm"
              tone="primary"
              type="button"
              variant="solid"
            >
              {busy ? axDraftCard.confirming : axDraftCard.confirm}
            </Button>
          )}
        </footer>
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
