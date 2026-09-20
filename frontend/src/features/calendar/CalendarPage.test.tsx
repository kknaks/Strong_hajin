import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CalendarEntry } from "../../lib/viewModels";
import { CalendarPage } from "./CalendarPage";

vi.mock("../../lib/labels", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/labels")>()),
  seoulToday: () => "2027-03-04",
}));

const getCalendar = vi.fn<(from: string, to: string) => Promise<CalendarEntry[]>>();
vi.mock("../../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/api")>()),
  getCalendar: (from: string, to: string) => getCalendar(from, to),
  getTask: vi.fn(),
  transitionDirectTask: vi.fn(),
  updateTask: vi.fn(),
  createTaskSchedule: vi.fn(),
  updateTaskSchedule: vi.fn(),
}));

/* 셸이 넘기는 콜백은 «렌더마다 같은 것»이다(`App.tsx` 의 setState 들). 여기서 렌더마다 새로 만들면
   화면의 등록 effect 가 자기 자신을 다시 깨워 무한히 돈다 — App 에는 없는 함정이라 여기서도 고정한다. */
const noop = vi.fn();

/** 레일은 셸의 칸에 선다 — 화면이 등록한 것을 여기서 대신 그려 셸과 같은 3분할을 만든다. */
function Harness() {
  const [rails, setRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
  return (
    <div>
      {rails.left ? <aside data-testid="rail-left">{rails.left}</aside> : null}
      {rails.right ? <aside data-testid="rail-right">{rails.right}</aside> : null}
      <CalendarPage
        canAssignTasks={false}
        canCreateWorkRequests={false}
        canManageOwnTasks
        onAskAboutTask={noop}
        onError={noop}
        onNotice={noop}
        onRegisterRails={setRails}
        personaId="me"
        personaName="유하람 (기획자)"
        personas={[]}
      />
    </div>
  );
}

const flipped: CalendarEntry = {
  kind: "task",
  task_id: "flip",
  title: "뒤집힌 업무",
  state: "in_progress",
  start_date: "2027-03-06",
  due_date: "2027-03-04",
  span_from: "2027-03-04",
  span_to: "2027-03-06",
  version: 3,
  schedules: [{ schedule_id: "s-flip", on_date: "2027-03-05", starts_at: "10:00", ends_at: "11:30", version: 1 }],
};

const shared: CalendarEntry = {
  kind: "meeting",
  meeting_id: "m-next",
  title: "다음 주 공유 회의",
  starts_at: "2027-03-09T01:00:00+00:00",
  ends_at: "2027-03-09T02:00:00+00:00",
  location: "3층 회의실",
  status: "scheduled",
  viewer_relation: "shared",
  created_by: "mina",
  attendee_count: 4,
  created_by_display_name: "민아 (구성원)",
};

const thisWeek: CalendarEntry = { ...shared, meeting_id: "m-now", title: "이번 주 회의", starts_at: "2027-03-04T01:00:00+00:00", ends_at: "2027-03-04T02:00:00+00:00", viewer_relation: "attendee" };

/** 2027-03 격자의 마지막 줄에 «달 밖 칸»으로 서는 날. 격자가 비워 두는 자리다. */
const outOfMonth: CalendarEntry = { ...shared, meeting_id: "m-out", title: "달 밖 회의", starts_at: "2027-04-01T01:00:00+00:00", ends_at: "2027-04-01T02:00:00+00:00" };

beforeEach(() => {
  getCalendar.mockReset();
  getCalendar.mockResolvedValue([flipped, thisWeek, shared, outOfMonth]);
});
afterEach(cleanup);

describe("캘린더 골격", () => {
  it("한 화면 = 한 요청 — 그려지는 칸 전부를 한 번에 받고, 탭을 바꿔도 다시 묻지 않는다", async () => {
    render(<Harness />);
    await waitFor(() => expect(getCalendar).toHaveBeenCalledTimes(1));
    // 2027-03 은 일요일 02-28 에서 시작해 04-03 까지 다섯 주다.
    expect(getCalendar).toHaveBeenCalledWith("2027-02-28", "2027-04-03");
    fireEvent.click(screen.getByRole("tab", { name: "회의" }));
    fireEvent.click(screen.getByRole("tab", { name: "업무" }));
    expect(getCalendar).toHaveBeenCalledTimes(1);
  });

  it("3분할이다 — 왼쪽 일정 레일만 서고 오른쪽 레일은 비어 있다", async () => {
    render(<Harness />);
    expect(await screen.findByTestId("rail-left")).toBeTruthy();
    expect(screen.queryByTestId("rail-right")).toBeNull();
  });

  it("뒤집힌 업무의 띠가 서버가 준 구간대로 선다 — start_date(3/6)부터가 아니다", async () => {
    const { container } = render(<Harness />);
    await waitFor(() => expect(getCalendar).toHaveBeenCalled());
    const covered = ["2027-03-04", "2027-03-05", "2027-03-06"].map(
      (date) => container.querySelectorAll(`[data-date="${date}"] .scax-event--task`).length,
    );
    expect(covered.every((count) => count > 0)).toBe(true);
    expect(container.querySelectorAll('[data-date="2027-03-03"] .scax-event--task')).toHaveLength(0);
    expect(container.querySelectorAll('[data-date="2027-03-07"] .scax-event--task')).toHaveLength(0);
  });

  it("격자는 상태를 말하지 않는다 — 조각의 유형 클래스가 둘뿐이다", async () => {
    const { container } = render(<Harness />);
    await waitFor(() => expect(getCalendar).toHaveBeenCalled());
    const kinds = new Set<string>();
    container.querySelectorAll(".scax-event").forEach((node) => {
      node.classList.forEach((name) => {
        if (name.startsWith("scax-event--")) kinds.add(name);
      });
    });
    /* 유형이 아닌 수식어들 — 띠의 모양(`bar*`)·빈 자리(`ghost`)·접힌 수(`more`)와,
       FE-2 가 더한 «손잡이가 붙는다»(`resizable`). 어느 것도 상태를 말하지 않는다. */
    const notAKind = ["scax-event--bar", "scax-event--bar-head", "scax-event--bar-tail", "scax-event--ghost", "scax-event--more", "scax-event--resizable"];
    expect([...kinds].filter((name) => !notAKind.includes(name)).sort()).toEqual(["scax-event--meeting", "scax-event--task"]);
    for (const state of ["open", "in_progress", "blocked", "done", "cancelled"]) {
      expect(container.querySelector(`.scax-event--${state}`)).toBeNull();
    }
  });

  it("회의 카드가 주최자 이름을 내고 member id 를 내지 않는다", async () => {
    render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    expect(await screen.findByText("이번 주 회의")).toBeTruthy();
    expect(rail.textContent).toContain("민아");
    expect(rail.textContent).not.toContain("mina");
  });

  it("업무 카드는 상태를 내지 않고 유형 배지 하나만 낸다", async () => {
    render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    expect(rail.textContent).toContain("업무");
    for (const label of ["진행 중", "시작 전", "완료", "막힘", "취소"]) {
      expect(rail.textContent).not.toContain(label);
    }
  });

  it("업무의 시간 배정은 자기 카드가 아니라 업무 카드의 meta 줄로 접힌다", async () => {
    render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    await waitFor(() => expect(rail.textContent).toContain("5일 10:00"));
    expect(rail.querySelectorAll(".scax-inbox-card")).toHaveLength(3);
  });

  it("탭이 레일과 격자를 동시에 가른다", async () => {
    const { container } = render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    fireEvent.click(screen.getByRole("tab", { name: "회의" }));
    expect(rail.textContent).not.toContain("뒤집힌 업무");
    expect(container.querySelectorAll(".scax-event--task")).toHaveLength(0);
    fireEvent.click(screen.getByRole("tab", { name: "업무" }));
    expect(rail.textContent).not.toContain("이번 주 회의");
    expect(container.querySelectorAll(".scax-event--meeting")).toHaveLength(0);
  });

  it("주 뷰는 종일 칸 + 0~24시이고 8시에 맞춰 열린다", async () => {
    const { container } = render(<Harness />);
    await waitFor(() => expect(getCalendar).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "주" }));
    await waitFor(() => expect(getCalendar).toHaveBeenCalledWith("2027-02-28", "2027-03-06"));
    expect(screen.getByText("종일")).toBeTruthy();
    expect(container.querySelectorAll(".scax-week__ruler-hour")).toHaveLength(24);
    expect(screen.getByText("오전 12시")).toBeTruthy();
    expect(screen.getByText("오후 11시")).toBeTruthy();
    // 56px × 8시간 — 이 곱은 `styles/calendar.css` 의 시간 눈금 배경과 짝이다.
    expect((container.querySelector(".scax-week__scroll") as HTMLElement).scrollTop).toBe(448);
  });

  it("공유받은 다음 주 회의가 그 주에 제자리로 선다", async () => {
    const { container } = render(<Harness />);
    await waitFor(() => expect(getCalendar).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "주" }));
    await waitFor(() => expect(getCalendar).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "다음 주" }));
    await waitFor(() => expect(getCalendar).toHaveBeenCalledWith("2027-03-07", "2027-03-13"));
    await waitFor(() =>
      expect(container.querySelectorAll('[data-date="2027-03-09"] .scax-week__slot')).toHaveLength(1),
    );
    expect(container.querySelector('[data-date="2027-03-09"] .scax-week__slot')!.textContent).toContain("다음 주 공유 회의");
  });

  it("레일과 격자가 같은 범위를 본다 — 달 밖 칸의 일정은 둘 다에 없다 (K18)", async () => {
    const { container } = render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    await waitFor(() => expect(rail.textContent).toContain("이번 주 회의"));
    // 4/1 은 3월 격자에 «달 밖 칸»으로 서고 비어 있다 — 고를 수도 없으니 data-date 가 없다.
    expect(container.querySelectorAll(".scax-month__cell--out").length).toBeGreaterThan(0);
    expect(container.querySelector('[data-date="2027-04-01"]')).toBeNull();
    expect(container.querySelectorAll(".scax-month__cell--out .scax-event")).toHaveLength(0);
    // 그러므로 레일에도 서지 않는다 — 레일에만 있고 격자에 없는 일정이 0건이다.
    expect(rail.textContent).not.toContain("달 밖 회의");
    // 조회는 좁히지 않는다 — 달 밖 칸까지 한 번에 받아 두고 «내지 않을 뿐»이다.
    expect(getCalendar).toHaveBeenCalledWith("2027-02-28", "2027-04-03");
  });

  it("같은 일정이 그 날을 품는 주 뷰에서는 레일에 선다 — 범위는 뷰를 따라간다 (K18)", async () => {
    render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    await waitFor(() => expect(rail.textContent).toContain("이번 주 회의"));
    fireEvent.click(screen.getByRole("tab", { name: "주" }));
    await waitFor(() => expect(getCalendar).toHaveBeenCalledTimes(2));
    // 2027-03-28 ~ 04-03 주로 간다 — 그 주는 4/1 을 «그리는» 주다.
    for (let jump = 0; jump < 4; jump += 1) fireEvent.click(screen.getByRole("button", { name: "다음 주" }));
    await waitFor(() => expect(getCalendar).toHaveBeenCalledWith("2027-03-28", "2027-04-03"));
    await waitFor(() => expect(rail.textContent).toContain("달 밖 회의"));
  });

  it("날짜를 고르면 레일이 그 날로 좁혀지고 주·월 이동이 선택을 푼다", async () => {
    const { container } = render(<Harness />);
    const rail = await screen.findByTestId("rail-left");
    await waitFor(() => expect(rail.textContent).toContain("이번 주 회의"));
    // 칸 전체가 단추다 — 접근성 이름에 그 날의 일정이 함께 실리므로 날짜로 집는다.
    fireEvent.click(container.querySelector('[data-date="2027-03-05"]') as HTMLElement);
    await waitFor(() => expect(rail.textContent).toContain("전체 보기"));
    expect(rail.textContent).not.toContain("이번 주 회의");
    fireEvent.click(screen.getByRole("button", { name: "다음 달" }));
    await waitFor(() => expect(rail.textContent).not.toContain("전체 보기"));
  });
});
