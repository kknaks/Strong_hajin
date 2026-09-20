import { useCallback, useEffect, useMemo, useState } from "react";
import type React from "react";

import { Button, IconButton } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { StatusNote } from "../../ds/StatusNote";
import { getCalendar, getTask, transitionDirectTask, updateTask } from "../../lib/api";
import {
  calendarCursorText,
  calendarScreen,
  calendarViewLabel,
  personName,
  seoulToday,
} from "../../lib/labels";
import type { CalendarEntry, DirectTask, Persona, TaskPatch } from "../../lib/viewModels";
import { TaskDetailDrawer, type TaskAction } from "../work/WorkModals";
import { MonthGrid } from "./MonthGrid";
import { ScheduleRail } from "./ScheduleRail";
import { WeekGrid } from "./WeekGrid";
import {
  gridSegments,
  monthGridDays,
  railCards,
  shiftMonth,
  shiftWeek,
  spanSegments,
  timedBlocks,
  weekGridDays,
  weekOfMonth,
  type CalendarTab,
  type CalendarView,
  type RailCard,
} from "./calendarModel";

type CalendarPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canManageOwnTasks: boolean;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  onError: (message: string | null) => void;
  /** Registers this surface's reload so the shell can await it after an approved AX effect (no remount). */
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  /** 셸의 `AppBody` 세 칸 중 **왼쪽만** 쓴다 — 오른쪽 레일은 비운다(§J). */
  onRegisterRails?: (rails: { left?: React.ReactNode; right?: React.ReactNode }) => void;
};

/**
 * 캘린더 — 3분할(사이드바 · 좌측 일정 레일 · 격자) · 탭 셋 · 월/주 뷰 (SPEC-004 §2 · WORK-004 FE-1).
 *
 * **이 화면이 업무만 내던 시절이 끝났다.** 합본 조회 하나가 업무와 회의를 한 배열로 싣고
 * `kind` 로 가른다 — 공유받은 회의도 여기 선다(K9).
 *
 * 규율 셋이 이 파일에 걸려 있다:
 *
 * - **한 화면 = 한 요청.** 주 뷰 이레·월 뷰 42칸이 각각 `getCalendar()` **한 번**으로 그려진다.
 *   탭을 바꾸는 것은 다시 묻는 일이 아니다 — 받아 둔 한 배열을 걸러 내기만 한다.
 * - **기간을 계산하지 않는다** (K14). 띠는 서버가 준 `span_from`·`span_to` 로 그린다.
 *   `features/work` 의 `taskSpan()` 은 `TaskTimeline` 과 공유라 여기서 부르지 않는다.
 * - **격자는 상태를 말하지 않는다** (§2.6) — 색은 유형 둘로만. 상태 어휘는 상세가 갖는다.
 *
 * 드롭·손잡이·시간 배정 만들기는 **FE-2** 다. 여기까지는 읽기다.
 */
export function CalendarPage({
  personaName,
  canManageOwnTasks,
  onAskAboutTask,
  onNotice,
  onError,
  onRegisterRefresh,
  onRegisterRails,
}: CalendarPageProps) {
  const today = useMemo(() => seoulToday(), []);
  const [view, setView] = useState<CalendarView>("month");
  const [tab, setTab] = useState<CalendarTab>("all");
  /** 지금 보고 있는 구간의 기준 날짜. 월 뷰에서는 그 달의 1일, 주 뷰에서는 그 주의 아무 날이다. */
  const [anchor, setAnchor] = useState<string>(() => today);
  const [selected, setSelected] = useState<string | null>(null);
  const [entries, setEntries] = useState<CalendarEntry[]>([]);
  const [state, setState] = useState<"loading" | "error" | "ready">("loading");
  const [task, setTask] = useState<DirectTask | null>(null);
  const [busy, setBusy] = useState(false);

  const monthDays = useMemo(() => monthGridDays(Number(anchor.slice(0, 4)), Number(anchor.slice(5, 7))), [anchor]);
  const weekDays = useMemo(() => weekGridDays(anchor), [anchor]);
  /* 조회 구간은 «그려지는 칸 전부»다 — 월 뷰는 달 밖 칸까지, 주 뷰는 이레. 한 번에 받는다. */
  const range = useMemo(
    () =>
      view === "month"
        ? { from: monthDays[0].date, to: monthDays[monthDays.length - 1].date }
        : { from: weekDays[0], to: weekDays[6] },
    [monthDays, view, weekDays],
  );

  const reload = useCallback(async () => {
    const rows = await getCalendar(range.from, range.to);
    setEntries(rows);
  }, [range.from, range.to]);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    void reload()
      .then(() => {
        if (cancelled) return;
        setState("ready");
        onError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState("error");
        onError(error instanceof Error ? error.message : calendarScreen.loadFailed);
      });
    return () => {
      cancelled = true;
    };
  }, [onError, reload]);

  // The shell awaits this to know the visible projection has settled; re-reading in place keeps filter/view state.
  useEffect(() => {
    onRegisterRefresh?.(reload);
    return () => onRegisterRefresh?.(null);
  }, [onRegisterRefresh, reload]);

  /**
   * **레일과 격자는 같은 범위를 본다** — 월 뷰면 그 달, 주 뷰면 그 주 (증보 K18).
   *
   * 조회 구간(`range`)과 **일부러 다르다.** 월 격자는 달 밖 칸을 «그리되 비워 둔다»(시안 —
   * `MonthGrid` 가 `--out` 칸에 날짜만 남긴다). 레일이 그 칸의 일정까지 내면 **레일에는 있는데
   * 격자에는 없는 일정**이 생기고, 사람은 그것을 「둘 중 하나가 틀렸다」로 읽는다.
   * 그래서 **격자가 실제로 그리는 날들**(달 밖이 아닌 칸)을 그대로 레일의 범위로 쓴다 —
   * 두 값을 따로 계산하지 않으므로 어긋날 수가 없다.
   *
   * 조회 구간은 **좁히지 않는다.** 달 밖 칸까지 받아 두는 편이 한 요청으로 끝나고,
   * 안 그리는 여분은 무해하다. 좁히면 뷰마다 재조회가 는다.
   */
  const railScope = useMemo(() => {
    if (view !== "month") return { from: weekDays[0], to: weekDays[6] };
    const drawn = monthDays.filter((day) => !day.out);
    return { from: drawn[0].date, to: drawn[drawn.length - 1].date };
  }, [monthDays, view, weekDays]);

  const cards = useMemo(
    () => railCards(entries, tab, { ...railScope, selected }),
    [entries, railScope, selected, tab],
  );
  const segments = useMemo(() => gridSegments(entries, tab), [entries, tab]);
  const spans = useMemo(() => spanSegments(entries, tab), [entries, tab]);
  const blocks = useMemo(() => timedBlocks(entries, tab), [entries, tab]);

  const openTask = useCallback(
    async (card: RailCard) => {
      try {
        // 카드는 상태를 내지 않는다 (K15) — 그 말은 상세가 갖고, 상세는 자기 행을 따로 읽는다.
        setTask(await getTask(card.id));
        onError(null);
      } catch (error) {
        onError(error instanceof Error ? error.message : calendarScreen.loadFailed);
      }
    },
    [onError],
  );

  /* 셸이 세 칸이라 «화면의» 레일이 «셸의» AppBody 슬롯에 선다. 오른쪽은 비운다 — 캘린더는 왼쪽만 쓴다. */
  useEffect(() => {
    if (!onRegisterRails) return;
    onRegisterRails({
      left: (
        <ScheduleRail
          cards={cards}
          onClearDay={() => setSelected(null)}
          onOpen={(card) => void openTask(card)}
          onRetry={() => void reload()}
          onTab={setTab}
          selected={selected}
          state={state}
          tab={tab}
        />
      ),
    });
    return () => onRegisterRails({});
  }, [cards, onRegisterRails, openTask, reload, selected, state, tab]);

  /** 주·월 이동은 **선택을 항상 푼다** — 고른 날이 화면 밖에 남으면 레일이 빈 채로 굳는다(§2.1). */
  const shift = (delta: number) => {
    setSelected(null);
    setAnchor((current) => (view === "week" ? shiftWeek(current, delta) : shiftMonth(current, delta)));
  };

  const changeView = (next: CalendarView) => {
    setSelected(null);
    setView(next);
  };

  const year = Number(anchor.slice(0, 4));
  const month = Number(anchor.slice(5, 7));
  const week = weekOfMonth(weekDays);

  const transition = async (target: DirectTask, action: TaskAction, reason?: string): Promise<boolean> => {
    setBusy(true);
    try {
      await transitionDirectTask(target.task_id, action, target.version, reason);
      await reload();
      setTask(await getTask(target.task_id));
      onError(null);
      onNotice("상태를 바꿨습니다.");
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 상태를 바꾸지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const update = async (target: DirectTask, patch: TaskPatch) => {
    setBusy(true);
    try {
      await updateTask(target.task_id, target.version, patch);
      await reload();
      setTask(await getTask(target.task_id));
      onError(null);
      onNotice("업무 내용을 저장했습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scax-cal-main">
      <div className="scax-cal-toolbar">
        <span className="scax-cal-toolbar__year">
          {view === "week" ? calendarCursorText.yearMonth(week.year, week.month) : calendarCursorText.year(year)}
        </span>
        <div className="scax-cal-toolbar__nav">
          <IconButton
            className="scax-cal-toolbar__prev"
            label={view === "week" ? calendarScreen.prev.week : calendarScreen.prev.month}
            name="chevron-right"
            onClick={() => shift(-1)}
            size={20}
          />
          <h2 className="scax-cal-toolbar__month">
            {view === "week" ? calendarCursorText.week(week.week) : calendarCursorText.month(year, month)}
          </h2>
          <IconButton
            label={view === "week" ? calendarScreen.next.week : calendarScreen.next.month}
            name="chevron-right"
            onClick={() => shift(1)}
            size={20}
          />
        </div>
        <span className="scax-cal-toolbar__spacer" />
        <SegmentedControl
          ariaLabel={calendarScreen.viewAria}
          onChange={changeView}
          options={[
            { value: "week", label: calendarViewLabel.week },
            { value: "month", label: calendarViewLabel.month },
          ]}
          value={view}
        />
      </div>
      {state === "error" ? (
        <StatusNote tone="danger">
          {calendarScreen.loadFailed}{" "}
          <Button onClick={() => void reload()} size="sm" type="button" variant="text">
            {calendarScreen.retry}
          </Button>
        </StatusNote>
      ) : view === "week" ? (
        <WeekGrid blocks={blocks} days={weekDays} onSelect={setSelected} selected={selected} spans={spans} today={today} />
      ) : (
        <MonthGrid
          days={monthDays}
          month={month}
          onSelect={setSelected}
          segments={segments}
          selected={selected}
          today={today}
          year={year}
        />
      )}
      {task && (
        <TaskDetailDrawer
          busy={busy}
          canManage={canManageOwnTasks}
          onAskAx={onAskAboutTask}
          onClose={() => setTask(null)}
          onError={onError}
          onNotice={onNotice}
          onTransition={transition}
          onUpdate={update}
          ownerName={personName(personaName)}
          task={task}
        />
      )}
    </div>
  );
}
