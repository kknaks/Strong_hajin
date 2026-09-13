import { useCallback, useEffect, useState } from "react";

import { Button } from "../../ds/Button";
import { listMeetings, quickStartMeeting, removeMeeting } from "../../lib/api";
import { Badge } from "../../ds/Badge";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { ConfirmModal } from "../../ds/Modal";
import { Skeleton } from "../../ds/Skeleton";
import { emptyActionLabel, meetingCardBadgeTone, meetingScreen, meetingStatusLabel, meetingWhen } from "../../lib/labels";
import type { MeetingListPayload, MeetingRow } from "../../lib/viewModels";
import { BookingModal, roomReservationNotice } from "./BookingModal";

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
  selected,
  onRegisterHeaderActions,
}: {
  onOpenMeeting: (meetingId: string) => void;
  onError: (message: string | null) => void;
  onNotice: (message: string) => void;
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  focusMeetingId?: string | null;
  onFocusHandled?: () => void;
  /** 바퀴 6a: 고른 회의는 «목록 칸의 선택 상태» 다. 워크스페이스가 들고 이 칸은 받아 쓴다 (M-1). */
  selected: string | null;
  /** 바퀴 6a: 머리가 한 줄로 합쳐져서, 이 칸의 액션(회의 생성·빠른 시작)을 셸 머리에 등록한다 (M-6). */
  onRegisterHeaderActions?: (actions: React.ReactNode) => void;
}) {
  const [payload, setPayload] = useState<MeetingListPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [moreBusy, setMoreBusy] = useState(false);
  const [confirm, setConfirm] = useState<MeetingRow | null>(null);
  const [booking, setBooking] = useState(false);
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
    onOpenMeeting(focusMeetingId);
    onFocusHandled?.();
  }, [focusMeetingId, onFocusHandled]);

  /* 바퀴 6a M-4: 목록 오른쪽 미리보기 패널을 지웠다(시안에 없다). 그 패널만 쓰던
     「고른 회의의 회의록을 미리 읽는」 effect 도 함께 사라진다 — 이제 회의록은 3칸(상세)이 읽는다. */

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
      // 지운 회의를 고르고 있었으면 선택을 놓는다 — 선택은 워크스페이스가 들고 있다.
      if (selected === confirm.meeting_id) onOpenMeeting("");
      setConfirm(null);
      await reload();
      onNotice(scope === "meeting" ? meetingScreen.deletedMeeting : meetingScreen.deletedNote);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "삭제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const quickStart = useCallback(async () => {
    setBusy((current) => {
      if (current) return current;
      void (async () => {
        try {
          const next = await quickStartMeeting();
          onOpenMeeting(next.meeting.meeting_id);
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "회의를 시작하지 못했습니다.");
        } finally {
          setBusy(false);
        }
      })();
      return true;
    });
  }, [onError, onOpenMeeting]);

  /* 바퀴 6a M-6: 페이지 머리가 셸의 AppHeader 한 줄로 합쳐졌다. 이 칸이 가진 두 동작을 거기 등록한다.
     시안 순서대로 «회의 생성이 왼쪽», 빠른 시작이 오른쪽(solid + play)이다.
     둘 다 계약이 있다 — `bookMeeting`(생성) · `quickStartMeeting`(빠른 시작). */
  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    onRegisterHeaderActions(
      <>
        <Button onClick={() => setBooking(true)} size="sm" tone="primary" type="button" variant="outlined">
          {meetingScreen.book}
        </Button>
        <Button disabled={busy} onClick={() => void quickStart()} size="sm" tone="primary" type="button" variant="solid">
          <Icon name="play" size={14} /> {meetingScreen.start}
        </Button>
      </>,
    );
    return () => onRegisterHeaderActions(null);
  }, [busy, onRegisterHeaderActions, quickStart]);

  const upcoming = payload?.upcoming ?? [];
  const past = payload?.past.items ?? [];
  const isEmpty = Boolean(payload) && upcoming.length === 0 && past.length === 0;

  return (
    /* 바퀴 6a: 이 컴포넌트는 이제 «2칸(목록 레일)» 이다. 페이지 머리는 셸의 AppHeader 로,
       오른쪽 미리보기 패널은 삭제(M-4). 남은 것은 목록 하나라 감싸는 칸도 하나다.
       바퀴 6bc(§8-B 11·12): 그 칸을 시안의 `.scax-meeting-list` 로 갈았다. 구 `meeting-list-surface`
       가 남겨 두었던 규칙은 «미리보기 패널 푸터의 오른쪽 여백» 하나뿐이었는데 그 푸터가 이 바퀴에서
       사라져(죽은 NotePanel 삭제) 껍데기만 남은 이름이었다. */
    <section aria-label={meetingScreen.title} className="scax-meeting-list">
      {failed ? (
        <div className="meeting-panel-center">
          <Empty
            actionLabel={emptyActionLabel.error} onAction={() => void reload().catch(() => setFailed(true))} title={meetingScreen.listError} variant="error" />
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
        <div className="scax-meeting-list__body scax-scroll">
          {upcoming.length > 0 && (
            <section className="scax-meeting-section">
              {/* 구획 제목은 그 자체가 머리줄이다 — h2 에 바로 건다. 감싸는 칸을 하나 더 두면
                  바퀴 2 가 잘라낸 h1~h4 리셋 탓에 제목 크기가 브라우저 기본으로 튄다 */}
              <h2 className="scax-meeting-section__head" id="meeting-upcoming">
                {meetingScreen.upcoming}
                <span className="scax-meeting-section__count">{upcoming.length}</span>
              </h2>
              <ul aria-labelledby="meeting-upcoming" className="scax-meeting-section__list">
                {upcoming.map((row) => (
                  <MeetingRowItem key={row.meeting_id} onAskDelete={setConfirm} onSelect={onOpenMeeting} row={row} selected={selected === row.meeting_id} />
                ))}
              </ul>
            </section>
          )}
          {past.length > 0 && (
            <section className="scax-meeting-section">
              <h2 className="scax-meeting-section__head" id="meeting-past">
                {meetingScreen.past}
              </h2>
              <ul aria-labelledby="meeting-past" className="scax-meeting-section__list">
                {past.map((row) => (
                  <MeetingRowItem key={row.meeting_id} onAskDelete={setConfirm} onSelect={onOpenMeeting} row={row} selected={selected === row.meeting_id} />
                ))}
              </ul>
              {/* §8-B 12: [더 보기]는 구획 바닥 한가운데다. `listMeetings(cursor)` · `past.next_cursor`
                  계약이 실제로 있고 이미 이어받고 있다 (R-1 확인) */}
              {payload.past.next_cursor && (
                <div className="scax-meeting-section__more">
                  <Button size="sm" disabled={moreBusy} onClick={() => void loadMore()} type="button">
                    {meetingScreen.more}
                  </Button>
                </div>
              )}
            </section>
          )}
        </div>
      )}

      {/* T08 삭제 확인 — 닫는 자리는 머리의 × 하나다. 아래에 [취소]를 또 두지 않는다.
          §8-B 15: 손으로 짠 600 짜리 모달을 바퀴 3b 의 `ConfirmModal`(.scax-modal--sm 420 ·
          role="alertdialog")로 갈았다. 갈래 없는 푸터는 `cancelLabel={null}` 이 만들고,
          닫을 길 셋(× · Esc · 바깥 클릭)은 그 부품이 다 갖고 있다 (R-3) */}
      {confirm && (
        <ConfirmModal
          closeLabel="닫기"
          busy={busy}
          cancelLabel={null}
          /* 회의를 취소하는 자리 하나다 (회의록만 지우는 길은 API 에 남아 있다) */
          confirmLabel={meetingScreen.deleteMeeting}
          danger
          description={meetingScreen.deleteBody}
          onClose={() => setConfirm(null)}
          onConfirm={() => void drop("meeting")}
          title={meetingScreen.deleteTitle}
        />
      )}

      {/* 바퀴 6a M-4: 미리보기 패널과 함께 그 안의 공유 단추도 사라졌다. 공유는 3칸(상세)에 그대로 있다. */}
      {booking && (
        <BookingModal
          onClose={() => setBooking(false)}
          onCreated={(next) => {
            setBooking(false);
            void reload().catch(() => setFailed(true));
            onOpenMeeting(next.meeting.meeting_id);
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

/**
 * 목록 한 건 — 시안의 `.scax-meeting-card` (§8-B 11).
 *
 * 구 `.meeting-row` 는 «한 줄짜리 표 행» 이었다(시각·제목·상태가 고정 폭 칸에 나란히). 시안은 카드다:
 * 위줄에 시각과 상태, 아래에 제목, 그 아래 메타 한 줄. 예정 회의에만 [수정]·[삭제]가 폭을 반씩 나눠 선다
 * (`.scax-meeting-card__actions>.scax-button{flex:1 1 0}`).
 *
 * **상태는 여섯 다 배지다** — `meetings.css` 가 「실패는 레거시의 글자색 대신 danger 배지로 통일한다」고
 * 직접 적어 둔 규칙이다. 상세 머리의 D34(「정리 중」에는 배지 없음)는 *그 화면의* 규칙이라 그대로 산다.
 *
 * **[수정] 은 이 회의의 상세로 간다** — 회의 정보를 고치는 자리는 거기 머리의 「회의 정보 수정」 하나이고
 * (R-2), 목록에서 곧장 폼을 여는 길은 시안에도 우리 앱에도 없다. 시안의 이 단추는 빈 핸들러다.
 */
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
  const open = () => onSelect(row.meeting_id);
  return (
    <li>
      <div
        className={selected ? "scax-meeting-card scax-meeting-card--selected" : "scax-meeting-card"}
        data-meeting-id={row.meeting_id}
        onClick={open}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            open();
          }
        }}
        role="button"
        tabIndex={0}
      >
        <div className="scax-meeting-card__top">
          <span className="scax-meeting-card__when">{meetingWhen(row.starts_at)}</span>
          <span className="scax-meeting-card__state">
            {/* 「열람」은 상태가 아니라 접근 표시다 — 상태와 나란히 함께 선다 */}
            {row.viewer_relation === "shared" && <Badge tone="info">{meetingScreen.sharedTag}</Badge>}
            <Badge tone={meetingCardBadgeTone[row.status]}>{meetingStatusLabel[row.status]}</Badge>
          </span>
        </div>
        <p className={row.title ? "scax-meeting-card__title" : "scax-meeting-card__title scax-meeting-card__title--untitled"}>
          {row.title ?? meetingScreen.noTitle}
        </p>
        {/* 메타는 있는 값만 낸다 — 장소가 비면 그 칸도 구분선도 서지 않는다 */}
        <div className="scax-meeting-card__meta">
          {row.location && <span className="scax-meeting-card__meta-item">{row.location}</span>}
          {row.location && <span aria-hidden className="scax-meeting-card__meta-sep" />}
          <span className="scax-meeting-card__meta-item">{meetingScreen.attendCount(row.attendee_count)}</span>
        </div>
        {row.status === "scheduled" && (
          <div className="scax-meeting-card__actions">
            <Button
              onClick={(event) => {
                event.stopPropagation();
                open();
              }}
              size="sm"
              type="button"
            >
              {meetingScreen.edit}
            </Button>
            <Button
              onClick={(event) => {
                event.stopPropagation();
                onAskDelete(row);
              }}
              size="sm"
              type="button"
            >
              {meetingScreen.remove}
            </Button>
          </div>
        )}
      </div>
    </li>
  );
}
