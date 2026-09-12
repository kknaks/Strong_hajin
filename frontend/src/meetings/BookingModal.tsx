import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, bookMeeting, readMeeting, readMeetingRooms } from "../api";
import { DateField } from "../DateField";
import { Icon } from "../Icon";
import { Select } from "../Select";
import { TimeRangeField } from "../TimeField";
import { useEscape } from "../Modal";
import {
  meetingAgendaSourceText,
  meetingClock,
  meetingDateInput,
  meetingIsoAt,
  meetingScreen,
  meetingTimeOptions,
  meetingWhen,
  seoulToday,
} from "../labels";
import type { MeetingRecord, MeetingRoom, MeetingRow } from "../viewModels";
import { OrgDirectory, PersonSearch, PickedTags } from "./PeoplePicker";
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
  if (reservation.status !== "failed") return null;
  return meetingScreen.roomFailed[reservation.reason ?? ""] ?? meetingScreen.roomFailed.room_reservation_failed;
}

/** 409 로 «회의가 서지 않은» 경우다. 그 밖의 오류면 `null`. */
function roomRejectionOf(reason: unknown): { message: string; rooms: MeetingRoom[] } | null {
  if (!(reason instanceof ApiError) || reason.status !== 409) return null;
  const detail = reason.detail as { code?: string; available_rooms?: MeetingRoom[] } | undefined;
  const message = detail?.code ? meetingScreen.roomRejected[detail.code] : undefined;
  if (!message) return null;
  return { message, rooms: detail?.available_rooms ?? [] };
}

type AgendaDraft = { title: string; source: "manual" | "carried" };

function addHour(time: string): string {
  const [hour, minute] = time.split(":").map(Number);
  return `${String((hour + 1) % 24).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
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
}) {
  const today = seoulToday();
  const [subject, setSubject] = useState(initialSubject ?? "");
  const [date, setDate] = useState(today);
  const [from, setFrom] = useState(nextSlot());
  const [to, setTo] = useState(addHour(nextSlot()));
  const [purpose, setPurpose] = useState("");
  const [agendas, setAgendas] = useState<AgendaDraft[]>(initialAgendas ?? []);
  const [agendaDraft, setAgendaDraft] = useState("");
  const [people, setPeople] = useState<RosterPerson[]>([]);
  const [guests, setGuests] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [unitId, setUnitId] = useState<string | null>(null);
  /** 고른 회의실 번호. 빈 문자열이 「선택 안 함」이고 그것이 기본이다. */
  const [room, setRoom] = useState<string>("");
  const [rooms, setRooms] = useState<MeetingRoom[]>([]);
  const [round, setRound] = useState(0);
  const [suggestClosed, setSuggestClosed] = useState(false);
  const [carried, setCarried] = useState<string | null>(carriedFrom ?? null);
  const [suggestion, setSuggestion] = useState<MeetingRecord | null>(null);
  const [askClose, setAskClose] = useState(false);
  const [busy, setBusy] = useState(false);
  const { roster } = useRoster(true);

  /* 고를 수 있는 방은 예약 시스템이 정한다 — 닿지 않으면 빈 목록이고, 그때는 「선택 안 함」만 선다.
     처음에는 전부 받고, **일시를 바꾸면 그 시간에 쓸 수 있는 방만** 다시 받는다: 못 잡을 방을 고르게 두지 않는다.
     시각 눈금을 하나씩 옮길 때마다 부르지 않도록 잠깐 기다렸다 부른다 */
  const firstLoad = useRef(true);
  useEffect(() => {
    let cancelled = false;
    const load = (range?: { starts_at: string; ends_at: string }) =>
      readMeetingRooms(range)
        .then((next) => {
          if (!cancelled) setRooms(next);
        })
        .catch(() => {
          if (!cancelled) setRooms([]);
        });

    if (firstLoad.current) {
      firstLoad.current = false;
      void load();
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setTimeout(
      () => void load({ starts_at: meetingIsoAt(date, from), ends_at: meetingIsoAt(date, to) }),
      300,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [date, from, to]);

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
  const showSuggest = Boolean(picked) && !suggestClosed;

  const selectedIds = useMemo(() => new Set(people.map((person) => person.member_id)), [people]);
  const headcount = people.length + guests.length;
  const ready = trimmed.length > 0 && headcount > 0 && from < to;
  const dirty = trimmed.length > 0 || headcount > 0 || agendas.length > 0 || purpose.trim().length > 0 || room.length > 0;

  useEscape(() => (dirty ? setAskClose(true) : onClose()), !askClose);

  function togglePerson(person: RosterPerson) {
    setPeople((current) =>
      current.some((one) => one.member_id === person.member_id)
        ? current.filter((one) => one.member_id !== person.member_id)
        : [...current, person],
    );
  }

  /* E21 [불러오기] — 일시 · 장소 · 참석자 · 목적을 넣고 **결론 안 난 안건만** 넘겨 담는다 (X-121) */
  async function applySuggestion() {
    if (!picked) return;
    setBusy(true);
    try {
      const record = suggestion?.meeting.meeting_id === picked.meeting_id ? suggestion : await readMeeting(picked.meeting_id);
      setSuggestion(record);
      setDate(meetingDateInput(record.meeting.starts_at));
      setFrom(meetingClock(record.meeting.starts_at));
      setTo(meetingClock(record.meeting.ends_at));
      setPurpose((current) => current || (record.meeting.purpose ?? ""));
      setRoom(String(rooms.find((one) => one.name === record.meeting.location)?.room_id ?? ""));
      setPeople(
        record.meeting.attendees
          .map((attendee) => (roster?.people ?? []).find((person) => person.member_id === attendee.member_id))
          .filter((person): person is RosterPerson => Boolean(person)),
      );
      setAgendas((current) => {
        const next = [...current];
        for (const agenda of record.agendas) {
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
    try {
      const record = await bookMeeting({
        title: trimmed,
        purpose: purpose.trim() || null,
        starts_at: meetingIsoAt(date, from),
        ends_at: meetingIsoAt(date, to),
        // 「선택 안 함」이면 `null` 이 나가고 예약 시스템을 부르지 않는다
        room_id: room === "" ? null : Number(room),
        attendee_ids: people.map((person) => person.member_id),
        external_attendees: guests,
        agendas: agendas.map((agenda) => ({ title: agenda.title })),
        carried_from_meeting_id: carried,
      });
      onCreated(record);
    } catch (reason) {
      const rejected = roomRejectionOf(reason);
      if (rejected) {
        /* 회의가 서지 않았다 — 쓴 것을 그대로 두고 **회의실 목록만** 다시 그린다.
           고른 방은 풀어 둔다: 방금 거절당한 자리를 고른 채로 두지 않는다 */
        setRooms(rejected.rooms);
        setRoom("");
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
              <div className="field">
                <label htmlFor="meeting-subject">{meetingScreen.subject} *</label>
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
                      <button
                        className="filter-chip"
                        key={label}
                        onClick={() => {
                          setSubject(label);
                          setSuggestClosed(false);
                          setRound(0);
                        }}
                        type="button"
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}
                {showSuggest && picked && (
                  <div className="meeting-suggest">
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                      <span className="t-item">{meetingScreen.suggestTitle(trimmed)}</span>
                      <button
                        aria-label={meetingScreen.suggestClose}
                        className="btn ghost h30 icon"
                        onClick={() => setSuggestClosed(true)}
                        type="button"
                      >
                        <Icon name="close" size={14} />
                      </button>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                      <Select
                        label="회차"
                        onChange={(next) => setRound(Number(next))}
                        options={rounds.map((row, index) => ({ value: String(index), label: meetingWhen(row.starts_at) }))}
                        value={String(Math.min(round, rounds.length - 1))}
                      />
                      <span className="t-meta">{[picked.location, meetingScreen.attendCount(picked.attendee_count)].filter(Boolean).join(" · ")}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
                      <button className="btn h30" disabled={busy} onClick={() => void applySuggestion()} type="button">
                        {meetingScreen.suggestApply}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* E03 일시 · E16 [지금] — 30분 눈금. 반복 칸은 두지 않는다.
                  날짜·시각은 공용 부품이다 — 브라우저 기본 달력·드롭다운을 쓰지 않는다 */}
              <div className="field">
                <label htmlFor="meeting-date">{meetingScreen.when} *</label>
                <div className="meeting-when">
                  <DateField hideLabel id="meeting-date" label={meetingScreen.when} onChange={setDate} value={date} />
                  <TimeRangeField
                    end={to}
                    label={meetingScreen.when}
                    onChange={(next) => {
                      setFrom(next.start);
                      setTo(next.end);
                    }}
                    start={from}
                  />
                  <button
                    className="btn h30"
                    onClick={() => {
                      setDate(today);
                      setFrom(nextSlot());
                      setTo(addHour(nextSlot()));
                    }}
                    style={{ marginLeft: "auto" }}
                    type="button"
                  >
                    {meetingScreen.now}
                  </button>
                </div>
              </div>

              {/* E23 목적 */}
              <div className="field">
                <label htmlFor="meeting-purpose">{meetingScreen.purpose}</label>
                <input
                  id="meeting-purpose"
                  onChange={(event) => setPurpose(event.target.value)}
                  placeholder={meetingScreen.purposePlaceholder}
                  type="text"
                  value={purpose}
                />
              </div>

              {/* E07·E08·E09 안건 */}
              <div className="field">
                <label htmlFor="meeting-agenda">{meetingScreen.agenda}</label>
                {agendas.length > 0 && (
                  <ul className="meeting-list" style={{ margin: 0 }}>
                    {agendas.map((agenda, index) => (
                      <li className="meeting-row" key={`${agenda.title}-${index}`} style={{ padding: "8px 12px", gap: 12 }}>
                        <span className="tabular" style={{ flex: "none", fontSize: 12, color: "var(--text-tertiary)" }}>
                          {index + 1}
                        </span>
                        <span style={{ flex: 1, minWidth: 0, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {agenda.title}
                        </span>
                        <span className="t-meta" style={{ flex: "none", fontSize: 12 }}>
                          {meetingAgendaSourceText(agenda.source)}
                        </span>
                        <button
                          aria-label={meetingScreen.dropAgenda}
                          className="btn ghost h30 icon"
                          onClick={() => setAgendas((current) => current.filter((_, at) => at !== index))}
                          type="button"
                        >
                          <Icon name="close" size={14} />
                        </button>
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
                  <button
                    className="btn"
                    disabled={agendas.length >= 20 || agendaDraft.trim().length === 0}
                    onClick={() => {
                      setAgendas((current) => [...current, { title: agendaDraft.trim(), source: "manual" }]);
                      setAgendaDraft("");
                    }}
                    type="button"
                  >
                    {meetingScreen.addAgenda}
                  </button>
                </div>
              </div>

              {/* E05 참석자 · E06 사외 참석자 */}
              <div className="field">
                <label>
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

              {/* E04 장소 — 이름·정원만 낸 정적 목록 */}
              <div className="field">
                <label>{meetingScreen.place}</label>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {/* 장소를 안 고르는 자리가 맨 위에 서고 그것이 기본이다 — 회의실을 잡지 않는 회의가 흔하다 */}
                  <label className={room === "" ? "meeting-room none on" : "meeting-room none"}>
                    <input checked={room === ""} name="meeting-room" onChange={() => setRoom("")} type="radio" />
                    <span style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 700 }}>{meetingScreen.noRoom}</span>
                  </label>
                  {rooms.map((one) => (
                    <label className={String(one.room_id) === room ? "meeting-room on" : "meeting-room"} key={one.room_id}>
                      <input
                        checked={String(one.room_id) === room}
                        name="meeting-room"
                        onChange={() => setRoom(String(one.room_id))}
                        type="radio"
                      />
                      <span style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 700 }}>{one.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <footer className="modal-foot">
            {/* 갈래를 두지 않는다 — 장소에 「선택 안 함」이 있으니 단추가 둘일 이유가 없다 */}
            {/* 예약 시스템이 20초까지 붙잡을 수 있다 — 무엇을 기다리는지 단추가 말한다. 다시 걸지 않는다 */}
            <button className="btn h40 primary" disabled={!ready || busy} onClick={() => void submit()} type="button">
              {busy && room !== "" ? meetingScreen.booking : meetingScreen.createMeeting}
            </button>
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
              <button className="btn h40 ghost" onClick={() => setAskClose(false)} type="button">
                {meetingScreen.keep}
              </button>
              <button className="btn h40 danger" onClick={onClose} type="button">
                {meetingScreen.discard}
              </button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
