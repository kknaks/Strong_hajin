import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import type { CalendarEntry, DirectTask } from "../../lib/viewModels";
import { CalendarPage } from "./CalendarPage";

vi.mock("../../lib/labels", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/labels")>()),
  seoulToday: () => "2027-03-04",
}));

const getCalendar = vi.fn<(from: string, to: string) => Promise<CalendarEntry[]>>();
const updateTask = vi.fn();
const createTaskSchedule = vi.fn();
const updateTaskSchedule = vi.fn();
const getTask = vi.fn();

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...actual,
    getCalendar: (from: string, to: string) => getCalendar(from, to),
    getTask: (taskId: string) => getTask(taskId),
    updateTask: (...args: unknown[]) => updateTask(...args),
    createTaskSchedule: (...args: unknown[]) => createTaskSchedule(...args),
    updateTaskSchedule: (...args: unknown[]) => updateTaskSchedule(...args),
    transitionDirectTask: vi.fn(),
    getWorkRequestCcCandidates: vi.fn().mockResolvedValue([]),
    getWorkRequestAssigneeCandidates: vi.fn().mockResolvedValue([]),
    getTaskAssignmentCandidates: vi.fn().mockResolvedValue([]),
  };
});

const onError = vi.fn();
const onNotice = vi.fn();
const noop = vi.fn();

function Harness({ canManage = true }: { canManage?: boolean }) {
  const [rails, setRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
  const [actions, setActions] = useState<React.ReactNode>(null);
  return (
    <div>
      <header data-testid="header">{actions}</header>
      {rails.left ? <aside data-testid="rail-left">{rails.left}</aside> : null}
      <CalendarPage
        canAssignTasks={false}
        canCreateWorkRequests={false}
        canManageOwnTasks={canManage}
        onAskAboutTask={noop}
        onError={onError}
        onNotice={onNotice}
        onRegisterHeaderActions={setActions}
        onRegisterRails={setRails}
        personaId="me"
        personaName="유하람 (기획자)"
        personas={[]}
      />
    </div>
  );
}

/** 시작 9/6 · 마감 9/4 — 뒤집힌 기간은 실재한다. span 은 [3/03, 3/05] 로 정규화돼 온다. */
const flipped: CalendarEntry = {
  kind: "task",
  task_id: "flip",
  title: "뒤집힌 업무",
  state: "in_progress",
  start_date: "2027-03-05",
  due_date: "2027-03-03",
  span_from: "2027-03-03",
  span_to: "2027-03-05",
  version: 7,
  schedules: [],
};

const undated: CalendarEntry = {
  kind: "task",
  task_id: "free",
  title: "기한 없는 업무",
  state: "open",
  start_date: null,
  due_date: null,
  span_from: null,
  span_to: null,
  version: 2,
  schedules: [],
};

const meeting: CalendarEntry = {
  kind: "meeting",
  meeting_id: "m1",
  title: "주간 회의",
  starts_at: "2027-03-04T01:00:00+00:00",
  ends_at: "2027-03-04T02:00:00+00:00",
  location: null,
  status: "scheduled",
  viewer_relation: "attendee",
  created_by: "mina",
  attendee_count: 2,
  created_by_display_name: "민아 (구성원)",
};

const saved = (over: Partial<DirectTask> = {}): DirectTask =>
  ({ task_id: "flip", title: "뒤집힌 업무", state: "in_progress", version: 8, block_reason: null, ...over }) as DirectTask;

/**
 * jsdom 에는 `DragEvent`·`PointerEvent` 의 좌표가 없다 — 그래서 `MouseEvent` 로 만들고
 * `dataTransfer` 만 얹는다. **`clientY` 가 실제로 실려야** 시간 격자의 분 계산을 검사할 수 있다.
 */
function pointerish(type: string, init: { taskId?: string; clientY?: number } = {}): Event {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientY: init.clientY ?? 0 });
  if (init.taskId !== undefined) {
    Object.defineProperty(event, "dataTransfer", {
      value: { getData: () => init.taskId, setData: () => undefined, effectAllowed: "" },
    });
  }
  return event;
}

beforeEach(() => {
  for (const spy of [getCalendar, updateTask, createTaskSchedule, updateTaskSchedule, getTask, onError, onNotice]) spy.mockReset();
  getCalendar.mockResolvedValue([flipped, undated, meeting]);
  updateTask.mockResolvedValue(saved({ schedule_release: { released_count: 0, reason: null } }));
  createTaskSchedule.mockResolvedValue({});
  updateTaskSchedule.mockResolvedValue({});
});
afterEach(cleanup);

/** 격자와 **레일이 둘 다 선 뒤에** 돌려준다 — 레일은 셸 슬롯에 등록되므로 한 박자 늦다. */
async function ready(canManage = true) {
  const view = render(<Harness canManage={canManage} />);
  await waitFor(() => expect(getCalendar).toHaveBeenCalled());
  await waitFor(() => expect(view.container.querySelectorAll(".scax-inbox-card").length).toBeGreaterThan(0));
  return view;
}

/** 좌측 카드를 집어 그 날 칸에 떨어뜨린다. */
function dragCardTo(container: HTMLElement, taskId: string, selector: string, clientY = 0) {
  const card = container.querySelector(`[data-calendar-key="task:${taskId}"]`) as HTMLElement;
  fireEvent(card, pointerish("dragstart", { taskId }));
  const target = container.querySelector(selector) as HTMLElement;
  fireEvent(target, pointerish("dragover", { clientY, taskId }));
  fireEvent(target, pointerish("drop", { clientY, taskId }));
  return target;
}

describe("R1 — 날짜 칸 드롭은 실제 PATCH /api/tasks 로 나간다", () => {
  it("span 길이(2일)를 유지한 채 옮긴다 — 뒤집힌 원본 두 날짜의 차이가 아니다 (WARN-A)", async () => {
    const { container } = await ready();
    dragCardTo(container, "flip", '[data-date="2027-03-10"]');
    await waitFor(() => expect(updateTask).toHaveBeenCalledTimes(1));
    expect(updateTask).toHaveBeenCalledWith("flip", 7, { start_date: "2027-03-10", due_date: "2027-03-12" });
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("업무 기간을 옮겼습니다."));
  });

  it("기한 없는 업무도 떨어진다 — 거기서 그 날 하루짜리 기간이 생긴다 (R2)", async () => {
    const { container } = await ready();
    dragCardTo(container, "free", '[data-date="2027-03-10"]');
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith("free", 2, { start_date: "2027-03-10", due_date: "2027-03-10" }));
  });

  it("내 업무를 다룰 수 없으면 **말로** 거절한다 — 조용히 튕기지 않는다", async () => {
    const { container } = await ready(false);
    dragCardTo(container, "flip", '[data-date="2027-03-10"]');
    expect(onError).toHaveBeenCalledWith("내가 맡은 업무에만 시간을 배정할 수 있습니다.");
    expect(updateTask).not.toHaveBeenCalled();
  });
});

describe("R3·R4 — 좌우 손잡이 (WARN-A 가 코드에 있다)", () => {
  /** 포인터가 지나는 칸을 읽는 자리를 세운다 — jsdom 에는 배치가 없어 이 함수가 늘 null 이다. */
  function pointAt(container: HTMLElement, date: string) {
    const cell = container.querySelector(`[data-date="${date}"]`) as HTMLElement;
    document.elementFromPoint = () => cell;
  }

  it("뒤집힌 업무에서 start 손잡이를 끌면 **start_date 가** 바뀐다", async () => {
    const { container } = await ready();
    const handle = container.querySelector('[data-date="2027-03-03"] .scax-event__handle--start') as HTMLElement;
    expect(handle).not.toBeNull();
    fireEvent.pointerDown(handle);
    pointAt(container, "2027-03-02");
    fireEvent(window, pointerish("pointermove"));
    fireEvent(window, pointerish("pointerup"));
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith("flip", 7, { start_date: "2027-03-02" }));
  });

  it("end 손잡이는 due_date 를 바꾼다 — 뒤집힌 업무에서도 그렇다", async () => {
    const { container } = await ready();
    const handle = container.querySelector('[data-date="2027-03-05"] .scax-event__handle--end') as HTMLElement;
    fireEvent.pointerDown(handle);
    pointAt(container, "2027-03-09");
    fireEvent(window, pointerish("pointermove"));
    fireEvent(window, pointerish("pointerup"));
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith("flip", 7, { due_date: "2027-03-09" }));
  });

  it("역전은 조용히 접지 않고 말한다 — 시작이 마감을 넘으면 아무것도 보내지 않는다", async () => {
    const { container } = await ready();
    const handle = container.querySelector('[data-date="2027-03-03"] .scax-event__handle--start') as HTMLElement;
    fireEvent.pointerDown(handle);
    pointAt(container, "2027-03-20");
    fireEvent(window, pointerish("pointermove"));
    fireEvent(window, pointerish("pointerup"));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("시작일은 마감일보다 뒤일 수 없습니다."));
    expect(updateTask).not.toHaveBeenCalled();
  });

  it("R4 — 마감만 있던 업무가 start 손잡이로 기간을 갖는다", async () => {
    const dueOnly: CalendarEntry = { ...flipped, task_id: "due", title: "마감만", start_date: null, due_date: "2027-03-04", span_from: "2027-03-04", span_to: "2027-03-04", version: 3 };
    getCalendar.mockResolvedValue([dueOnly]);
    const { container } = render(<Harness />);
    await screen.findByText("마감만");
    const handle = container.querySelector('[data-date="2027-03-04"] .scax-event__handle--start') as HTMLElement;
    fireEvent.pointerDown(handle);
    pointAt(container, "2027-03-02");
    fireEvent(window, pointerish("pointermove"));
    fireEvent(window, pointerish("pointerup"));
    await waitFor(() => expect(updateTask).toHaveBeenCalledWith("due", 3, { start_date: "2027-03-02" }));
  });
});

describe("R5 — 시간 격자 드롭", () => {
  async function weekReady(rows: CalendarEntry[] = [flipped, undated, meeting]) {
    getCalendar.mockResolvedValue(rows);
    const view = await ready();
    fireEvent.click(screen.getByRole("tab", { name: "주" }));
    await waitFor(() => expect(view.container.querySelector(".scax-week")).not.toBeNull());
    // 뷰를 바꾸면 그 구간을 다시 한 번 받는다 — 그 동안 레일은 스켈레톤이라 카드가 잠시 사라진다.
    await waitFor(() => expect(view.container.querySelectorAll(".scax-inbox-card").length).toBeGreaterThan(0));
    return view;
  }

  const hours = (date: string) => `.scax-week__hours[data-date="${date}"]`;

  it("기간 안에 떨어뜨리면 POST 가 나간다 — 기본 한 시간 · 30분 눈금", async () => {
    const { container } = await weekReady();
    dragCardTo(container, "flip", hours("2027-03-03"), 560);
    await waitFor(() => expect(createTaskSchedule).toHaveBeenCalledTimes(1));
    expect(createTaskSchedule.mock.calls[0][0]).toBe("flip");
    expect(createTaskSchedule.mock.calls[0][1]).toEqual({ on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:00" });
    expect(typeof createTaskSchedule.mock.calls[0][2]).toBe("string");
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("시간을 배정했습니다."));
  });

  it("**연타는 같은 멱등 키**로 나간다 — 두 번째가 409 가 아니라 영수증이 되게 (K12)", async () => {
    const { container } = await weekReady();
    dragCardTo(container, "flip", hours("2027-03-03"), 560);
    await waitFor(() => expect(createTaskSchedule).toHaveBeenCalledTimes(1));
    dragCardTo(container, "flip", hours("2027-03-03"), 560);
    await waitFor(() => expect(createTaskSchedule).toHaveBeenCalledTimes(2));
    expect(createTaskSchedule.mock.calls[1][2]).toBe(createTaskSchedule.mock.calls[0][2]);
  });

  it("**같은 날에 이미 배정이 있으면 POST 가 아니라 PATCH** 다 (K10) — 409 를 보는 자리가 없다", async () => {
    const booked: CalendarEntry = {
      ...flipped,
      schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 4 }],
    };
    const { container } = await weekReady([booked]);
    dragCardTo(container, "flip", hours("2027-03-03"), 784);
    await waitFor(() => expect(updateTaskSchedule).toHaveBeenCalledTimes(1));
    // expected_version 은 **그 배정 자신의 회차**다 — 업무 회차(7)가 아니다 (K8).
    expect(updateTaskSchedule).toHaveBeenCalledWith("s1", 4, { starts_at: "14:00", ends_at: "15:00" });
    expect(createTaskSchedule).not.toHaveBeenCalled();
  });

  it("기간 밖이면 **정규화 구간을 문구에 넣어** 거절한다 — 아무것도 보내지 않는다", async () => {
    const { container } = await weekReady();
    dragCardTo(container, "flip", hours("2027-03-06"), 560);
    expect(onError).toHaveBeenCalledWith("이 업무의 기간(2027/03/03~2027/03/05) 안에만 시간을 배정할 수 있습니다.");
    expect(createTaskSchedule).not.toHaveBeenCalled();
  });

  it("기한 없는 업무는 「날짜부터」라고 말한다 (R2)", async () => {
    const { container } = await weekReady();
    dragCardTo(container, "free", hours("2027-03-03"), 560);
    expect(onError).toHaveBeenCalledWith("먼저 업무 기간을 정해 주세요. 기간이 있어야 시간을 배정할 수 있습니다.");
    expect(createTaskSchedule).not.toHaveBeenCalled();
  });

  it("서버가 거절하면 **우리 문구**를 낸다 — 본문에 code 가 없고 한 자리는 영문이다", async () => {
    createTaskSchedule.mockRejectedValue(new ApiError(403, "…"));
    const { container } = await weekReady();
    dragCardTo(container, "flip", hours("2027-03-03"), 560);
    await waitFor(() => expect(onError).toHaveBeenCalledWith("내가 맡은 업무에만 시간을 배정할 수 있습니다."));
  });
});

describe("R6 — 시간 블록의 세로 손잡이", () => {
  const booked: CalendarEntry = {
    ...flipped,
    schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 4 }],
  };

  async function weekWithSlot() {
    getCalendar.mockResolvedValue([booked, meeting]);
    const view = await ready();
    fireEvent.click(screen.getByRole("tab", { name: "주" }));
    await waitFor(() => expect(view.container.querySelector(".scax-week__slot")).not.toBeNull());
    return view;
  }

  it("**놓을 때 한 번만** 부르고, 끌지 않은 쪽의 시각은 그대로 보낸다", async () => {
    const { container } = await weekWithSlot();
    const handle = container.querySelector(".scax-week__slot-handle--end") as HTMLElement;
    fireEvent.pointerDown(handle);
    fireEvent(window, pointerish("pointermove", { clientY: 1120 }));
    fireEvent(window, pointerish("pointermove", { clientY: 1124 }));
    expect(updateTaskSchedule).not.toHaveBeenCalled();
    fireEvent(window, pointerish("pointerup"));
    await waitFor(() => expect(updateTaskSchedule).toHaveBeenCalledTimes(1));
    expect(updateTaskSchedule).toHaveBeenCalledWith("s1", 4, { starts_at: "10:00", ends_at: "20:00" });
  });

  it("역전은 말로 거절한다", async () => {
    const { container } = await weekWithSlot();
    const handle = container.querySelector(".scax-week__slot-handle--end") as HTMLElement;
    fireEvent.pointerDown(handle);
    fireEvent(window, pointerish("pointermove", { clientY: 0 }));
    fireEvent(window, pointerish("pointerup"));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("종료 시각은 시작 시각보다 뒤여야 합니다."));
    expect(updateTaskSchedule).not.toHaveBeenCalled();
  });

  it("**회의 블록에는 손잡이가 없다** (§F) — 캘린더는 회의에 쓰기를 내지 않는다", async () => {
    const { container } = await weekWithSlot();
    const meetingBlock = container.querySelector(".scax-event--meeting.scax-week__slot") as HTMLElement;
    expect(meetingBlock).not.toBeNull();
    expect(meetingBlock.querySelector(".scax-week__slot-handle")).toBeNull();
    const taskBlock = container.querySelector(".scax-event--task.scax-week__slot") as HTMLElement;
    expect(taskBlock.querySelectorAll(".scax-week__slot-handle")).toHaveLength(2);
  });
});

describe("회의는 캘린더에서 읽기 전용이다 (§F)", () => {
  it("회의 카드는 끌 수 없다", async () => {
    const { container } = await ready();
    const card = container.querySelector('[data-calendar-key="meeting:m1"]') as HTMLElement;
    expect(card.getAttribute("draggable")).not.toBe("true");
    expect(container.querySelector('[data-calendar-key="task:flip"]')!.getAttribute("draggable")).toBe("true");
  });

  it("업무 띠에만 손잡이가 붙는다 — 회의 띠에는 없다", async () => {
    const { container } = await ready();
    expect(container.querySelectorAll(".scax-event--meeting .scax-event__handle")).toHaveLength(0);
    expect(container.querySelectorAll(".scax-event--task .scax-event__handle").length).toBeGreaterThan(0);
  });
});

describe("K3 — 기간이 줄면 몇 건이 해제됐는지 말한다", () => {
  it("닫힌 건수가 있으면 성공 문장에 이어 말한다", async () => {
    updateTask.mockResolvedValue(saved({ schedule_release: { released_count: 2, reason: "out_of_range" } }));
    const { container } = await ready();
    dragCardTo(container, "flip", '[data-date="2027-03-10"]');
    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith("업무 기간을 옮겼습니다. 2건의 시간 배정이 기간 밖이라 해제되었습니다."),
    );
  });

  it("0 건이면 **아무 말도 하지 않는다**", async () => {
    const { container } = await ready();
    dragCardTo(container, "flip", '[data-date="2027-03-10"]');
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("업무 기간을 옮겼습니다."));
    expect(onNotice.mock.calls.flat().join(" ")).not.toContain("해제");
  });

  it("칸이 아예 없는 응답에서도 아무 말도 하지 않는다 — /block·/resume 은 이 칸을 싣지 않는다", async () => {
    updateTask.mockResolvedValue(saved());
    const { container } = await ready();
    dragCardTo(container, "flip", '[data-date="2027-03-10"]');
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("업무 기간을 옮겼습니다."));
    expect(onNotice.mock.calls.flat().join(" ")).not.toContain("해제");
  });
});

describe("K17 — 머리의 「업무 만들기」", () => {
  it("단추가 셸 머리에 서고, 떠날 때 지워진다", async () => {
    const { unmount } = await ready();
    const header = screen.getByTestId("header");
    expect(header.textContent).toContain("업무 만들기");
    unmount();
  });

  it("누르면 **기존 업무 생성 모달**이 열린다 — 시안의 필드 구성을 따라가지 않는다", async () => {
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "업무 만들기" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });
});
