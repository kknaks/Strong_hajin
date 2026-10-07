import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "../../ds/Button";
import { DateField } from "../../ds/DateField";
import { Icon } from "../../ds/icons/Icon";
import { Skeleton } from "../../ds/Skeleton";
import { TimeRangeField } from "../../ds/TimeField";
import { useEscape } from "../../ds/Modal";
import { readMeeting, updateMeetingInfo } from "../../lib/api";
import { createIdempotencyKey } from "../../lib/idempotency";
import {
  datePickerLabel,
  emptyActionLabel,
  formatMonthLong,
  meetingClock,
  meetingDateInput,
  meetingIsoAt,
  meetingScreen,
  personName,
  selectLabel,
  seoulToday,
  timeFieldLabel,
  weekdayNames,
} from "../../lib/labels";
import type { MeetingRecord, MeetingRoom } from "../../lib/viewModels";
import { meetingErrorText, roomRejectionOf } from "./BookingModal";
import { addPersonOnce, PersonSearch, PickedTags } from "./PeoplePicker";
import { RoomSelect, roomChoiceId, type RoomChoice, type RoomSelectStatus } from "./RoomSelect";
import { useRoster, type RosterPerson } from "./roster";

/** 참석자 한 줄 — 상세가 쓰던 것과 같은 모양이다 (`MeetingInfo.attendees` 의 원소). */
type Attendee = { member_id: string; display_name: string };

/** 칸 넷 + 회의실. 장소는 글자 칸이 아니라 회의실 셀렉트다(SPEC-010 §2.2 · OQ-1005) — `room` 은 셀렉트 값. */
type Head = { title: string; date: string; from: string; to: string; room: RoomChoice; people: Attendee[] };

/**
 * 이 회의가 쥐고 있는 방 이름 — 셀렉트 맨 위 「기존」 줄의 이름(목록의 `current` 를 받지 못했을 때의 대체 표시).
 * 예약이 선 회의(`booked`)만이 아니라 **앞 동기화가 실패했거나(`failed`) 결과를 아직 확인 중이어도(`needs_verification`)
 * 방 이름이 남아 있으면** 기존 줄로 본다(검수 W-6 · W-r2-4) —
 * 그래야 조회까지 503 인 날에도 「기존 — 회의실 N (확인 못 함)」 이 서고 조용히 「예약 없음」 으로 열리지 않는다(검수 W-6).
 */
function heldRoomName(meeting: MeetingRecord["meeting"] | null | undefined): string | null {
  const reservation = meeting?.room_reservation;
  if (!reservation?.room_name) return null;
  return reservation.status === "booked" || reservation.status === "failed" || reservation.status === "needs_verification" ? reservation.room_name : null;
}

/**
 * 회의 정보 수정 — **목록 카드의 [수정]이 여는 모달** (시안 02).
 *
 * 예전에는 상세 머리의 「회의 정보」 옆 연필이 그 자리에서 폼을 펴서, 제목 줄 위에 구획 라벨이
 * 한 겹 얹히고 [회의 시작]이 아래 줄로 밀렸다(현재 화면 07·11). 시안의 상세 머리는 제목으로
 * 시작하므로 **고치는 자리를 목록 카드로 옮겼다** — 시안 02 의 카드에 이미 있던 그 [수정]이다.
 *
 * 칸 다섯(회의명 · 날짜 · 시작~종료 · 회의실 · 참석자). **장소 글자 칸은 회의실 셀렉트로 바뀌었고**(SPEC-010 §2.2 · OQ-1005),
 * 저장은 **바뀐 값만** 보낸다(OQ-1010 — 시각이 안 바뀌면 시각을 싣지 않아 쓸데없는 Connect 호출이 안 난다).
 * 회의실은 안 고치면 `room` 을 싣지 않고(방 그대로 — 서버가 새 조건으로 다시 확인), 「예약 없음」 = `{room_id: null}`, 다른 방 = `{room_id: n}`.
 *
 * **열 수 있는지는 서버가 말한다** — 목록 행에는 `can_edit_info` 가 없으므로 이 모달이 열리며
 * 상세를 읽고, 서버가 「못 고친다」고 하면 그 사실만 내고 칸을 열지 않는다. 행의 status 로
 * 권한을 넘겨짚지 않는다.
 *
 * 닫는 길은 셋(× · Esc · 바깥 클릭)이고 **고치던 것이 있으면 한 번 묻는다** — 워크스페이스의
 * 이탈 가드가 「다른 회의를 고를 때」를 막는 것과 같은 약속을, 이 모달은 자기 닫기 경로에서 지킨다.
 */
export function MeetingEditModal({
  meetingId,
  onClose,
  onSaved,
  onError,
  onNotice,
}: {
  meetingId: string;
  onClose: () => void;
  /** 저장이 끝났다 — 목록과 (고르고 있었다면) 상세를 다시 읽는 것은 부르는 쪽이 한다. */
  onSaved: (meetingId: string) => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [record, setRecord] = useState<MeetingRecord | null>(null);
  const [failed, setFailed] = useState(false);
  const [head, setHead] = useState<Head | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [askClose, setAskClose] = useState(false);
  const [roomStatus, setRoomStatus] = useState<RoomSelectStatus>({ state: "loading", blocked: false, reason: null });
  /** 저장이 409 로 거절됐을 때 서버가 준 「지금 가능한 방」(SPEC-010 §4.3 · AC-08). */
  const [refusedRooms, setRefusedRooms] = useState<MeetingRoom[] | null>(null);
  /** 새 예약이 날 수 있는 저장의 멱등 키 — 같은 내용을 다시 누르면 같은 키(이중 예약 울타리 · SPEC-010 §4.3). */
  const saveAttempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const { roster } = useRoster(true);

  useEffect(() => {
    let cancelled = false;
    void readMeeting(meetingId)
      .then((next) => {
        if (cancelled) return;
        setRecord(next);
        const meeting = next.meeting;
        setHead({
          // 제목이 비어 있으면 후보를 칸에 채워 연다 — 사람이 그대로 저장하면 그것이 제목이 된다
          title: meeting.title ?? meeting.title_candidate ?? "",
          date: meetingDateInput(meeting.starts_at),
          from: meetingClock(meeting.starts_at),
          to: meetingClock(meeting.ends_at),
          /* 방이 잡혀 있으면 「기존 (변경 안 함)」, 없던 회의면 「회의실 예약 없음」 에서 연다 */
          room: heldRoomName(meeting) ? "keep" : "none",
          people: meeting.attendees.map((one) => ({ ...one })),
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  const meeting = record?.meeting ?? null;
  const canEdit = Boolean(meeting?.can_edit_info);
  /** 지금 잡힌 방 이름 — 셀렉트 맨 위 「기존」 줄. 방이 없던 회의면 `null`(그 줄도 없다). */
  const currentRoomName = heldRoomName(meeting);

  const unchanged = useMemo(() => {
    if (!head || !meeting) return true;
    return (
      head.title === (meeting.title ?? "") &&
      head.date === meetingDateInput(meeting.starts_at) &&
      head.from === meetingClock(meeting.starts_at) &&
      head.to === meetingClock(meeting.ends_at) &&
      (head.room === "keep" || (head.room === "none" && currentRoomName === null)) &&
      head.people.map((one) => one.member_id).join() === meeting.attendees.map((one) => one.member_id).join()
    );
  }, [head, meeting]);

  const close = () => (unchanged ? onClose() : setAskClose(true));
  useEscape(close, !askClose);

  /** 바뀐 값만 담은 patch(OQ-1010). 시각은 날짜·시작·종료 중 하나라도 바뀌면 둘 다 싣는다. */
  function changedPatch() {
    if (!head || !meeting) return {};
    const patch: Parameters<typeof updateMeetingInfo>[1] = {};
    const title = head.title.trim() || null;
    if ((title ?? "") !== (meeting.title ?? "")) patch.title = title;
    const startsAt = meetingIsoAt(head.date, head.from);
    const endsAt = meetingIsoAt(head.date, head.to);
    if (head.date !== meetingDateInput(meeting.starts_at) || head.from !== meetingClock(meeting.starts_at) || head.to !== meetingClock(meeting.ends_at)) {
      patch.starts_at = startsAt;
      patch.ends_at = endsAt;
    }
    if (head.people.map((one) => one.member_id).join() !== meeting.attendees.map((one) => one.member_id).join()) {
      patch.attendee_ids = head.people.map((one) => one.member_id);
    }
    /* 「기존 (변경 안 함)」 이면 `room` 을 싣지 않는다 — 방이 없던 회의에서 「예약 없음」 그대로여도 싣지 않는다 */
    if (head.room !== "keep" && !(head.room === "none" && currentRoomName === null)) patch.room = { room_id: roomChoiceId(head.room) };
    return patch;
  }

  async function save() {
    if (!head || !meeting || unchanged || busy || roomStatus.blocked) return;
    setBusy(true);
    const patch = changedPatch();
    /* 새 예약이 날 수 있는 요청(다른 방을 고름)은 멱등 키를 싣는다 — 같은 내용이면 같은 키 */
    let key: string | undefined;
    if (patch.room && patch.room.room_id !== null) {
      const fingerprint = JSON.stringify(patch);
      if (saveAttempt.current?.fingerprint !== fingerprint) saveAttempt.current = { fingerprint, key: createIdempotencyKey() };
      key = saveAttempt.current.key;
    }
    /* 조회 실패 중 「기존 (확인 못 함)」 으로 시각을 바꿨다 — 예약이 옛 시각에 남을 수 있다(H-1) */
    const unchecked = roomStatus.state === "failed" && head.room === "keep" && currentRoomName !== null && patch.starts_at !== undefined;
    try {
      const saved = await updateMeetingInfo(meeting.meeting_id, patch, key);
      const reservation = saved?.meeting?.room_reservation;
      /* 저장 때 Connect 에 닿지 못했다 — 회의는 바뀌었다(WP3 계약 고정 3 · OQ-1008) */
      const syncFailed = reservation?.status === "failed" && reservation.reason === "reservation_unavailable";
      onNotice(unchecked ? meetingScreen.roomSyncUnchecked : syncFailed ? meetingScreen.roomSyncFailed : meetingScreen.saved);
      onSaved(meeting.meeting_id);
    } catch (reason) {
      const rejected = roomRejectionOf(reason);
      if (rejected) {
        /* 그새 방이 찼다 — 넣은 값은 그대로 두고 셀렉트를 «지금 가능한 방» 으로 다시 세운다. 자동 대체하지 않고(OQ-1004),
           조용히 「예약 없음」 으로 옮기지도 않는다 — 사람이 다시 고를 때까지 [저장]이 막힌다 */
        setRefusedRooms(rejected.rooms);
        setHead((current) => (current ? { ...current, room: "unset" } : current));
        saveAttempt.current = null;
        onNotice(rejected.message);
      } else {
        onError(meetingErrorText(reason, "저장하지 못했습니다."));
      }
      setBusy(false);
    }
  }

  const pickedIds = new Set((head?.people ?? []).map((one) => one.member_id));

  return (
    <>
      <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && close()}>
        <section aria-label={meetingScreen.editInfo} aria-modal="true" className="modal meeting-modal-wide" role="dialog">
          <header className="modal-head">
            <h3>{meetingScreen.editInfo}</h3>
            <button aria-label="닫기" className="modal-close" onClick={close} type="button">
              <Icon name="close" size={20} />
            </button>
          </header>

          <div className="modal-body meeting-scroll">
            {failed ? (
              <p className="scax-field__error">{meetingScreen.listError}</p>
            ) : !head || !meeting ? (
              <Skeleton label="회의를 불러오는 중" rows={4} />
            ) : !canEdit ? (
              /* 서버가 「못 고친다」고 했다 — 칸을 열지 않고 그 사실만 낸다 */
              <p className="t-meta">{meetingScreen.cannotEditInfo}</p>
            ) : (
              <div className="meeting-meta-edit">
                <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor="meeting-head-title">
                      {meetingScreen.titleField}
                    </label>
                    <input
                      id="meeting-head-title"
                      onChange={(event) => setHead({ ...head, title: event.target.value })}
                      placeholder={meetingScreen.subjectPlaceholder}
                      type="text"
                      value={head.title}
                    />
                  </div>
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor="meeting-head-date">
                      {meetingScreen.whenField}
                    </label>
                    {/* 날짜·시각은 공용 부품이다 — 예약 모달과 같은 달력·같은 시각 칩을 쓴다 */}
                    <div className="meeting-when">
                      <DateField
                        formatMonth={formatMonthLong}
                        hideLabel
                        id="meeting-head-date"
                        label={meetingScreen.whenField}
                        labels={datePickerLabel}
                        onChange={(next) => setHead({ ...head, date: next })}
                        today={seoulToday()}
                        value={head.date}
                        weekdayNames={weekdayNames}
                      />
                      <TimeRangeField
                        emptyActionLabel={emptyActionLabel.filter}
                        end={head.to}
                        endLabel={timeFieldLabel.end}
                        label={meetingScreen.whenField}
                        labels={timeFieldLabel}
                        onChange={(next) => setHead({ ...head, from: next.start, to: next.end })}
                        selectLabels={selectLabel}
                        start={head.from}
                        startLabel={timeFieldLabel.start}
                      />
                    </div>
                  </div>
                  {/* 장소는 회의실 셀렉트다(SPEC-010 §2.2 · OQ-1005) — 맨 위 「기존 (변경 안 함)」 · 예약 없음 · 새 시간·인원의 가용 방 */}
                  <div className="scax-field">
                    <label className="scax-field__label">{meetingScreen.place}</label>
                    <RoomSelect
                      currentName={currentRoomName}
                      meetingId={meeting.meeting_id}
                      name="meeting-edit-room"
                      onChange={(next) => setHead({ ...head, room: next })}
                      onStatus={setRoomStatus}
                      query={{
                        starts_at: meetingIsoAt(head.date, head.from),
                        ends_at: meetingIsoAt(head.date, head.to),
                        people: head.people.length + meeting.external_attendees.length,
                      }}
                      refused={refusedRooms}
                      value={head.room}
                    />
                  </div>
                </div>

                <div className="scax-field" style={{ minWidth: 0 }}>
                  <label className="scax-field__label">
                    {meetingScreen.attendees} <span className="count-badge">{head.people.length}</span>
                  </label>
                  <PickedTags
                    items={head.people.map((person) => ({
                      key: person.member_id,
                      name: personName(person.display_name),
                      onRemove: () => setHead({ ...head, people: head.people.filter((one) => one.member_id !== person.member_id) }),
                    }))}
                  />
                  <div style={{ marginTop: 12 }}>
                    <PersonSearch
                      excluded={pickedIds}
                      onPick={(person: RosterPerson) => {
                        /* 이미 담긴 사람은 다시 담지 않는다(SPEC-010 §2.1 · OQ-909) */
                        setHead({ ...head, people: addPersonOnce(head.people, { member_id: person.member_id, display_name: person.name }) });
                        setQuery("");
                      }}
                      onQueryChange={setQuery}
                      placeholder={meetingScreen.nameSearchPlaceholder}
                      query={query}
                      roster={roster}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          <footer className="modal-foot">
            {/* 닫는 자리는 머리의 × 하나다 — 아래에 [취소]를 또 두지 않는다 (I10) */}
            {/* 기존 방을 새 조건에 쓸 수 없거나 거절 뒤 아직 안 골랐다 — 막힌 이유를 [저장] 옆에 한 줄로(H-2 · OQ-1016) */}
            {canEdit && roomStatus.reason && <span className="t-meta danger-text">{roomStatus.reason}</span>}
            <Button disabled={!canEdit || unchanged || busy || roomStatus.blocked} onClick={() => void save()} tone="primary" type="button" variant="solid">
              {meetingScreen.save}
            </Button>
          </footer>
        </section>
      </div>

      {/* 고치던 것이 있는 채로 닫을 때 — 워크스페이스의 이탈 가드와 같은 약속이다 */}
      {askClose && (
        <div className="modal-backdrop" style={{ zIndex: 60 }}>
          <section aria-label={meetingScreen.leaveTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.leaveTitle}</h3>
            </header>
            <footer className="modal-foot">
              <Button onClick={() => setAskClose(false)} type="button" variant="text">
                {meetingScreen.keep}
              </Button>
              <Button onClick={onClose} tone="danger" type="button" variant="solid">
                {meetingScreen.leave}
              </Button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
