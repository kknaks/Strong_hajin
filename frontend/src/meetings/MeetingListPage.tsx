import { useCallback, useEffect, useState } from "react";

import { listMeetings, meetingExportUrl, quickStartMeeting, readMeeting, removeMeeting } from "../api";
import { Empty, EmptyValue } from "../Empty";
import { Icon } from "../Icon";
import { Skeleton } from "../Skeleton";
import {
  meetingAgendaSourceText,
  meetingRange,
  meetingScreen,
  meetingStatusClass,
  meetingStatusLabel,
  meetingWhen,
} from "../labels";
import type { MeetingListPayload, MeetingRecord, MeetingRow } from "../viewModels";
import { AgendaBlock } from "./AgendaBlock";
import { BookingModal, roomReservationNotice } from "./BookingModal";
import { ShareModal } from "./ShareModal";

/**
 * SCR-105 회의 목록 — 왼쪽 목록, 오른쪽 **읽기 전용** 회의록 패널.
 *
 * 상태는 모든 행에 낸다 (2026-09-10 디자인 피드백). 「열람」은 상태가 아니라 접근 표시라 상태와 나란히
 * 함께 서고, `viewer_relation` 하나로 갈린다 — 역할로 추론하지 않는다. 패널에는 조작 버튼도,
 * 후속업무 후보 건수 줄도 두지 않는다 (X-133) — 같은 사실을 두 자리에 내지 않는다.
 */
export function MeetingListPage({
  onOpenMeeting,
  onError,
  onNotice,
  onRegisterRefresh,
  focusMeetingId,
  onFocusHandled,
}: {
  onOpenMeeting: (meetingId: string) => void;
  onError: (message: string | null) => void;
  onNotice: (message: string) => void;
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  focusMeetingId?: string | null;
  onFocusHandled?: () => void;
}) {
  const [payload, setPayload] = useState<MeetingListPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [record, setRecord] = useState<MeetingRecord | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const [confirm, setConfirm] = useState<MeetingRow | null>(null);
  const [booking, setBooking] = useState(false);
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setFailed(false);
    const next = await listMeetings();
    setPayload(next);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void reload().catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  useEffect(() => {
    onRegisterRefresh?.(reload);
    return () => onRegisterRefresh?.(null);
  }, [onRegisterRefresh, reload]);

  useEffect(() => {
    if (!focusMeetingId) return;
    setSelected(focusMeetingId);
    onFocusHandled?.();
  }, [focusMeetingId, onFocusHandled]);

  // 고른 회의의 회의록은 그때 읽는다 — 목록 응답에는 안건이 없다.
  useEffect(() => {
    if (!selected) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    setRecord(null);
    void readMeeting(selected)
      .then((next) => {
        if (!cancelled) setRecord(next);
      })
      .catch((reason: unknown) => {
        if (!cancelled) onError(reason instanceof Error ? reason.message : "회의록을 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, selected]);

  async function loadMore() {
    const cursor = payload?.past.next_cursor;
    if (!cursor || moreBusy) return;
    setMoreBusy(true);
    try {
      const next = await listMeetings(cursor);
      setPayload((current) =>
        current
          ? { upcoming: next.upcoming.length ? next.upcoming : current.upcoming, past: { items: [...current.past.items, ...next.past.items], next_cursor: next.past.next_cursor } }
          : next,
      );
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "회의를 더 불러오지 못했습니다.");
    } finally {
      setMoreBusy(false);
    }
  }

  async function drop(scope: "meeting" | "note") {
    if (!confirm || busy) return;
    setBusy(true);
    try {
      await removeMeeting(confirm.meeting_id, scope);
      if (selected === confirm.meeting_id) setSelected(null);
      setConfirm(null);
      await reload();
      onNotice(scope === "meeting" ? meetingScreen.deletedMeeting : meetingScreen.deletedNote);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "삭제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function quickStart() {
    if (busy) return;
    setBusy(true);
    try {
      const next = await quickStartMeeting();
      onOpenMeeting(next.meeting.meeting_id);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "회의를 시작하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const upcoming = payload?.upcoming ?? [];
  const past = payload?.past.items ?? [];
  const isEmpty = Boolean(payload) && upcoming.length === 0 && past.length === 0;

  return (
    /* `meeting-list-surface` 는 이 화면의 스타일이 닿는 범위다 — 조용한 포커스와 푸터 여백이
       회의 상세(회의실)까지 번지지 않게 한다. 예약·삭제 모달도 이 안에 서므로 함께 걸린다 */
    <section className="page-surface meeting-surface meeting-list-surface">
      <div className="page-head">
        <div>
          <h1 className="meeting-page-title">{meetingScreen.title}</h1>
        </div>
        <div className="page-head-actions">
          <button className="btn primary" disabled={busy} onClick={() => void quickStart()} type="button">
            <Icon name="play" size={14} /> {meetingScreen.start}
          </button>
          <button className="btn" onClick={() => setBooking(true)} type="button">
            {meetingScreen.book}
          </button>
        </div>
      </div>

      <div className="meeting-columns">
        <section aria-label={meetingScreen.title} className="meeting-panel">
          {failed ? (
            <div className="meeting-panel-center">
              <Empty onAction={() => void reload().catch(() => setFailed(true))} title={meetingScreen.listError} variant="error" />
            </div>
          ) : payload === null ? (
            <div style={{ padding: 20 }}>
              <Skeleton label="회의를 불러오는 중" />
            </div>
          ) : isEmpty ? (
            <div className="meeting-panel-center">
              <Empty title={meetingScreen.listEmpty} />
            </div>
          ) : (
            <div className="scroll-hidden" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0 16px 20px" }}>
              {upcoming.length > 0 && (
                <>
                  <div className="meeting-section-head">
                    <h2 id="meeting-upcoming">{meetingScreen.upcoming}</h2>
                    <span className="tabular" style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                      {upcoming.length}
                    </span>
                  </div>
                  <ul aria-labelledby="meeting-upcoming" className="meeting-list">
                    {upcoming.map((row) => (
                      <MeetingRowItem key={row.meeting_id} onAskDelete={setConfirm} onSelect={setSelected} row={row} selected={selected === row.meeting_id} />
                    ))}
                  </ul>
                </>
              )}
              {past.length > 0 && (
                <>
                  <div className="meeting-section-head" style={{ paddingTop: 20 }}>
                    <h2 id="meeting-past">{meetingScreen.past}</h2>
                  </div>
                  <ul aria-labelledby="meeting-past" className="meeting-list">
                    {past.map((row) => (
                      <MeetingRowItem key={row.meeting_id} onAskDelete={setConfirm} onSelect={setSelected} row={row} selected={selected === row.meeting_id} />
                    ))}
                  </ul>
                  {payload.past.next_cursor && (
                    <div style={{ display: "flex", justifyContent: "center", padding: "16px 0 4px" }}>
                      <button className="btn" disabled={moreBusy} onClick={() => void loadMore()} type="button">
                        {meetingScreen.more}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </section>

        <NotePanel
          onOpen={onOpenMeeting}
          onShare={() => setShare(true)}
          record={record}
          selected={selected}
        />
      </div>

      {/* T08 삭제 확인 — 닫는 자리는 머리의 × 하나다. 아래에 [취소]를 또 두지 않는다 */}
      {confirm && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setConfirm(null)}>
          <section aria-label={meetingScreen.deleteTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.deleteTitle}</h3>
              <button aria-label="닫기" autoFocus className="modal-close" onClick={() => setConfirm(null)} type="button">
                <Icon name="close" size={20} />
              </button>
            </header>
            <div className="modal-body">
              <p>{meetingScreen.deleteBody}</p>
            </div>
            <footer className="modal-foot">
              {/* 갈래를 두지 않는다 — 회의를 취소하는 자리 하나다 (회의록만 지우는 길은 API 에 남아 있다) */}
              <button className="btn h40 danger" disabled={busy} onClick={() => void drop("meeting")} type="button">
                {meetingScreen.deleteMeeting}
              </button>
            </footer>
          </section>
        </div>
      )}

      {share && record && <ShareModal meeting={record.meeting} onClose={() => setShare(false)} onNotice={onNotice} />}

      {booking && (
        <BookingModal
          onClose={() => setBooking(false)}
          onCreated={(next) => {
            setBooking(false);
            void reload().catch(() => setFailed(true));
            setSelected(next.meeting.meeting_id);
            // 회의실을 못 잡았어도 회의는 섰다 — 장소가 왜 비었는지만 한 줄로 말한다
            const notice = roomReservationNotice(next);
            if (notice) onNotice(notice);
          }}
          onError={onError}
          onNotice={onNotice}
          pastRows={past}
        />
      )}
    </section>
  );
}

function MeetingRowItem({
  row,
  selected,
  onSelect,
  onAskDelete,
}: {
  row: MeetingRow;
  selected: boolean;
  onSelect: (meetingId: string) => void;
  onAskDelete: (row: MeetingRow) => void;
}) {
  return (
    <li
      className={selected ? "meeting-row openable selected" : "meeting-row openable"}
      data-meeting-id={row.meeting_id}
      onClick={() => onSelect(row.meeting_id)}
    >
      <span aria-hidden className="meeting-row-mark" style={{ visibility: selected ? "visible" : "hidden" }}>
        ▸
      </span>
      <span className="meeting-row-when tabular">{meetingWhen(row.starts_at)}</span>
      <b className={row.title ? "meeting-row-title" : "meeting-row-title untitled"}>{row.title ?? meetingScreen.noTitle}</b>
      <span className="meeting-row-state">
        {/* 「열람」은 상태가 아니라 접근 표시다 — 상태와 나란히 함께 선다 */}
        {row.viewer_relation === "shared" && <span className="badge outline">{meetingScreen.sharedTag}</span>}
        <span className={meetingStatusClass[row.status]} style={{ fontSize: 12 }}>
          {meetingStatusLabel[row.status]}
        </span>
      </span>
      {row.status === "scheduled" && (
        <button
          className="btn ghost h30"
          onClick={(event) => {
            event.stopPropagation();
            onAskDelete(row);
          }}
          style={{ flex: "none" }}
          type="button"
        >
          {meetingScreen.remove}
        </button>
      )}
    </li>
  );
}

/** E20 회의록 패널 — 읽기 전용이다. 조작 버튼도 후보 건수 줄도 두지 않는다. */
function NotePanel({
  selected,
  record,
  onOpen,
  onShare,
}: {
  selected: string | null;
  record: MeetingRecord | null;
  onOpen: (meetingId: string) => void;
  onShare: () => void;
}) {
  if (!selected) {
    return (
      <section aria-label="회의록" className="meeting-panel">
        <div className="meeting-panel-center">
          <Empty title={meetingScreen.panelEmpty} />
        </div>
      </section>
    );
  }
  if (!record) {
    return (
      <section aria-label="회의록" className="meeting-panel">
        <div style={{ padding: 24 }}>
          <Skeleton label="회의록을 불러오는 중" />
        </div>
      </section>
    );
  }
  const meeting = record.meeting;
  const planned = meeting.status === "scheduled";
  /* `order` 로 줄만 세운다 — 번호는 그 줄에서의 «자리» 다 (바로 시작한 회의는 order 0 에서 시작한다) */
  const agendas = [...record.agendas].sort((left, right) => left.order - right.order);
  return (
    <section aria-label="회의록" className="meeting-panel">
      <header className="meeting-panel-head">
        <h2>{meeting.title ?? meetingScreen.noTitle}</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--text-secondary)" }}>
          <span className="tabular">{meetingRange(meeting.starts_at, meeting.ends_at)}</span>
          <span style={{ color: "var(--text-tertiary)" }}>·</span>
          {meeting.location ? <span>{meeting.location}</span> : <EmptyValue />}
        </div>
        <div className="t-meta" style={{ marginTop: 4 }}>
          {meetingScreen.attendCount(meeting.attendees.length + meeting.external_attendees.length)}
        </div>
      </header>

      <div className="scroll-hidden" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "20px 24px 24px" }}>
        {/* E26 예정 회의는 회의록 대신 안건을 낸다 — 아직 결론도 본문도 없을 뿐 같은 블록이다 */}
        {agendas.map((agenda, index) => (
          <AgendaBlock
            compact
            index={index}
            key={agenda.agenda_id}
            lines={planned ? [] : agenda.lines.filter((line) => line.track === "final").map((line) => ({ text: line.text }))}
            mark={planned ? null : { text: agenda.concluded ? meetingScreen.concluded : meetingScreen.notConcluded, concluded: agenda.concluded }}
            /* 안건이 어디서 왔는지는 상태와 무관하게 늘 낸다 (E21) */
            source={meetingAgendaSourceText(agenda.source)}
            title={agenda.title}
            todos={
              planned || agenda.todos.length === 0
                ? null
                : agenda.todos.map((todo) => ({
                    key: todo.todo_id,
                    what: todo.title,
                    // 담당 후보 칸은 없다 (D19-3) — 기한만, 그것도 있을 때만
                    due: todo.due_candidate ?? undefined,
                    // 이미 요청으로 선 줄은 「요청됨」으로 남는다 — 누르는 자리는 두지 않는다 (D24)
                    actions: todo.linked ? (
                      <span className="t-meta" style={{ fontSize: 12 }}>
                        {meetingScreen.requested}
                      </span>
                    ) : undefined,
                  }))
            }
          />
        ))}
      </div>

      {/* 패널에서 하는 조작은 회의록을 바꾸지 않는 것 둘뿐이다 — [공유]·[내보내기]. 고치려면 [회의록 열기]로 간다.
          [다음 회의 예약]은 SCR-105 요소표에 없다 (그것은 SCR-106-E11 이고 상세 머리에 선다) */}
      <footer className="meeting-panel-foot">
        {/* 공유는 끝난 뒤에만 선다 (D34) — 상세 머리와 같은 규칙이다 */}
        {(meeting.status === "done" || meeting.status === "failed") && (
          <button className="btn h30" onClick={onShare} type="button">
            {meetingScreen.share}
          </button>
        )}
        {/* 정리 전(예정 · 진행 중 · 정리 중 · 실패 · 취소됨)에는 내보낼 회의록이 없다.
            받는 것은 브라우저가 한다 — 상세 머리와 같은 주소다 */}
        {meeting.status === "done" && (
          <a className="btn h30" href={meetingExportUrl(meeting.meeting_id)}>
            {meetingScreen.export}
          </a>
        )}
        <button className="btn h30" onClick={() => onOpen(meeting.meeting_id)} type="button">
          {meetingScreen.openNote}
        </button>
      </footer>
    </section>
  );
}
