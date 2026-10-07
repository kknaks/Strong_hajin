import { useMemo, useRef, useState, type ReactNode } from "react";

import { Badge } from "../../ds/Badge";
import { Chip } from "../../ds/Chip";
import { Button, IconButton } from "../../ds/Button";
import { ApiError, bookMeeting, readMeeting } from "../../lib/api";
import { createIdempotencyKey } from "../../lib/idempotency";
import { DateField } from "../../ds/DateField";
import { Icon } from "../../ds/icons/Icon";
import { Select } from "../../ds/Select";
import { TimeRangeField } from "../../ds/TimeField";
import { useEscape } from "../../ds/Modal";
import { addDays, axDraftCard, datePickerLabel, emptyActionLabel, formatMonthLong, meetingAgendaSourceText, meetingClock, meetingDateInput, meetingIsoAt, meetingScreen, meetingTimeOptions, meetingWhen, selectLabel, seoulToday, timeFieldLabel, weekdayNames } from "../../lib/labels";
import type { MeetingRecord, MeetingRoom, MeetingRow } from "../../lib/viewModels";
import { OrgDirectory, PersonSearch, PickedTags } from "./PeoplePicker";
import { RoomSelect, roomChoiceId, type RoomChoice, type RoomSelectStatus } from "./RoomSelect";
import { useRoster, type RosterPerson } from "./roster";

/**
 * 회의실 예약이 안 됐을 때 낼 한 줄. 잘 됐거나 안 잡은 회의면 `null` 이다.
 * **회의는 이미 만들어졌다** — 이 문장은 장소가 왜 비었는지만 말한다.
 */
export function roomReservationNotice(record: MeetingRecord): string | null {
  const reservation = record.meeting.room_reservation;
  if (!reservation) return null;
  // 고른 방이 안 돼 다른 방으로 잡혔다 — 어디로 잡혔는지 말하지 않으면 사람이 모른 채 간다
  if (reservation.status === "booked") {
    return reservation.replaced && reservation.room_name ? meetingScreen.roomReplaced(reservation.room_name) : null;
  }
  if (!["failed", "needs_verification"].includes(reservation.status)) return null;
  return meetingScreen.roomFailed[reservation.reason ?? ""] ?? meetingScreen.roomFailed.room_reservation_failed;
}

/**
 * 409 로 «회의실 때문에 서지 않은» 경우다(생성 `room_unavailable` 등 · 수정 `ROOM_BOOKING_REFUSED` — WP3 계약 고정 2).
 * 코드가 아는 것이거나 `available_rooms` 배열이 실려 오면 거절로 본다. 그 밖의 오류면 `null`(겹침 409 는 `detail` 이 문자열이다).
 * 수정 모달·AX 수정 카드도 같은 판정을 쓴다.
 */
/**
 * 회의 저장(수정 모달 · AX 수정 카드)의 오류 문구 — **코드별 문구**가 있으면 그것(`meetingScreen.saveErrors` · 계약 고정 §7),
 * 모르는 코드면 서버가 실은 `detail.message`, 그것도 없으면 오류 글자 · 기본 문구. 겹침 409 처럼 `detail` 이 문자열이면 그 문자열이 정본이다(K22).
 */
export function meetingErrorText(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError && reason.detail && typeof reason.detail === "object") {
    const detail = reason.detail as { code?: string; message?: string };
    const known = detail.code ? meetingScreen.saveErrors[detail.code] : undefined;
    if (known) return known;
    if (detail.message) return detail.message;
  }
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function roomRejectionOf(reason: unknown): { message: string; rooms: MeetingRoom[] } | null {
  if (!(reason instanceof ApiError) || reason.status !== 409) return null;
  const detail = reason.detail as { code?: string; message?: string; available_rooms?: MeetingRoom[] } | undefined;
  if (!detail || typeof detail !== "object") return null;
  const known = detail.code ? meetingScreen.roomRejected[detail.code] : undefined;
  if (!known && !Array.isArray(detail.available_rooms)) return null;
  return { message: known ?? detail.message ?? meetingScreen.roomRejected.ROOM_BOOKING_REFUSED, rooms: detail.available_rooms ?? [] };
}

type AgendaDraft = { title: string; source: "manual" | "carried" };

/** 생성 입력 한 벌 — 생성(`bookMeeting`)과 AX 초안 저장(`draft.onSubmit`)이 같은 모양을 싣는다. */
export type BookingInput = {
  title: string;
  purpose: string | null;
  starts_at: string;
  ends_at: string;
  room_id: number | null;
  attendee_ids: string[];
  external_attendees: string[];
  agendas: Array<{ title: string; source: "manual" | "carried" }>;
  carried_from_meeting_id: string | null;
};

/**
 * AX 회의 생성 카드의 [수정] = **이 모달을 편집 창으로** 연다(SPEC-010 §2.4 · OQ-1001). 값은 편집 계약(`values`)에서 채우고,
 * 주 단추는 「저장」 — 회의를 만들지 않고 **초안의 새 회차**를 저장한다(`onSubmit`). 지난 회의 제안 카드는 서지 않는다.
 */
export type BookingDraft = {
  values: Record<string, unknown>;
  /** 참석자 이름 — 편집 계약의 선택지(`attendee_ids.options`). 명부를 기다리지 않고 바로 태그를 세운다. */
  attendeeOptions: Array<{ value: string; label: string }>;
  onSubmit: (input: BookingInput) => Promise<void>;
  error?: string | null;
  /** 첨부 초안 자리(D-05) — 카드가 만든 `TaskAttachmentGroup` 을 그대로 꽂는다. */
  attachments?: ReactNode;
};

const draftText = (value: unknown) => (value === null || value === undefined ? "" : String(value));
const draftList = (value: unknown) => (Array.isArray(value) ? value.map(String).filter(Boolean) : []);
function draftAgendas(value: unknown): AgendaDraft[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((row): { title?: unknown; source?: unknown } => (row && typeof row === "object" ? (row as { title?: unknown; source?: unknown }) : { title: row }))
    .map((row) => ({ title: draftText(row.title).trim(), source: row.source === "carried" ? ("carried" as const) : ("manual" as const) }))
    .filter((row) => row.title.length > 0);
}

function addHour(time: string): string {
  const [hour, minute] = time.split(":").map(Number);
  return `${String((hour + 1) % 24).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/**
 * 끝이 시작보다 «뒤가 아니면» 그 회의는 자정을 넘는다 — 끝은 다음 날이다 (바퀴 13).
 *
 * `from`·`to` 는 `"HH:MM"` 문자열이라 `from < to` 하나로는 자정 넘김을 모른다. 23:30 에 시작해
 * 00:30 에 끝나는 회의가 「끝이 시작보다 앞」으로 읽혀 예약 자체가 막혀 있었다(23:00~23:30 에
 * 예약 모달을 열면 기본값이 그 꼴이라 아무 회의도 못 만들었다).
 *
 * 같은 시각(`to === from`)은 0분인지 24시간인지 알 수 없어 여전히 안 받는다 —
 * 그 판단은 `ready` 가 한다. 이 함수는 «날짜가 하루 넘어가는가» 만 말한다.
 */
function endsNextDay(from: string, to: string): boolean {
  return to < from;
}

/** 끝 시각이 실제로 놓이는 날짜. 자정을 넘으면 하루 뒤다. */
function endDateOf(date: string, from: string, to: string): string {
  return endsNextDay(from, to) ? addDays(date, 1) : date;
}

/** 지금에서 30분 눈금으로 올린 시각 — [지금]이 쓰는 값이다. */
function nextSlot(): string {
  const now = new Date();
  const minutes = Number(now.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" }).slice(3));
  const hour = now.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" }).slice(0, 2);
  return minutes === 0 ? `${hour}:00` : minutes <= 30 ? `${hour}:30` : addHour(`${hour}:00`);
}

/**
 * MOD-102 회의 예약 — 드로어가 아니라 **모달**이다.
 *
 * 칸 순서는 주제 · 일시 · 목적 · 안건 · 참석자 · 장소 (2026-09-10 사용자 결정). **반복 칸은 없다.**
 * 사외 참석자는 따로 칸을 두지 않고 이름 찾기 안에서 단다. 닫는 자리는 머리의 `×` 하나이고
 * (`ESC` · 바깥 클릭이 같다) 쓴 것이 있으면 한 번 묻는다 — 아래에 [취소]를 또 두지 않는다 (I10).
 */
export function BookingModal({
  pastRows,
  initialSubject,
  initialAgendas,
  carriedFrom,
  onClose,
  onCreated,
  onError,
  onNotice,
  draft,
}: {
  /** 제안 카드와 「최근」 칩이 딛는 지난 회의들. */
  pastRows: MeetingRow[];
  initialSubject?: string;
  initialAgendas?: AgendaDraft[];
  carriedFrom?: string | null;
  onClose: () => void;
  onCreated: (record: MeetingRecord) => void;
  onError: (message: string) => void;
  /** 회의실이 거절됐을 때 한 줄로 말한다 — 모달은 그대로 열려 있다. */
  onNotice: (message: string) => void;
  /** AX 회의 생성 카드의 편집 창으로 연다 — 주 단추가 「저장」(초안 저장)이 된다. */
  draft?: BookingDraft;
}) {
  const today = seoulToday();
  const values = draft?.values ?? null;
  const draftStart = values ? draftText(values.starts_at) : "";
  const draftEnd = values ? draftText(values.ends_at) : "";
  const [subject, setSubject] = useState(values ? draftText(values.title) : initialSubject ?? "");
  const [date, setDate] = useState(draftStart ? meetingDateInput(draftStart) : today);
  const [from, setFrom] = useState(draftStart ? meetingClock(draftStart) : nextSlot());
  const [to, setTo] = useState(draftEnd ? meetingClock(draftEnd) : addHour(nextSlot()));
  const [purpose, setPurpose] = useState(values ? draftText(values.purpose) : "");
  const [agendas, setAgendas] = useState<AgendaDraft[]>(values ? draftAgendas(values.agendas) : initialAgendas ?? []);
  const [agendaDraft, setAgendaDraft] = useState("");
  const [people, setPeople] = useState<RosterPerson[]>(() =>
    values
      ? draftList(values.attendee_ids).map((id) => ({
          member_id: id,
          name: draft?.attendeeOptions.find((option) => option.value === id)?.label ?? id,
          unit_id: "",
          unit: "",
          rank: "",
        }))
      : [],
  );
  const [guests, setGuests] = useState<string[]>(values ? draftList(values.external_attendees) : []);
  const [query, setQuery] = useState("");
  const [unitId, setUnitId] = useState<string | null>(null);
  /** 고른 회의실 — `"none"`(회의실 예약 없음)이 기본이다. 고르는 자리는 공용 셀렉트(`RoomSelect` · SPEC-010 §2.2). */
  const [room, setRoom] = useState<RoomChoice>(values && values.room_id !== null && values.room_id !== undefined && values.room_id !== "" ? (String(values.room_id) as RoomChoice) : "none");
  /** 저장이 409 로 거절됐을 때 서버가 준 「지금 가능한 방」 — 셀렉트가 그 목록으로 다시 선다. */
  const [refusedRooms, setRefusedRooms] = useState<MeetingRoom[] | null>(null);
  /** 불러온 지난 회의의 방 이름 — 셀렉트가 그 방을 미리 고르고, 새 시간·인원에 못 쓰면 이유를 말한다(WP1 검수 W-3). */
  const [requestedRoom, setRequestedRoom] = useState<string | null>(null);
  const [roomStatus, setRoomStatus] = useState<RoomSelectStatus>({ state: "loading", blocked: false, reason: null });
  const [round, setRound] = useState(0);
  const [suggestClosed, setSuggestClosed] = useState(false);
  const [carried, setCarried] = useState<string | null>(values ? draftText(values.carried_from_meeting_id) || null : carriedFrom ?? null);
  const [suggestion, setSuggestion] = useState<MeetingRecord | null>(null);
  const [askClose, setAskClose] = useState(false);
  const [busy, setBusy] = useState(false);
  const submitAttempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const { roster } = useRoster(true);

  const trimmed = subject.trim();
  /* E24 최근 회의 — 주제 칸이 비었을 때만 선다. 지난 목록에서 주제를 훑어 최신 둘을 낸다 */
  const recent = useMemo(() => {
    const seen: string[] = [];
    for (const row of pastRows) {
      if (row.title && !seen.includes(row.title)) seen.push(row.title);
      if (seen.length === 2) break;
    }
    return seen;
  }, [pastRows]);

  /* E20 제안 카드 — 주제가 «정확히 같은» 지난 회의가 있을 때만 (X-116) */
  const rounds = useMemo(
    () => (trimmed ? pastRows.filter((row) => row.title === trimmed) : []),
    [pastRows, trimmed],
  );
  const picked = rounds[Math.min(round, Math.max(rounds.length - 1, 0))] ?? null;
  /* AX 초안 편집 창에서는 지난 회의 제안을 세우지 않는다 — 그 초안은 AX 가 지은 것이다 */
  const showSuggest = !draft && Boolean(picked) && !suggestClosed;

  const selectedIds = useMemo(() => new Set(people.map((person) => person.member_id)), [people]);
  const headcount = people.length + guests.length;
  /* 바퀴 13: 자정을 넘는 회의를 허용한다(사용자 결정). 끝이 시작보다 앞서 보이면 그것은
     「다음 날」이지 잘못된 값이 아니다. 다만 시작과 끝이 같은 시각인 것은 여전히 안 받는다. */
  const crossesMidnight = endsNextDay(from, to);
  /* 회의실 셀렉트가 막았으면(고른 방이 새 조건 목록에서 빠짐 · 거절 뒤 아직 안 고름) 만들지 않는다 — 조용히 「예약 없음」 으로 가지 않는다 */
  const ready = trimmed.length > 0 && headcount > 0 && from !== to && !roomStatus.blocked;
  const dirty = trimmed.length > 0 || headcount > 0 || agendas.length > 0 || purpose.trim().length > 0 || room !== "none";
  /* 셀렉트가 딛는 조건 — 날짜·시작·종료·참석 인원(사내+사외). 바뀌면 셀렉트가 300ms 뒤 다시 받는다 */
  const roomQuery = { starts_at: meetingIsoAt(date, from), ends_at: meetingIsoAt(endDateOf(date, from, to), to), people: headcount };

  useEscape(() => (dirty ? setAskClose(true) : onClose()), !askClose);

  function togglePerson(person: RosterPerson) {
    setPeople((current) =>
      current.some((one) => one.member_id === person.member_id)
        ? current.filter((one) => one.member_id !== person.member_id)
        : [...current, person],
    );
  }

  /* E21 [불러오기] — 장소 · 참석자 · 목적을 넣고 **결론 안 난 안건만** 넘겨 담는다 (X-121).
     **날짜·시작·종료는 건드리지 않는다**(SPEC-010 §2.3 · D-10) — 새 회의에서 정한(또는 비어 있는) 시간을 그대로 둔다 */
  async function applySuggestion() {
    if (!picked) return;
    setBusy(true);
    try {
      const record = suggestion?.meeting.meeting_id === picked.meeting_id ? suggestion : await readMeeting(picked.meeting_id);
      setSuggestion(record);
      setPurpose((current) => current || (record.meeting.purpose ?? ""));
      /* 방은 이름으로 «요청»만 한다 — 셀렉트가 새 시간·인원의 가용 목록에서 찾아 고르고, 못 쓰면 이유를 한 줄로 말한다(W-3) */
      setRequestedRoom(record.meeting.location ?? null);
      setPeople(
        record.meeting.attendees
          .map((attendee) => (roster?.people ?? []).find((person) => person.member_id === attendee.member_id))
          .filter((person): person is RosterPerson => Boolean(person)),
      );
      setAgendas((current) => {
        const next = [...current];
        /* **최종 벌만 이어 간다** (사용자 결정 2026-09-14 ②). `readMeeting` 이 내는 `agendas` 는
           세 벌 합본이라 그대로 훑으면 임시 재료(사람 벌·AI 벌)까지 다음 회의로 실려 간다 —
           화면엔 최종 2개인데 모달엔 5개가 서던 자리다. 상세 화면이 넘기는 `initialAgendas` 쪽은
           이미 걸러져 오지만, 이 길(지난 회의 이어가기)은 서버 응답을 직접 훑으므로 여기서 거른다. */
        for (const agenda of record.agendas) {
          if (agenda.track !== "final") continue;
          if (agenda.concluded) continue;
          if (!next.some((one) => one.title === agenda.title)) next.push({ title: agenda.title, source: "carried" });
        }
        return next;
      });
      setCarried(record.meeting.meeting_id);
      setSuggestClosed(true);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "지난 회의를 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!ready || busy) return;
    setBusy(true);
    const input: BookingInput = {
      title: trimmed,
      purpose: purpose.trim() || null,
      starts_at: meetingIsoAt(date, from),
      // 바퀴 13: 자정을 넘으면 끝은 다음 날이다 — 날짜 하나로 보내면 끝이 시작보다 앞선 값이 나간다
      ends_at: meetingIsoAt(endDateOf(date, from, to), to),
      // 「선택 안 함」이면 `null` 이 나가고 예약 시스템을 부르지 않는다
      room_id: roomChoiceId(room),
      attendee_ids: people.map((person) => person.member_id),
      external_attendees: guests,
      /* 출처는 **안건마다** 보낸다(SPEC-010 §2.3 · §4.2) — 불러온 미결 안건 = `carried` · 손으로 쓴 안건 = `manual` */
      agendas: agendas.map((agenda) => ({ title: agenda.title, source: agenda.source })),
      carried_from_meeting_id: carried,
    };
    if (draft) {
      /* AX 초안 저장 — 회의를 만들지 않는다. 실패 문구는 부르는 쪽(카드)이 `draft.error` 로 돌려준다 */
      try {
        await draft.onSubmit(input);
      } finally {
        setBusy(false);
      }
      return;
    }
    const fingerprint = JSON.stringify(input);
    if (submitAttempt.current?.fingerprint !== fingerprint) {
      submitAttempt.current = { fingerprint, key: createIdempotencyKey() };
    }
    try {
      const record = await bookMeeting(input, submitAttempt.current.key);
      onCreated(record);
    } catch (reason) {
      const rejected = roomRejectionOf(reason);
      if (rejected) {
        /* 회의가 서지 않았다 — 쓴 것을 그대로 두고 **회의실 목록만** 다시 그린다.
           고른 방은 풀어 둔다: 방금 거절당한 자리를 고른 채로 두지 않는다 */
        setRefusedRooms(rejected.rooms);
        setRoom("none");
        submitAttempt.current = null;
        onNotice(rejected.message);
      } else {
        onError(reason instanceof Error ? reason.message : "회의를 만들지 못했습니다.");
      }
      setBusy(false);
    }
  }

  return (
    <>
      <div
        className="modal-backdrop"
        onMouseDown={(event) => event.target === event.currentTarget && (dirty ? setAskClose(true) : onClose())}
      >
        <section
          aria-label={meetingScreen.bookTitle}
          aria-modal="true"
          className="modal meeting-modal-wide"
          role="dialog"
        >
          <header className="modal-head">
            <h3>{meetingScreen.bookTitle}</h3>
            <button aria-label="닫기" className="modal-close" onClick={() => (dirty ? setAskClose(true) : onClose())} type="button">
              <Icon name="close" size={20} />
            </button>
          </header>

          <div className="modal-body meeting-scroll">
            <div className="form-stack">
              {/* E02 주제 · E24 최근 · E20 제안 카드 */}
              <div className="scax-field">
                <label className="scax-field__label" htmlFor="meeting-subject">{meetingScreen.subject} *</label>
                <input
                  autoFocus
                  className="title-input"
                  id="meeting-subject"
                  onChange={(event) => {
                    setSubject(event.target.value);
                    setSuggestClosed(false);
                    setRound(0);
                  }}
                  placeholder={meetingScreen.subjectPlaceholder}
                  type="text"
                  value={subject}
                />
                {trimmed.length === 0 && recent.length > 0 && (
                  <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
                    <span className="t-meta" style={{ fontSize: 12 }}>
                      {meetingScreen.recent}
                    </span>
                    {recent.map((label) => (
                      <Chip
                        key={label}
                        label={label}
                        onClick={() => {
                          setSubject(label);
                          setSuggestClosed(false);
                          setRound(0);
                        }}
                      />
                    ))}
                  </div>
                )}
                {showSuggest && picked && (
                  <div className="meeting-suggest">
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                      <span className="t-item">{meetingScreen.suggestTitle(trimmed)}</span>
                      <IconButton name="close" size={14} label={meetingScreen.suggestClose} onClick={() => setSuggestClosed(true)} />
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                      <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
                        label="회차"
                        onChange={(next) => setRound(Number(next))}
                        options={rounds.map((row, index) => ({ value: String(index), label: meetingWhen(row.starts_at) }))}
                        value={String(Math.min(round, rounds.length - 1))}
                      />
                      <span className="t-meta">{[picked.location, meetingScreen.attendCount(picked.attendee_count)].filter(Boolean).join(" · ")}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
                      <Button size="sm" disabled={busy} onClick={() => void applySuggestion()} type="button">
                        {meetingScreen.suggestApply}
                      </Button>
                    </div>
                  </div>
                )}
              </div>

              {/* E03 일시 · E16 [지금] — 30분 눈금. 반복 칸은 두지 않는다.
                  날짜·시각은 공용 부품이다 — 브라우저 기본 달력·드롭다운을 쓰지 않는다 */}
              <div className="scax-field">
                <label className="scax-field__label" htmlFor="meeting-date">{meetingScreen.when} *</label>
                <div className="meeting-when">
                  <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames} hideLabel id="meeting-date" label={meetingScreen.when} onChange={setDate} value={date} />
                  <TimeRangeField
            emptyActionLabel={emptyActionLabel.filter}
            endLabel={timeFieldLabel.end}
            labels={timeFieldLabel}
            selectLabels={selectLabel}
            startLabel={timeFieldLabel.start}
                    end={to}
                    label={meetingScreen.when}
                    onChange={(next) => {
                      setFrom(next.start);
                      setTo(next.end);
                    }}
                    start={from}
                  />
                  {/* 바퀴 13: 끝이 다음 날이면 그렇다고 말한다. 시안에 이 자리가 없어서(자정 넘김을
                      아예 못 만들던 화면이다) 가장 작은 표시 하나를 종료 시각 옆에 둔다 — 새 칸도
                      새 색도 만들지 않고, 이미 있는 `Badge` 의 중립 톤이다. */}
                  {crossesMidnight && <Badge tone="neutral">{meetingScreen.nextDay}</Badge>}
                  <Button size="sm" onClick={() => {
                      setDate(today);
                      setFrom(nextSlot());
                      setTo(addHour(nextSlot()));
                    }}
                    style={{ marginLeft: "auto" }}
                    type="button"
                  >
                    {meetingScreen.now}
                  </Button>
                </div>
              </div>

              {/* E23 목적 */}
              <div className="scax-field">
                <label className="scax-field__label" htmlFor="meeting-purpose">{meetingScreen.purpose}</label>
                <input
                  id="meeting-purpose"
                  onChange={(event) => setPurpose(event.target.value)}
                  placeholder={meetingScreen.purposePlaceholder}
                  type="text"
                  value={purpose}
                />
              </div>

              {/* E07·E08·E09 안건 */}
              <div className="scax-field">
                <label className="scax-field__label" htmlFor="meeting-agenda">{meetingScreen.agenda}</label>
                {agendas.length > 0 && (
                  <ul className="meeting-list" style={{ margin: 0 }}>
                    {agendas.map((agenda, index) => (
                      <li className="meeting-row" key={`${agenda.title}-${index}`} style={{ padding: "8px 12px", gap: 12 }}>
                        <span className="tabular" style={{ flex: "none", fontSize: 12, color: "var(--scax-color-ink-assistive)" }}>
                          {index + 1}
                        </span>
                        <span style={{ flex: 1, minWidth: 0, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {agenda.title}
                        </span>
                        <span className="t-meta" style={{ flex: "none", fontSize: 12 }}>
                          {meetingAgendaSourceText(agenda.source)}
                        </span>
                        <IconButton name="close" size={14} label={meetingScreen.dropAgenda} onClick={() => setAgendas((current) => current.filter((_, at) => at !== index))} />
                      </li>
                    ))}
                  </ul>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <input
                    id="meeting-agenda"
                    onChange={(event) => setAgendaDraft(event.target.value)}
                    placeholder={meetingScreen.agendaPlaceholder}
                    style={{ flex: 1 }}
                    type="text"
                    value={agendaDraft}
                  />
                  <Button size="sm" disabled={agendas.length>= 20 || agendaDraft.trim().length === 0}
                    onClick={() => {
                      setAgendas((current) => [...current, { title: agendaDraft.trim(), source: "manual" }]);
                      setAgendaDraft("");
                    }}
                    type="button"
                  >
                    {meetingScreen.addAgenda}
                  </Button>
                </div>
              </div>

              {/* E05 참석자 · E06 사외 참석자 */}
              <div className="scax-field">
                <label className="scax-field__label">
                  {meetingScreen.attendees} * <span className="count-badge">{headcount}명</span>
                </label>
                <PickedTags
                  items={[
                    ...people.map((person) => ({
                      key: person.member_id,
                      name: person.name,
                      note: person.unit,
                      onRemove: () => togglePerson(person),
                    })),
                    ...guests.map((name) => ({
                      key: `guest-${name}`,
                      name,
                      note: meetingScreen.guest,
                      onRemove: () => setGuests((current) => current.filter((one) => one !== name)),
                    })),
                  ]}
                />
                <div style={{ marginTop: 8 }}>
                  <PersonSearch
                    excluded={selectedIds}
                    guest={{
                      canAdd: (name) => !guests.includes(name),
                      onAdd: (name) => {
                        setGuests((current) => [...current, name]);
                        setQuery("");
                      },
                    }}
                    onPick={(person) => {
                      togglePerson(person);
                      setQuery("");
                    }}
                    onQueryChange={setQuery}
                    placeholder={meetingScreen.peopleSearchPlaceholder}
                    query={query}
                    roster={roster}
                  />
                </div>
                <div style={{ marginTop: 8 }}>
                  <OrgDirectory
                    mode="check"
                    onToggle={togglePerson}
                    onUnitChange={setUnitId}
                    roster={roster}
                    selected={selectedIds}
                    unitId={unitId}
                  />
                </div>
              </div>

              {/* E04 장소 — 공용 회의실 셀렉트(SPEC-010 §2.2). 「회의실 예약 없음」 이 기본이고, 그 시간·인원의 가용 방만 선다 */}
              <div className="scax-field">
                <label className="scax-field__label">{meetingScreen.place}</label>
                <RoomSelect
                  onChange={(next) => {
                    setRoom(next);
                    submitAttempt.current = null;
                  }}
                  onStatus={setRoomStatus}
                  query={roomQuery}
                  refused={refusedRooms}
                  requestedName={requestedRoom ?? (values ? draftText(values.room_name) || null : null)}
                  value={room}
                />
              </div>
              {draft?.attachments}
            </div>
          </div>

          <footer className="modal-foot">
            {/* 갈래를 두지 않는다 — 장소에 「선택 안 함」이 있으니 단추가 둘일 이유가 없다 */}
            {/* 예약 시스템이 20초까지 붙잡을 수 있다 — 무엇을 기다리는지 단추가 말한다. 다시 걸지 않는다 */}
            {draft?.error && <span className="t-meta danger-text" role="alert">{draft.error}</span>}
            {roomStatus.reason && <span className="t-meta danger-text">{roomStatus.reason}</span>}
            <Button variant="solid" tone="primary" disabled={!ready || busy} onClick={() => void submit()} type="button">
              {draft ? (busy ? axDraftCard.saving : axDraftCard.save) : busy && room !== "none" ? meetingScreen.booking : meetingScreen.createMeeting}
            </Button>
          </footer>
        </section>
      </div>

      {/* TXT-004 — 쓴 것이 있는 채로 닫을 때 */}
      {askClose && (
        <div className="modal-backdrop" style={{ zIndex: 60 }}>
          <section aria-label={meetingScreen.discardTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.discardTitle}</h3>
            </header>
            <footer className="modal-foot">
              <Button variant="text" onClick={() => setAskClose(false)} type="button">
                {meetingScreen.keep}
              </Button>
              <Button variant="solid" tone="danger" onClick={onClose} type="button">
                {meetingScreen.discard}
              </Button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
