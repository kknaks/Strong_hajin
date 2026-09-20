import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";

import { Button, IconButton } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { StatusNote } from "../../ds/StatusNote";
import {
  ApiError,
  createTaskSchedule,
  getCalendar,
  getTask,
  getTaskAssignmentCandidates,
  getWorkRequestAssigneeCandidates,
  getWorkRequestCcCandidates,
  transitionDirectTask,
  updateTask,
  updateTaskSchedule,
} from "../../lib/api";
import {
  calendarCursorText,
  calendarDeny,
  calendarDone,
  calendarScreen,
  calendarViewLabel,
  personName,
  seoulToday,
} from "../../lib/labels";
import type { CalendarEntry, CalendarTaskRow, DirectTask, Persona, TaskPatch } from "../../lib/viewModels";
import { CreateWorkModal, TaskDetailDrawer, type TaskAction } from "../work/WorkModals";
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
  type TimedBlock,
} from "./calendarModel";
import {
  defaultSlot,
  denyMessage,
  dropGuard,
  moveTaskDates,
  previewResize,
  releaseNotice,
  resizeTaskDates,
  scheduleKey,
  slotGuard,
  snapClock,
  spanOf,
  type DateEdge,
  type HandleGrab,
  type ScheduleCommand,
} from "./calendarWrites";

type CalendarPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canManageOwnTasks: boolean;
  canCreateWorkRequests: boolean;
  canAssignTasks: boolean;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  onError: (message: string | null) => void;
  /** Registers this surface's reload so the shell can await it after an approved AX effect (no remount). */
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  /** 셸의 `AppBody` 세 칸 중 **왼쪽만** 쓴다 — 오른쪽 레일은 비운다(§J). */
  onRegisterRails?: (rails: { left?: React.ReactNode; right?: React.ReactNode }) => void;
  /** 머리의 「업무 만들기」 단추가 서는 자리 (K17). 선례 `MyWorkPage.tsx:845-862`. */
  onRegisterHeaderActions?: (actions: React.ReactNode) => void;
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
  personaId,
  personaName,
  canManageOwnTasks,
  canCreateWorkRequests,
  canAssignTasks,
  onAskAboutTask,
  onNotice,
  onError,
  onRegisterRefresh,
  onRegisterRails,
  onRegisterHeaderActions,
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
  /** 지금 끌고 있는 업무 — 고스트를 그릴지와 가드에 쓴다. 끌 수 있는 것은 업무뿐이다(§F). */
  const [dragTaskId, setDragTaskId] = useState<string | null>(null);
  /** 손잡이를 잡고 있는 동안. **보내는 것은 놓을 때**다 — 낙관적 잠금이라 끌 때마다 부르면 회차가 어긋난다. */
  const [grab, setGrab] = useState<HandleGrab | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [assignCandidates, setAssignCandidates] = useState<Persona[]>([]);
  const [ccCandidates, setCcCandidates] = useState<Persona[]>([]);
  /**
   * 멱등 키 원장 — **「이 업무의 이 날 이 시각」이라는 하나의 제출 의도**에 키 하나 (K12).
   *
   * 연타가 같은 키로 나가야 두 번째가 `409` 가 아니라 **`200` 영수증**이 된다.
   * 키를 매번 새로 만들면 두 번째 클릭이 「그 날은 이미 찼다」로 튕긴다.
   */
  const scheduleKeys = useRef(new Map<string, string>());

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

  /** 합본 조회가 실어 준 업무 행 — 가드도 쓰기도 **이 행의 `span_*`** 를 쓴다(K14). */
  const taskRow = useCallback(
    (taskId: string): CalendarTaskRow | null =>
      (entries.find((entry) => entry.kind === "task" && entry.task_id === taskId) as CalendarTaskRow | undefined) ?? null,
    [entries],
  );

  /**
   * 거절을 **말로** 한다 (§I) — 조용히 튕기는 자리가 없어야 한다.
   *
   * 서버 본문에는 `code` 가 없고 `{"detail": "<문장>"}` 뿐이라(게다가 한 자리는 영문이다)
   * **상태 코드 + 어떤 명령을 불렀는지**로 고른 우리 문구를 낸다.
   */
  const deny = useCallback(
    (command: ScheduleCommand, error: unknown, row: CalendarTaskRow | null) => {
      const status = error instanceof ApiError ? error.status : 0;
      onError(denyMessage(command, status, row ? spanOf(row) : null));
    },
    [onError],
  );

  /** 날짜를 바꾼 응답에는 **함께 닫힌 배정의 건수**가 실린다 (K3). 0 건이면 아무 말도 하지 않는다. */
  const announce = useCallback(
    (done: string, saved: DirectTask) => {
      const released = releaseNotice(saved.schedule_release);
      onNotice(released ? `${done} ${released}` : done);
    },
    [onNotice],
  );

  /** R1·R3·R4 — 업무의 날짜를 바꾸는 한 자리. 드롭도 손잡이도 같은 명령(`PATCH /api/tasks`)으로 간다. */
  const saveDates = useCallback(
    async (row: CalendarTaskRow, patch: TaskPatch, done: string) => {
      setBusy(true);
      try {
        const saved = await updateTask(row.task_id, row.version, patch);
        await reload();
        if (task?.task_id === row.task_id) setTask(await getTask(row.task_id));
        onError(null);
        announce(done, saved);
      } catch (error) {
        deny("task_dates", error, row);
      } finally {
        setBusy(false);
      }
    },
    [announce, deny, onError, reload, task],
  );

  /** R1 — 날짜 칸 드롭. **기간 가드가 없다**(§2.3 R1) — 기한 없는 업무도 떨어지고 거기서 기간이 생긴다. */
  const dropTask = useCallback(
    (taskId: string, date: string) => {
      setDragTaskId(null);
      const row = taskRow(taskId);
      // 회의는 애초에 끌리지 않지만(§F), 다른 데서 온 것이면 말로 돌려보낸다.
      if (!row) {
        onError(calendarDeny.meetingReadOnly);
        return;
      }
      const refused = dropGuard(row, canManageOwnTasks);
      if (refused) {
        onError(refused);
        return;
      }
      void saveDates(row, moveTaskDates(row, date), calendarDone.moved);
    },
    [canManageOwnTasks, onError, saveDates, taskRow],
  );

  /**
   * R3·R4 — 손잡이를 놓았다.
   *
   * **`start` 손잡이는 `start_date` 를, `end` 손잡이는 `due_date` 를 쓴다 — 뒤집힌 업무에서도 그렇다**
   * (WARN-A). 역전은 조용히 접지 않고 **말한다**(§I).
   */
  const commitGrab = useCallback(
    (held: HandleGrab) => {
      if (!held.date) return;
      const row = taskRow(held.taskId);
      if (!row) return;
      const refused = dropGuard(row, canManageOwnTasks);
      if (refused) {
        onError(refused);
        return;
      }
      const outcome = resizeTaskDates(row, held.edge, held.date);
      if ("deny" in outcome) {
        onError(outcome.deny);
        return;
      }
      void saveDates(row, outcome.patch, calendarDone.resized);
    },
    [canManageOwnTasks, onError, saveDates, taskRow],
  );

  /* 손잡이를 잡고 있는 동안 — 포인터가 지나는 칸을 읽어 «그림만» 바꾸고, 놓을 때 한 번 보낸다. */
  useEffect(() => {
    if (!grab) return undefined;
    const move = (event: PointerEvent) => {
      const cell = document.elementFromPoint(event.clientX, event.clientY)?.closest?.("[data-date]");
      const date = cell?.getAttribute("data-date");
      if (date) setGrab((current) => (current && current.date !== date ? { ...current, date } : current));
    };
    const up = () => {
      setGrab((current) => {
        if (current) commitGrab(current);
        return null;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [commitGrab, grab]);

  /**
   * R5 — 시간 격자 드롭.
   *
   * **그 날에 이미 배정이 있으면 `POST` 가 아니라 `PATCH` 다** (K10) — 합본 조회의 `schedules[]` 가
   * `schedule_id` 와 **그 배정 자신의 회차**를 이미 주었다. 그래서 정상 흐름에서 `409` 를 볼 일이 없다.
   * 새로 만드는 쪽은 **같은 내용이면 같은 멱등 키**로 나간다 (K12) — 연타가 영수증이 되는 자리다.
   */
  const dropSlot = useCallback(
    (taskId: string, date: string, minutes: number) => {
      setDragTaskId(null);
      const row = taskRow(taskId);
      if (!row) {
        onError(calendarDeny.meetingReadOnly);
        return;
      }
      const refused = slotGuard(row, date, canManageOwnTasks);
      if (refused) {
        onError(refused);
        return;
      }
      const slot = defaultSlot(minutes);
      const standing = row.schedules.find((schedule) => schedule.on_date === date);
      void (async () => {
        setBusy(true);
        try {
          if (standing) {
            await updateTaskSchedule(standing.schedule_id, standing.version, slot);
          } else {
            await createTaskSchedule(row.task_id, { on_date: date, ...slot }, scheduleKey(scheduleKeys.current, row.task_id, { on_date: date, ...slot }));
          }
          await reload();
          onError(null);
          onNotice(standing ? calendarDone.rescheduled : calendarDone.scheduled);
        } catch (error) {
          deny(standing ? "schedule_update" : "schedule_create", error, row);
        } finally {
          setBusy(false);
        }
      })();
    },
    [canManageOwnTasks, deny, onError, onNotice, reload, taskRow],
  );

  /**
   * R6 — 시간 블록의 세로 손잡이를 놓았다. **놓을 때 한 번만** 온다.
   *
   * 끌지 않은 쪽의 시각은 **서버가 준 문자열 그대로** 보낸다 — 다시 눈금에 접으면 `23:59` 같은
   * 눈금 밖의 값이 조용히 바뀐다. `expected_version` 은 **그 배정 자신의 회차**다(K8).
   */
  const resizeSlot = useCallback(
    (block: TimedBlock, edge: DateEdge, minutes: number) => {
      if (!block.scheduleId || block.version === null) return;
      const row = block.taskId ? taskRow(block.taskId) : null;
      const starts_at = edge === "start" ? snapClock(minutes) : block.startLabel;
      const ends_at = edge === "end" ? snapClock(minutes) : block.endLabel;
      // `HH:MM` 은 사전 순이 곧 시간 순이다.
      if (ends_at <= starts_at) {
        onError(calendarDeny.invalidRange);
        return;
      }
      if (starts_at === block.startLabel && ends_at === block.endLabel) return;
      void (async () => {
        setBusy(true);
        try {
          await updateTaskSchedule(block.scheduleId!, block.version!, { starts_at, ends_at });
          await reload();
          onError(null);
          onNotice(calendarDone.rescheduled);
        } catch (error) {
          deny("schedule_update", error, row);
        } finally {
          setBusy(false);
        }
      })();
    },
    [deny, onError, onNotice, reload, taskRow],
  );

  /** 손잡이를 잡았다 — 아직 어느 칸도 지나지 않았다. */
  const grabHandle = useCallback((taskId: string, edge: DateEdge) => setGrab({ date: null, edge, taskId }), []);

  /**
   * 고스트는 **놓을 수 있는 자리에만** 뜬다 (§2.3 R5). 못 놓는 자리에서도 **받기는 받는다** —
   * 조용히 튕기면 사람이 이유를 못 듣기 때문이다(§I). 가드는 `span_*` 로 본다(K14).
   */
  const ghostAt = useCallback(
    (date: string) => {
      const row = dragTaskId ? taskRow(dragTaskId) : null;
      return row ? slotGuard(row, date, canManageOwnTasks) === null : false;
    },
    [canManageOwnTasks, dragTaskId, taskRow],
  );

  const openTask = useCallback(
    async (taskId: string) => {
      try {
        // 카드는 상태를 내지 않는다 (K15) — 그 말은 상세가 갖고, 상세는 자기 행을 따로 읽는다.
        setTask(await getTask(taskId));
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
          onDragStart={(card) => setDragTaskId(card.id)}
          onOpen={(card) => void openTask(card.id)}
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

  /**
   * 머리의 「업무 만들기」 (증보 K17).
   *
   * **단추는 레이아웃이라 시안 정본, 모달은 기능이라 우리 것**이다 — 시안 `TaskCreateModal` 의
   * 필드 구성을 따라가지 않고 **기존 업무 생성 모달**을 연다(G-CAL-03 · §J 정정).
   * 셸 머리에 등록하고 떠날 때 지운다 — 선례 `MyWorkPage.tsx:845-862`.
   */
  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    onRegisterHeaderActions(
      canManageOwnTasks || canCreateWorkRequests ? (
        <Button onClick={() => setIsCreating(true)} size="sm" tone="primary" type="button" variant="solid">
          {calendarScreen.create}
        </Button>
      ) : null,
    );
    return () => onRegisterHeaderActions(null);
  }, [canCreateWorkRequests, canManageOwnTasks, onRegisterHeaderActions]);

  /* 모달이 고르게 할 사람들. 못 불러와도 화면은 서고, 그 칸만 빈다 — 선례 `MyWorkPage.tsx:388-438`. */
  useEffect(() => {
    if (!isCreating) return;
    let cancelled = false;
    void getWorkRequestCcCandidates()
      .then((rows) => !cancelled && setCcCandidates(rows))
      .catch(() => !cancelled && setCcCandidates([]));
    if (canCreateWorkRequests) {
      void getWorkRequestAssigneeCandidates()
        .then((rows) => !cancelled && setAssigneeCandidates(rows))
        .catch(() => !cancelled && setAssigneeCandidates([]));
    }
    if (canAssignTasks) {
      void getTaskAssignmentCandidates()
        .then((rows) => !cancelled && setAssignCandidates(rows))
        .catch(() => !cancelled && setAssignCandidates([]));
    }
    return () => {
      cancelled = true;
    };
  }, [canAssignTasks, canCreateWorkRequests, isCreating, personaId]);

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

  /**
   * 상세 서랍이 저장한다. **여기서도 날짜가 바뀔 수 있으므로** K3 의 「N건 해제」를 같이 낸다 —
   * 격자의 드롭·손잡이와 같은 `PATCH /api/tasks` 한 자리다. 0 건이면 아무 말도 하지 않는다.
   */
  const update = async (target: DirectTask, patch: TaskPatch) => {
    setBusy(true);
    try {
      const saved = await updateTask(target.task_id, target.version, patch);
      await reload();
      setTask(await getTask(target.task_id));
      onError(null);
      announce("업무 내용을 저장했습니다.", saved);
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
        <WeekGrid
          blocks={blocks}
          days={weekDays}
          dragging={dragTaskId !== null}
          ghostAt={ghostAt}
          grabbing={grab !== null}
          onDropSlot={dropSlot}
          onDropTask={dropTask}
          onGrabHandle={grabHandle}
          onResizeSlot={resizeSlot}
          onSelect={setSelected}
          selected={selected}
          spans={previewResize(spans, grab)}
          today={today}
        />
      ) : (
        <MonthGrid
          days={monthDays}
          dragging={dragTaskId !== null}
          grabbing={grab !== null}
          month={month}
          onDropTask={dropTask}
          onGrabHandle={grabHandle}
          onSelect={setSelected}
          segments={previewResize(segments, grab)}
          selected={selected}
          today={today}
          year={year}
        />
      )}
      {isCreating && (
        <CreateWorkModal
          assignCandidates={canAssignTasks ? assignCandidates : []}
          assigneeCandidates={assigneeCandidates}
          canCreateRequest={canCreateWorkRequests}
          canCreateTask={canManageOwnTasks}
          ccCandidates={ccCandidates}
          onClose={() => setIsCreating(false)}
          onCreated={async (message) => {
            setIsCreating(false);
            await reload();
            onNotice(message);
          }}
          onError={onError}
          onOpenTask={(taskId) => void openTask(taskId)}
          ownerName={personName(personaName)}
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
