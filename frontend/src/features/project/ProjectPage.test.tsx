import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type React from "react";
import { useCallback, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/labels", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/labels")>()),
  /* 「오늘」은 고정값이 아니다 — 축의 today 선이 자료를 따라 서는지 보려고 여기서만 얼린다. */
  seoulToday: () => "2026-09-05",
}));

vi.mock("../../lib/api", () => ({
  assignToProject: vi.fn(),
  createProject: vi.fn(),
  getMemberDirectory: vi.fn(),
  getOrganizationTree: vi.fn(),
  getProject: vi.fn(),
  getProjectParticipationHistory: vi.fn(),
  getTask: vi.fn(),
  listProjects: vi.fn(),
  releaseFromProject: vi.fn(),
  /* 상태 드롭다운이 지나는 자리 — 전이 하나와, 완료 보고 모달이 «스스로» 읽고 보내는 둘.
     그 모달은 업무 화면의 것을 그대로 부르므로 이 화면의 테스트도 그 호출을 세워 줘야 한다. */
  transitionDirectTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  submitTaskCompletion: vi.fn(),
}));

import * as api from "../../lib/api";
import type { ProjectTaskRow } from "../../lib/viewModels";
import { ProjectPage } from "./ProjectPage";
import { GANTT } from "./projectModel";

/**
 * jsdom 에는 `scrollIntoView` 가 없다 — 화면은 `?.` 로 조용히 넘어간다(`ds/GutterList.tsx:36` 선례).
 * 자동 스크롤이 **정말 불렸는지·어느 요소에·어떤 조건으로** 불렸는지는 그래서 여기서 세워야 잰다.
 * 「불렸다」만 보면 남의 요소를 굴려도 통과하므로 **대상 요소까지** 확인한다.
 */
function captureScrollIntoView() {
  const calls: Array<{ target: Element; options: unknown }> = [];
  (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView = function stub(this: Element, options?: unknown) {
    calls.push({ target: this, options });
  };
  return calls;
}

const project = {
  project_id: "p-1",
  name: "한빛의원 통합 마케팅",
  description: "홈페이지·네이버·플레이스",
  state: "active",
  starts_on: "2026-09-01",
  ends_on: null,
  external_key: null,
  version: 1,
};

function task(row: Partial<ProjectTaskRow> & { task_id: string; title: string }): ProjectTaskRow {
  return {
    state: "open",
    start_date: null,
    due_date: null,
    parent_task_id: null,
    preceding_task_ids: [],
    assignee: null,
    checklist_progress: { done: 0, total: 0 },
    span_from: null,
    span_to: null,
    overdue_days: null,
    ...row,
  };
}

/**
 * 4층 트리 + 조부의 형제를 선행으로 가진 손자 + 기간 없는 업무 + 취소된 업무.
 * **시안 목데이터로는 만들 수 없는 모양**이고, 이 판이 닫는 인수조건이 전부 여기에 걸린다.
 */
const tasks: ProjectTaskRow[] = [
  task({
    task_id: "t-1",
    title: "한빛 9월 통합 마케팅",
    state: "in_progress",
    start_date: "2026-09-01",
    due_date: "2026-09-12",
    span_from: "2026-09-01",
    span_to: "2026-09-12",
    assignee: { member_id: "mina", display_name: "민아 (구성원)" },
    checklist_progress: { done: 2, total: 5 },
  }),
  task({
    task_id: "t-2",
    title: "홈페이지 디자인 기획",
    state: "in_progress",
    parent_task_id: "t-1",
    start_date: "2026-09-02",
    due_date: "2026-09-06",
    span_from: "2026-09-02",
    span_to: "2026-09-06",
    assignee: { member_id: "jiho", display_name: "지호 (구성원)" },
  }),
  task({
    task_id: "t-3",
    title: "시안 확정",
    parent_task_id: "t-2",
    start_date: "2026-09-04",
    due_date: "2026-09-05",
    span_from: "2026-09-04",
    span_to: "2026-09-05",
    /* 손자가 «조부의 형제»를 선행으로 갖는다 — 깊이로 잘리면 이 화살표가 사라진다. */
    preceding_task_ids: ["t-9"],
    checklist_progress: { done: 1, total: 4 },
  }),
  /* 증손자 — 체크리스트가 없고, 서버가 기한 경과일을 낸 유일한 업무다. */
  task({
    task_id: "t-4",
    title: "썸네일 제작",
    parent_task_id: "t-3",
    start_date: null,
    due_date: "2026-09-07",
    span_from: "2026-09-07",
    span_to: "2026-09-07",
    overdue_days: 3,
  }),
  /* 체크리스트 없이 끝난 업무 — 그래도 100% 다. */
  task({
    task_id: "t-9",
    title: "계약 확정",
    state: "done",
    start_date: "2026-09-01",
    due_date: "2026-09-03",
    span_from: "2026-09-01",
    span_to: "2026-09-03",
  }),
  /* 하위가 없는 잎 — 4개 중 1개면 25% 다. */
  task({
    task_id: "t-5",
    title: "플레이스 등록",
    start_date: "2026-09-08",
    due_date: "2026-09-10",
    span_from: "2026-09-08",
    span_to: "2026-09-10",
    checklist_progress: { done: 1, total: 4 },
  }),
  /* `blocked` 는 계약 넷에 없지만 M-6 승계로 실제로 온다 — 떨어뜨리지 않는다. */
  task({
    task_id: "t-b",
    title: "막힌 일",
    state: "blocked",
    start_date: "2026-09-09",
    due_date: "2026-09-11",
    span_from: "2026-09-09",
    span_to: "2026-09-11",
  }),
  task({ task_id: "t-c", title: "취소된 광고안", state: "cancelled", span_from: "2026-09-02", span_to: "2026-09-04" }),
  /* 기간이 없는 업무 — 간트에 안 서고 좌 레일에만 선다. 담당도 없다. */
  task({ task_id: "t-n", title: "사람도 기간도 없는 일" }),
];

const detail = {
  ...project,
  may_manage: true,
  members: [
    { assignment_id: "current-mina", member_id: "mina", display_name: "민아", assignment_kind: "lead", valid_from: null, valid_until: null },
    { assignment_id: "current-jiho", member_id: "jiho", display_name: "지호", assignment_kind: "member", valid_from: null, valid_until: null },
  ],
  tasks,
};

const history = [
  {
    assignment_id: "ended-1",
    member_id: "jiho",
    display_name: "지호",
    assignment_kind: "member" as const,
    valid_from: null,
    valid_until: null,
    assigned_by_member_id: "mina",
    assigned_by_display_name: "민아",
    created_at: "2026-09-01T00:00:00+00:00",
    ended_at: "2026-09-10T00:00:00+00:00",
    ended_by_member_id: "mina",
    ended_by_display_name: "민아",
    end_reason: "1차 지원 종료",
  },
];

/* 셸이 넘기는 콜백은 «렌더마다 같은 것»이다(`App.tsx` 의 setState 들). 여기서 렌더마다 새로 만들면
   화면의 등록 effect 가 자기 자신을 다시 깨워 무한히 돈다. */
const noop = vi.fn();
/** 성공 문구의 자리 — 셸의 토스트다. 오류(`noop`)와 **둘이 갈린다.** */
const notice = vi.fn();

/** 화면이 등록한 «모든» 레일 — 마지막 상태만이 아니라 **지나간 프레임까지** 남긴다 (D-22). */
const railLog: Array<{ left?: React.ReactNode; right?: React.ReactNode }> = [];

/**
 * 레일과 머리 액션은 셸의 칸에 선다 — 화면이 등록한 것을 여기서 대신 그려 셸과 같은 3레일을 만든다.
 *
 * **세션 역량 둘은 셸이 내린다** (FE-5 작업 0) — `App.tsx` 가 `has("project.manage")` ·
 * `has("task.self_manage")` 로 계산해 이 두 prop 에만 싣는다. 여기서도 **값으로** 넘긴다:
 * 데모 페르소나에 그 역량이 있는지에 기대지 않고 **코드 자리로** 잰다 (L-17 · L-50).
 */
function Harness({
  personaId = "mina",
  canManageOwnTasks = false,
  mounted = true,
}: {
  personaId?: string;
  canManageOwnTasks?: boolean;
  mounted?: boolean;
}) {
  const [rails, setRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
  const [actions, setActions] = useState<React.ReactNode>(null);
  /* **셸은 그 문구를 «그린다».** `App.tsx` 가 오류를 빨간 띠로, 성공을 토스트로 내는 자리다 —
     「불렸다」만 재면 부르는 쪽이 빈 함수로 받아도 통과한다(검수 FAIL-1 이 바로 그 자리였다). */
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  /* 셸의 setState 와 같이 «렌더마다 같은» 함수여야 한다 — 매번 새로 만들면 등록 effect 가 무한히 돈다. */
  const registerRails = useCallback((next: { left?: React.ReactNode; right?: React.ReactNode }) => {
    railLog.push(next);
    setRails(next);
  }, []);
  const handleError = useCallback((message: string | null) => {
    noop(message);
    setError(message);
  }, []);
  const handleNotice = useCallback((message: string) => {
    notice(message);
    setToast(message);
  }, []);
  return (
    <div>
      <header data-testid="header-actions">{actions}</header>
      {error ? <p data-testid="error-banner">{error}</p> : null}
      {toast ? <p data-testid="toast">{toast}</p> : null}
      {rails.left ? <aside data-testid="rail-left">{rails.left}</aside> : null}
      {rails.right ? <aside data-testid="rail-right">{rails.right}</aside> : null}
      {mounted ? (
        <ProjectPage
          canManageOwnTasks={canManageOwnTasks}
          onError={handleError}
          onNotice={handleNotice}
          onOpenTask={noop}
          onRegisterHeaderActions={setActions}
          onRegisterRails={registerRails}
          personaId={personaId}
        />
      ) : null}
    </div>
  );
}

/** 업무 상세의 기본꼴 — `tasks[]` 에는 없는 것들(`version`·`access`·`checklist`·`description`)이 여기 산다. */
function taskDetail(row: Record<string, unknown>) {
  return { state: "open", version: 1, block_reason: null, access: "read_only", ...row };
}

function renderPage(
  personaId = "mina",
  overrides: Record<string, unknown> = {},
  session: { canManageOwnTasks?: boolean; task?: Record<string, unknown> } = {},
) {
  vi.mocked(api.listProjects).mockResolvedValue([project] as never);
  vi.mocked(api.getProject).mockResolvedValue({ ...detail, ...overrides } as never);
  vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
  /* 프로젝트 상세를 «몇 번» 읽었나를 재는 단언이 있다(다시 읽기 · 안 다시 읽기) — 판마다 턴다. */
  vi.mocked(api.getProject).mockClear();
  /* 호출 «횟수» 를 재는 단언이 있다 — 앞 테스트가 고른 업무가 이 판의 수에 섞이지 않게 여기서 턴다. */
  vi.mocked(api.getTask).mockClear();
  /* **누적을 보는 단언이 더 있다** — `not.toHaveBeenCalled()` 와 콜백 스파이. 선언 «순서» 에 기대면
     테스트를 옮기는 순간 거짓이 된다(검수 WARN-5). 위 둘과 같은 자리에서 함께 턴다. */
  vi.mocked(api.createProject).mockClear();
  noop.mockClear();
  notice.mockClear();
  /* 업무 상세는 **여기서** 세운다 — 렌더 «전에» 밖에서 세우면 이 줄이 도로 덮어쓴다. */
  vi.mocked(api.getTask).mockResolvedValue(
    taskDetail(session.task ?? { task_id: "t-1", title: "한빛 9월 통합 마케팅", state: "in_progress" }) as never,
  );
  vi.mocked(api.getMemberDirectory).mockResolvedValue([
    { id: "mina", display_name: "민아" },
    { id: "jiho", display_name: "지호" },
    { id: "m-han", display_name: "한별" },
  ] as never);
  return render(<Harness canManageOwnTasks={session.canManageOwnTasks ?? false} personaId={personaId} />);
}

/** 좌 레일 카드를 골라 우 레일을 연다 — 선택 축은 하나다. */
async function pick(title: string) {
  const card = await screen.findByText(title, { selector: ".scax-inbox-card__title" });
  fireEvent.click(card.closest(".scax-inbox-card") as HTMLElement);
  return screen.findByTestId("rail-right");
}

/** 관리 모달을 연다 — 관리 기능은 본문이 아니라 여기 있다 (D-05). */
async function openManage() {
  const handle = await screen.findByRole("button", { name: "프로젝트 관리" });
  fireEvent.click(handle);
  return screen.findByRole("dialog", { name: "프로젝트 관리" });
}

describe("프로젝트", () => {
  afterEach(() => {
    cleanup();
    railLog.length = 0;
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  /* 바퀴 8-C §1 이 세운 규칙은 그대로다 — 셸 머리(AppHeader)가 이미 「프로젝트」를 이고 있다.
     WORK-005 가 바꾼 것은 그 아래다: 설명 줄(`.screens-b-lead`)이 사라지고 본문 첫 줄이 요약 스트립이다. */
  it("제목을 스스로 또 그리지 않고, 본문 첫 줄이 요약 스트립이다", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    expect(container.querySelector(".page-head")).toBeNull();
    /* ⚠ 2026-09-22(FE-5): **좌 레일 머리가 이제 「프로젝트」라는 제목을 «정당하게» 인다**(D-34).
       그래서 이 단언은 **본문 안으로** 좁힌다 — 재는 것은 「셸 머리를 본문이 또 그리지 않는다」이고,
       화면 전체에서 그 이름을 찾으면 레일 머리를 잡아 엉뚱한 자리가 깨진다. */
    expect(within(container.querySelector(".scax-pj-view") as HTMLElement).queryByRole("heading", { name: /프로젝트/ })).toBeNull();
    /* 시안 본문에는 설명 줄이 없다 — 조직·관계탐색·보고가 함께 쓰는 `.screens-b-lead` 를 여기서 놓았다. */
    expect(container.querySelector(".screens-b-lead")).toBeNull();
    expect(container.querySelector(".scax-pj-view > .scax-pj-summary")).toBeTruthy();
  });

  it("좌 레일 카드가 「분류」 없이 기간과 담당을 싣고, 담당이 없으면 그 칸을 비운다", async () => {
    renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-inbox-card__title" });
    const rail = screen.getByTestId("rail-left");
    const withOwner = within(rail).getByText("한빛 9월 통합 마케팅").closest(".scax-inbox-card") as HTMLElement;
    expect(within(withOwner).getByText("민아")).toBeTruthy();
    expect(withOwner.textContent).toContain("2026/09/01 ~ 2026/09/12");
    /* 「내 업무 / 보낸 업무」 같은 분류를 싣지 않는다 (D-08). */
    expect(withOwner.textContent).not.toContain("내 업무");

    const noOwner = within(rail).getByText("사람도 기간도 없는 일").closest(".scax-inbox-card") as HTMLElement;
    /* 「미정」을 지어내지 않는다 — meta 칸이 기간 하나뿐이다. */
    expect(noOwner.querySelectorAll(".scax-inbox-card__meta-sep").length).toBe(0);
    expect(noOwner.textContent).toContain("기한 없음");

    /* 취소된 업무는 «세지 않을» 뿐 감추지 않는다. */
    expect(within(rail).getByText("취소된 광고안")).toBeTruthy();
  });

  it("요약 스트립이 취소를 뺀 하나의 모수로 세고, 지연은 서버가 낸 업무만 센다", async () => {
    const { container } = renderPage();
    /* 좌 레일 배지와 나란히 세므로 레일이 선 뒤에 읽는다. */
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-inbox-card__title" });
    const value = (key: string) =>
      container.querySelector(`[data-summary="${key}"] .scax-pj-summary__value`)?.textContent;
    /* 업무 9건 중 취소 1건 → 모수 8. **좌 레일 배지(9)와 다른 것을 센다** — 세지 않는 것과 감추는 것은 다르다. */
    expect(screen.getByTestId("rail-left").querySelector(".scax-badge--count")?.textContent).toBe("9");
    expect(value("total")).toBe("8");
    expect(value("in-progress")).toBe("2");
    expect(value("done")).toBe("1");
    /* 서버가 `overdue_days` 를 낸 업무는 t-4 하나뿐이다 — 화면이 「오늘」을 다시 판정하지 않는다. */
    expect(value("overdue")).toBe("1");
    /* 완료 ÷ 전체 업무. 업무별 % 의 평균이 아니다. */
    expect(container.querySelector('[data-summary="percent"] .scax-pj-summary__pct')?.textContent).toBe("13%");
  });

  it("간트가 깊이를 자르지 않고, 기간 없는 업무만 빠진다", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const depthOf = (taskId: string) =>
      container.querySelector(`.scax-pj-gantt__row[data-task-id="${taskId}"]`)?.getAttribute("data-depth");
    /* 4층 — 손자도 증손자도 자기 행을 갖는다 (D-16). */
    expect(depthOf("t-1")).toBe("0");
    expect(depthOf("t-2")).toBe("1");
    expect(depthOf("t-3")).toBe("2");
    expect(depthOf("t-4")).toBe("3");
    /* 기간이 없으면 간트에 안 서고 좌 레일에만 선다 (D-09). */
    expect(container.querySelector('.scax-pj-gantt__row[data-task-id="t-n"]')).toBeNull();
    /* twisty 는 자식이 있는 «모든 깊이» 에 서고 기본은 모두 펼침이다. */
    const twisty = container.querySelector(
      '.scax-pj-gantt__row[data-task-id="t-3"] .scax-pj-gantt__twisty',
    ) as HTMLElement;
    expect(twisty.getAttribute("aria-expanded")).toBe("true");
  });

  /* ── 2루프 FE-4: 틀고정 · 첫 진입 오늘 · 자동 스크롤 (D-35 일부 · D-36 · D-37) ── */

  it("머리줄의 이름 칸 자리를 «요소가» 덮는다 — 비운 padding 으로 돌아가지 않았다 (L-44)", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const axis = container.querySelector(".scax-pj-gantt__axis") as HTMLElement;
    const pad = container.querySelector(".scax-pj-gantt__axis-pad") as HTMLElement;
    const nameCell = container.querySelector(".scax-pj-gantt__name-cell") as HTMLElement;
    /* 덮는 것과 비우는 것은 다르다 — `paddingLeft` 로 비운 자리는 스크롤한 날짜를 가리지 못했다.
       고정은 CSS 라 jsdom 이 못 재지만, **덮을 요소가 있는가**와 **그 폭이 이름 칸과 같은가**는 잰다. */
    expect(pad).toBeTruthy();
    expect(axis.style.paddingLeft).toBe("");
    expect(pad.style.width).toBe(`${GANTT.label}px`);
    expect(nameCell.style.width).toBe(`${GANTT.label}px`);
    /* 날짜 칸은 그 자리 «뒤» 에 선다 — 스페이서가 머리줄의 첫 칸이다. */
    expect(axis.firstElementChild).toBe(pad);
  });

  it("첫 진입은 «오늘» 이 보이는 자리에서 연다 — 기간의 첫날이 아니다 (L-45)", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const scroll = container.querySelector(".scax-pj-gantt__scroll") as HTMLElement;
    /* 축은 2026-09-01 ~ 09-12, 오늘은 09-05 (위의 `seoulToday` 고정) → 넷째 날.
       앞 두 날을 남기므로 **(4 - 2) × 34px = 68px** 다 — 구현 상수를 되계산하지 않고 «기대하는 수»
       를 적는다. 되계산하면 `openLead` 를 0 으로 바꿔도 단언이 따라 움직여 아무것도 붙들지 않는다.
       **0 이 아니라는 것이 이 판의 요점이다.**
       이 값을 쓰는 것은 «패시브 effect»(`ProjectGantt.tsx` 첫 진입 스크롤)라 `findByText` 가 돌아온
       커밋과 같은 태스크에서 보장되지 않는다 — 부하가 걸리면 0 으로 먼저 읽힌다. 그래서 `waitFor` 다
       (선례 `features/calendar/CalendarPage.test.tsx:268`). */
    await waitFor(() => expect(scroll.scrollLeft).toBe(68));
    expect(scroll.scrollLeft).not.toBe(0);
  });

  it("오늘이 기간 밖이면 가장 가까운 끝으로 접는다 — 없는 날로 스크롤하지 않는다", async () => {
    /* 이미 끝난 프로젝트(8월) 를 9월 5일에 연다 — 오늘은 축 오른쪽 «밖» 이다. */
    const { container } = renderPage("mina", {
      tasks: [task({ task_id: "past", title: "지난 달 일", span_from: "2026-08-01", span_to: "2026-08-20" })],
    });
    await screen.findByText("지난 달 일", { selector: ".scax-pj-gantt__name-text" });
    const scroll = container.querySelector(".scax-pj-gantt__scroll") as HTMLElement;
    /* 20일 축의 마지막 날(offset 19)에서 두 날을 남긴다 → **(19 - 2) × 34px = 578px.**
       축 밖의 35일째로 밀지 않는다. 여기도 패시브 effect 의 값이라 `waitFor` 로 읽는다. */
    await waitFor(() => expect(scroll.scrollLeft).toBe(578));
  });

  it("아직 시작 안 한 프로젝트는 기간의 «첫날» 에서 연다 — 오늘이 축 왼쪽 밖이다", async () => {
    /**
     * **계약이 비운 자리를 우리가 정한다** — L-45 는 「오늘이 보이는 위치」만 말하는데 오늘이 축
     * **왼쪽** 밖이면 보일 「오늘」이 애초에 없다. 그러면 **가장 가까운 끝 = 기간의 첫날**이다
     * (오른쪽 밖의 대칭). SPEC 문면과는 어긋나므로 리포트로 올린다.
     *
     * 단언이 **정말 무는** 자리로 만들려고 프로젝트를 «전환»한다 — jsdom 의 `scrollLeft` 초기값이
     * 0 이라 첫 렌더에서 `toBe(0)` 을 재면 effect 가 안 돌아도 통과한다. 68 에서 열린 화면을
     * 미래 프로젝트로 갈아타면 **0 을 덮어써야** 통과한다.
     */
    const future = { ...project, project_id: "p-2", name: "내년 리뉴얼", starts_on: "2026-10-01" };
    vi.mocked(api.listProjects).mockResolvedValue([project, future] as never);
    vi.mocked(api.getProject).mockImplementation((async (projectId: string) =>
      projectId === "p-2"
        ? { ...detail, ...future, tasks: [task({ task_id: "later", title: "내년 일", span_from: "2026-10-01", span_to: "2026-10-09" })] }
        : detail) as never);
    vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    const { container } = render(<Harness />);

    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const scroll = container.querySelector(".scax-pj-gantt__scroll") as HTMLElement;
    await waitFor(() => expect(scroll.scrollLeft).toBe(68));

    /* 프로젝트 전환은 레일 헤더의 셀렉터가 한다 (D-05 · 선례는 아래 「프로젝트를 바꾸면…」). */
    fireEvent.click(screen.getByRole("button", { name: "프로젝트 선택" }));
    fireEvent.click(await screen.findByRole("option", { name: "내년 리뉴얼" }));
    await screen.findByText("내년 일", { selector: ".scax-pj-gantt__name-text" });

    /* 축이 10-01 ~ 10-09 이고 오늘(09-05)은 그 왼쪽 밖이다 → 첫날에서 연다. */
    const next = container.querySelector(".scax-pj-gantt__scroll") as HTMLElement;
    await waitFor(() => expect(next.scrollLeft).toBe(0));
    /* 「오늘」 선은 축 밖이라 서지 않는다 — 없는 날에 선을 그리지 않는다. */
    expect(container.querySelector(".scax-pj-gantt__now")).toBeNull();
  });

  it("간트에서 고르면 좌 레일 카드가 «보이는 자리로» 온다 — 선택 표시만 서지 않는다 (L-46)", async () => {
    const calls = captureScrollIntoView();
    const { container } = renderPage();
    await screen.findByText("플레이스 등록", { selector: ".scax-pj-gantt__name-text" });
    expect(calls.length).toBe(0);

    fireEvent.click(
      container.querySelector('.scax-pj-gantt__row[data-task-id="t-5"] .scax-pj-gantt__name') as HTMLElement,
    );
    const card = screen
      .getByTestId("rail-left")
      .querySelector('.scax-inbox-card[data-task-id="t-5"]') as HTMLElement;
    expect(card.className).toContain("scax-inbox-card--selected");
    const hit = calls.find((call) => call.target === card);
    /* **그 카드가** 굴렀는지까지 본다. 그리고 `nearest` 다 — 이미 보이는 카드는 움직이지 않는다. */
    expect(hit).toBeTruthy();
    expect(hit?.options).toEqual({ block: "nearest" });
  });

  it("좌 레일에서 고르면 간트가 그 행이 보이는 자리로 온다 — 가로 자리는 빼앗지 않는다 (L-47)", async () => {
    const calls = captureScrollIntoView();
    const { container } = renderPage();
    const railCard = await screen.findByText("플레이스 등록", { selector: ".scax-inbox-card__title" });
    fireEvent.click(railCard.closest(".scax-inbox-card") as HTMLElement);

    const row = container.querySelector('.scax-pj-gantt__row[data-task-id="t-5"]') as HTMLElement;
    const hit = calls.find((call) => call.target === row);
    expect(hit).toBeTruthy();
    /* 가로도 `nearest` 다 — 행은 스크롤 폭 전체를 차지하므로 첫 진입에 맞춰 둔 «오늘» 자리가 남는다.
       그 자리는 68px(= (4-2)×34)이고, 여기서도 구현 상수를 되계산하지 않는다. */
    expect(hit?.options).toEqual({ block: "nearest", inline: "nearest" });
    expect((container.querySelector(".scax-pj-gantt__scroll") as HTMLElement).scrollLeft).toBe(68);
  });

  it("가지를 접어도 선이 사라지지 않는다 — 접힌 부모 바가 그 선을 들고 있다", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    /* 손자(t-3)가 조부의 형제(t-9)를 선행으로 갖는다 — 펼친 상태에서 화살표가 하나 선다. */
    expect(container.querySelectorAll(".scax-pj-gantt__link").length).toBe(1);

    fireEvent.click(
      container.querySelector('.scax-pj-gantt__row[data-task-id="t-1"] .scax-pj-gantt__twisty') as HTMLElement,
    );
    /* t-3 이 접혀 사라져도 그 선은 t-1 의 바로 끌려와 **여전히 하나**다. 시안의 `if (!a || !b) return`
       을 그대로 옮겼다면 여기서 0 이 되고 화면이 「선행 없음」이라고 거짓말한다 (D-17). */
    expect(container.querySelectorAll(".scax-pj-gantt__link").length).toBe(1);
    expect(container.querySelector('.scax-pj-gantt__row[data-task-id="t-3"]')).toBeNull();
  });

  it("바의 % 는 체크리스트가 정본이다 — 없으면 안 그리고, 상위와 취소는 % 를 내지 않는다", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const bar = (taskId: string) =>
      container.querySelector(`.scax-pj-gantt__row[data-task-id="${taskId}"] .scax-pj-gantt__bar`) as HTMLElement;
    /* 4개 중 1개 → 25%. 하위가 없는 잎이라 % 텍스트가 난다. */
    expect(bar("t-5").querySelector(".scax-pj-gantt__bar-pct")?.textContent).toBe("25%");
    /* 상위 바는 하위가 있는 «모든 깊이» 에서 % 텍스트를 내지 않는다 — 깊이 2의 t-3 도 마찬가지다. */
    expect(bar("t-1").querySelector(".scax-pj-gantt__bar-pct")).toBeNull();
    expect(bar("t-1").className).toContain("scax-pj-gantt__bar--parent");
    expect(bar("t-3").querySelector(".scax-pj-gantt__bar-pct")).toBeNull();
    expect(bar("t-3").className).toContain("scax-pj-gantt__bar--parent");
    /* 상위라도 fill 은 그린다 — % 를 «말하지» 않을 뿐이다. */
    expect(bar("t-3").querySelector(".scax-pj-gantt__bar-fill")).toBeTruthy();
    /* 체크리스트가 없는 업무는 fill 도 % 도 없다 — **0% 가 아니다** (D-02). */
    expect(bar("t-4").querySelector(".scax-pj-gantt__bar-fill")).toBeNull();
    expect(bar("t-4").querySelector(".scax-pj-gantt__bar-pct")).toBeNull();
    /* 외부 상태 done 은 체크리스트가 없어도 100% 다. */
    expect(bar("t-9").querySelector(".scax-pj-gantt__bar-pct")?.textContent).toBe("100%");
    /* 취소는 % 없음 + 제목 취소선. 간트에서 감추지 않는다 (D-23). */
    expect(bar("t-c").querySelector(".scax-pj-gantt__bar-pct")).toBeNull();
    expect(bar("t-c").className).toContain("scax-pj-gantt__bar--cancelled");
  });

  it("배지 라벨이 우리 어휘다 — 「대기」가 아니라 「시작 전」이고, blocked 는 「막힘 / danger」다", async () => {
    const { container } = renderPage();
    const blocked = await screen.findByText("막힌 일", { selector: ".scax-inbox-card__title" });
    const card = blocked.closest(".scax-inbox-card") as HTMLElement;
    /* 시안 목데이터의 「대기」·「지연」이 아니라 저장소 `taskStateLabel` 의 말이다. */
    expect(within(card).getByText("막힘").className).toContain("scax-badge--danger");
    const notStarted = within(screen.getByTestId("rail-left")).getByText("플레이스 등록").closest(".scax-inbox-card") as HTMLElement;
    expect(notStarted.textContent).toContain("시작 전");
    /* 간트 바도 같은 상태를 danger 규격으로 그린다. */
    expect(
      container.querySelector('.scax-pj-gantt__row[data-task-id="t-b"] .scax-pj-gantt__bar')?.className,
    ).toContain("scax-pj-gantt__bar--blocked");
  });

  it("선택 하나가 좌 레일 · 간트 · 우 레일을 함께 움직이고, 후행은 클라이언트가 뒤집어 만든다", async () => {
    renderPage();
    const railCard = await screen.findByText("계약 확정", { selector: ".scax-inbox-card__title" });
    fireEvent.click(railCard.closest(".scax-inbox-card") as HTMLElement);

    const card = railCard.closest(".scax-inbox-card") as HTMLElement;
    expect(card.className).toContain("scax-inbox-card--selected");

    const side = await screen.findByTestId("rail-right");
    const succeeding = side.querySelector('[data-relation="succeeding"]') as HTMLElement;
    /* 후행을 서버에 묻지 않는다 — t-3 의 `preceding_task_ids` 를 뒤집어 만든 것이다 (D-07). */
    expect(within(succeeding).getByText("시안 확정")).toBeTruthy();
    expect(within(succeeding).getByText("1")).toBeTruthy();
    /* 우 레일은 «선택된 하나» 에만 상세를 부른다 — 업무 수만큼 호출하지 않는다. */
    await waitFor(() => expect(vi.mocked(api.getTask).mock.calls.length).toBe(1));
    expect(vi.mocked(api.getTask).mock.calls[0][0]).toBe("t-9");
  });

  it("우 레일 미터는 체크리스트가 없으면 「—」다", async () => {
    renderPage();
    const railCard = await screen.findByText("썸네일 제작", { selector: ".scax-inbox-card__title" });
    fireEvent.click(railCard.closest(".scax-inbox-card") as HTMLElement);
    const side = await screen.findByTestId("rail-right");
    expect(side.querySelector(".scax-pj-side__pct")?.textContent).toBe("—");
    expect(side.querySelector(".scax-pj-side__fill")).toBeNull();
  });

  it("담당이 없으면 우 레일도 좌 레일과 «같은 방식» 으로 비운다 — 「—」도 「미정」도 없다", async () => {
    renderPage();
    const noOwner = await screen.findByText("썸네일 제작", { selector: ".scax-inbox-card__title" });
    fireEvent.click(noOwner.closest(".scax-inbox-card") as HTMLElement);
    const facts = (await screen.findByTestId("rail-right")).querySelector(".scax-pj-facts") as HTMLElement;
    /* 좌 레일이 meta 칸 자체를 안 만드는 것과 같다 — 두 자리가 한 방식이다 (I-1). */
    expect(within(facts).queryByText("담당")).toBeNull();
    expect(facts.textContent).not.toContain("—");
    expect(facts.textContent).not.toContain("미정");

    /* 담당이 있으면 그 칸이 선다 — 비우는 것은 «없을 때» 뿐이다. */
    fireEvent.click(
      (await screen.findByText("홈페이지 디자인 기획", { selector: ".scax-inbox-card__title" })).closest(
        ".scax-inbox-card",
      ) as HTMLElement,
    );
    const withOwner = (await screen.findByTestId("rail-right")).querySelector(".scax-pj-facts") as HTMLElement;
    expect(within(withOwner).getByText("담당")).toBeTruthy();
    expect(within(withOwner).getByText("지호")).toBeTruthy();
  });

  it("강조된 의존선은 «촉까지» 강조색이다 — 선만 accent 이고 촉이 회색으로 남지 않는다", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const link = () => container.querySelector(".scax-pj-gantt__link") as SVGPathElement;
    expect(link().getAttribute("marker-end")).toBe("url(#pj-arrow)");
    expect(link().classList.contains("scax-pj-gantt__link--on")).toBe(false);

    fireEvent.click(
      container.querySelector('.scax-pj-gantt__row[data-task-id="t-9"] .scax-pj-gantt__name') as HTMLElement,
    );
    expect(link().classList.contains("scax-pj-gantt__link--on")).toBe(true);
    /* 촉은 «자기 marker 의» 문맥에서 색을 받는다 — 강조용 marker 를 따로 가리켜야 색이 따라온다. */
    expect(link().getAttribute("marker-end")).toBe("url(#pj-arrow-on)");
    expect(container.querySelector("#pj-arrow-on .scax-pj-gantt__arrow--on")).toBeTruthy();
  });

  it("하위가 «전부 기간 없는» 업무면 죽은 twisty 를 세우지 않는다", async () => {
    renderPage("mina", {
      tasks: [
        task({ task_id: "solo", title: "혼자 선 상위", span_from: "2026-09-01", span_to: "2026-09-02" }),
        task({ task_id: "ghost-kid", title: "기간 없는 하위", parent_task_id: "solo" }),
      ],
    });
    const row = (await screen.findByText("혼자 선 상위", { selector: ".scax-pj-gantt__name-text" })).closest(
      ".scax-pj-gantt__row",
    ) as HTMLElement;
    /* 「하위 1」은 그대로 난다 — 하위가 있다는 사실은 참이다. 누를 것이 없을 뿐이다. */
    expect(row.textContent).toContain("하위 1");
    expect(row.querySelector(".scax-pj-gantt__twisty--blank")).toBeTruthy();
    expect(within(row).queryByRole("button", { name: "하위 업무 접기" })).toBeNull();
  });

  it("붙이고 뗄 수 있는지는 서버가 말한 대로 따른다 — 손잡이는 머리에 있다", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "프로젝트 관리" })).toBeTruthy();
    cleanup();

    // 그 프로젝트를 읽기만 하는 사람에게는 관리 손잡이가 아예 나타나지 않는다 — 눌러야 아는 거절을 만들지 않는다.
    renderPage("jiho", { may_manage: false });
    await screen.findByTestId("rail-left");
    /* ⚠ 머리가 «비는» 것이 아니다 — 「프로젝트 추가」는 게이트가 없어 누구에게나 선다(사용자 확정).
       사라지는 것은 **관리 손잡이 하나**다. */
    await waitFor(() => expect(screen.getByTestId("header-actions").textContent).toBe("프로젝트 추가"));
    expect(screen.queryByRole("button", { name: "프로젝트 관리" })).toBeNull();
  });

  it("붙어 있지 않은 사람에게는 업무가 왜 비어 있는지 말해 준다", async () => {
    renderPage("m-nobody", { tasks: [] });
    expect(await screen.findByText(/담당자에게만 보입니다/)).toBeTruthy();
  });

  it("관리 모달에서 담당자를 붙이면 그 프로젝트를 다시 읽는다", async () => {
    vi.mocked(api.assignToProject).mockResolvedValue({} as never);
    renderPage();
    const modal = await openManage();
    fireEvent.focus(within(modal).getByLabelText("붙일 구성원"));
    fireEvent.mouseDown(within(modal).getByRole("option", { name: "한별" }));
    fireEvent.click(within(modal).getByText("참여로 붙이기"));
    await waitFor(() => expect(api.assignToProject).toHaveBeenCalledWith("p-1", { member_id: "m-han", kind: "member" }));
  });

  it("관리 모달이 현재 참여자와 이력을 분리하고 선택 사유로 참여를 종료한다", async () => {
    vi.mocked(api.releaseFromProject).mockResolvedValue(undefined);
    renderPage();
    const modal = await openManage();

    const current = within(modal).getByRole("region", { name: "현재 참여자" });
    expect(within(current).getByText("지호")).toBeTruthy();
    const participationHistory = within(modal).getByRole("region", { name: "참여 이력" });
    expect(within(participationHistory).getByText("1차 지원 종료")).toBeTruthy();

    fireEvent.click(within(current).getByRole("button", { name: "지호 참여 종료" }));
    fireEvent.change(screen.getByLabelText("참여 종료 사유 (선택)"), { target: { value: "고객사 지원 종료" } });
    fireEvent.click(screen.getByRole("button", { name: "종료 기록" }));

    await waitFor(() => {
      expect(api.releaseFromProject).toHaveBeenCalledWith("p-1", "jiho", "current-jiho", "고객사 지원 종료");
    });
  });

  it("프로젝트를 바꾸면 이전 프로젝트의 선택과 열린 관리 모달을 함께 놓는다", async () => {
    vi.mocked(api.releaseFromProject).mockClear();
    const otherProject = { ...project, project_id: "p-2", name: "새 프로젝트" };
    vi.mocked(api.listProjects).mockResolvedValue([project, otherProject] as never);
    vi.mocked(api.getProject).mockImplementation(async (projectId) =>
      ({ ...detail, project_id: projectId, name: projectId === "p-1" ? project.name : otherProject.name }) as never,
    );
    vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    render(<Harness />);

    const modal = await openManage();
    fireEvent.click(within(modal).getByRole("button", { name: "지호 참여 종료" }));
    expect(screen.getByLabelText("참여 종료 사유 (선택)")).toBeTruthy();

    /* 프로젝트 전환은 «목록 행» 이 아니라 레일 헤더의 셀렉터가 한다 (D-05). */
    fireEvent.click(screen.getByRole("button", { name: "프로젝트 선택" }));
    fireEvent.click(await screen.findByRole("option", { name: "새 프로젝트" }));

    await waitFor(() => expect(screen.queryByLabelText("참여 종료 사유 (선택)")).toBeNull());
    expect(screen.queryByRole("dialog", { name: "프로젝트 관리" })).toBeNull();
    expect(api.releaseFromProject).not.toHaveBeenCalled();
  });

  it("읽을 수 있는 프로젝트가 0개면 화면 전체를 한 문장이 덮는다 — 네 칸이 각각 비지 않는다", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([] as never);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    render(<Harness />);
    expect(await screen.findByText("담당 프로젝트가 없습니다")).toBeTruthy();
    expect(screen.queryByTestId("rail-left")).toBeNull();
    expect(screen.queryByTestId("rail-right")).toBeNull();
    /* **레일이 «한 프레임도» 서지 않았다.** 목록을 모르는 동안 세웠다가 빈 상태로 덮으면
       「네 칸이 각각 비는」 순간이 실재한다 — 마지막 상태만 보면 그 프레임이 숨는다 (D-22). */
    expect(railLog.length).toBeGreaterThan(0);
    expect(railLog.every((rails) => !rails.left && !rails.right)).toBe(true);
  });

  /* ─────────────────────────── FE-5 ①  헤더: 「프로젝트 추가」와 「관리」 ─────────────────────────── */

  it("머리의 두 손잡이 — 관리는 «그 프로젝트의 권한», 추가는 게이트가 없다 (L-14 · L-16)", async () => {
    renderPage();
    const head = screen.getByTestId("header-actions");
    expect(await within(head).findByRole("button", { name: "프로젝트 관리" })).toBeTruthy();
    expect(within(head).getByRole("button", { name: "프로젝트 추가" })).toBeTruthy();
    /* 생성이 «오른쪽 끝» 이다 — 같은 슬롯 안에서 관리 뒤에 선다 (D-30 · D-25). */
    const order = Array.from(head.querySelectorAll("button")).map((button) => button.textContent);
    expect(order).toEqual(["프로젝트 관리", "프로젝트 추가"]);
    cleanup();

    /* **관리 권한이 없는 프로젝트** — 관리 버튼은 없고 생성 버튼은 있다. 두 판정이 갈린다 (L-16).
       ⚠ 자동 초대로 붙은 사람이 바로 이 자리다: 관계가 `참여` 라 `may_manage` 가 여전히 거짓이다. */
    renderPage("jiho", { may_manage: false });
    await screen.findByTestId("rail-left");
    expect(await screen.findByRole("button", { name: "프로젝트 추가" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "프로젝트 관리" })).toBeNull();
  });

  it("「프로젝트 추가」는 «모든 사람»에게 선다 — 역량 게이트가 없다 (L-17, 사용자 확정 2026-09-22)", async () => {
    /* ~~`project.manage` 가 있어야 뜬다~~ 를 뒤집은 자리다: **만드는 것은 모든 사람이 한다.**
       화면이 감출 판정을 갖지 않으므로 이 테스트는 **아무 역량도 안 싣고** 잰다 — 관리 권한도 없다. */
    renderPage("jiho", { may_manage: false });
    await screen.findByTestId("rail-left");
    const head = screen.getByTestId("header-actions");
    expect(await within(head).findByRole("button", { name: "프로젝트 추가" })).toBeTruthy();
    /* **「관리」의 게이트는 그대로다** — 두 판정이 섞이지 않았다. 머리에 선 버튼은 그 «하나»뿐이다. */
    expect(within(head).queryByRole("button", { name: "프로젝트 관리" })).toBeNull();
    expect(Array.from(head.querySelectorAll("button")).map((button) => button.textContent)).toEqual(["프로젝트 추가"]);

    /* 자격은 **서버가 판정한다** — 없는 사람이 보내면 거절이 오고 그 문구가 **모달 «안»** 에 선다.
       버튼을 감춰 「왜 없지」를 만드는 대신, 눌러서 «왜 안 되는지»를 읽게 한다. */
    vi.mocked(api.createProject).mockRejectedValue(new Error("프로젝트를 만들 권한이 없습니다"));
    const modal = await openCreate();
    fireEvent.change(within(modal).getByLabelText("이름"), { target: { value: "가을 캠페인" } });
    fireEvent.click(within(modal).getByRole("button", { name: "만들기" }));
    expect(await within(modal).findByText("프로젝트를 만들 권한이 없습니다")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "프로젝트 추가" })).toBeTruthy();
  });

  it("화면을 떠나면 그 버튼이 머리에서 사라진다 — 다른 탭에 남지 않는다 (L-18)", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([project] as never);
    vi.mocked(api.getProject).mockResolvedValue(detail as never);
    vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    const view = render(<Harness mounted />);
    expect(await screen.findByRole("button", { name: "프로젝트 추가" })).toBeTruthy();

    view.rerender(<Harness mounted={false} />);
    await waitFor(() => expect(screen.getByTestId("header-actions").textContent).toBe(""));
    expect(screen.queryByRole("button", { name: "프로젝트 추가" })).toBeNull();
    expect(screen.queryByRole("button", { name: "프로젝트 관리" })).toBeNull();
  });

  it("프로젝트가 0개여도 생성 버튼이 선다 — 본문은 한 문장으로 덮여 있는데 그 버튼은 있다 (L-15)", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([] as never);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    render(<Harness />);
    expect(await screen.findByText("담당 프로젝트가 없습니다")).toBeTruthy();
    /* 덮개는 **본문 네 칸**의 것이고 머리의 생성 손잡이는 그 밖이다 (D-22 를 깨지 않는다). */
    expect(screen.queryByTestId("rail-left")).toBeNull();
    const add = screen.getByRole("button", { name: "프로젝트 추가" });
    /* 그리고 실제로 **열린다** — 첫 프로젝트를 만들 길이 여기서 끊기면 버튼이 있어도 소용없다. */
    fireEvent.click(add);
    expect(await screen.findByRole("dialog", { name: "프로젝트 추가" })).toBeTruthy();
  });

  /* ─────────────────────────── FE-5 ①  생성 전용 모달 ─────────────────────────── */

  async function openCreate() {
    fireEvent.click(await screen.findByRole("button", { name: "프로젝트 추가" }));
    return screen.findByRole("dialog", { name: "프로젝트 추가" });
  }

  it("모달의 칸이 넷이고 external_key 칸이 없다 — 공백만으로는 만들 수 없다 (L-19 · L-20)", async () => {
    renderPage();
    const modal = await openCreate();

    expect(within(modal).getByLabelText("이름")).toBeTruthy();
    expect(within(modal).getByLabelText("설명")).toBeTruthy();
    expect(within(modal).getByLabelText("시작일")).toBeTruthy();
    expect(within(modal).getByLabelText("종료일")).toBeTruthy();
    /* **다섯째 칸이 없다.** 표면은 그 값을 받지만 화면이 «안 내기로» 했다 (§2.10 · OQ-607) —
       칸을 더 만들면 모르는 필드가 아니라 «중복»에서 터진다. 재는 것은 칸의 수 자체다. */
    expect(modal.querySelectorAll(".scax-field").length).toBe(4);
    expect(within(modal).queryByLabelText(/key/i)).toBeNull();

    /* 공백만 넣으면 **보낼 수 없다** — 눌러 보고 서버에게 거절당하는 자리를 만들지 않는다. */
    fireEvent.change(within(modal).getByLabelText("이름"), { target: { value: "   " } });
    const submit = within(modal).getByRole("button", { name: "만들기" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    fireEvent.click(submit);
    expect(api.createProject).not.toHaveBeenCalled();
  });

  it("만들면 셀렉터에 그 프로젝트가 뜬다 — 만든 사람이 리드로 붙는다 (L-22)", async () => {
    const made = { ...project, project_id: "p-new", name: "가을 캠페인" };
    vi.mocked(api.createProject).mockResolvedValue(made as never);
    renderPage();
    const modal = await openCreate();

    fireEvent.change(within(modal).getByLabelText("이름"), { target: { value: "  가을 캠페인  " } });
    fireEvent.change(within(modal).getByLabelText("설명"), { target: { value: "9~11월" } });
    /* 다시 읽고 나면 그 프로젝트가 **목록에도** 있어야 한다 — 셀렉터가 목록에서 나온다. */
    vi.mocked(api.listProjects).mockResolvedValue([project, made] as never);
    vi.mocked(api.getProject).mockResolvedValue({ ...detail, ...made } as never);
    fireEvent.click(within(modal).getByRole("button", { name: "만들기" }));

    /* 빈 칸은 **키째 안 보낸다** — 「비움」과 「안 건드림」을 같은 말로 보내지 않는다. */
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith({ name: "가을 캠페인", description: "9~11월" }));
    /* 성공했으니 닫힌다. 그리고 **만든 그 프로젝트**를 연다. */
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "프로젝트 추가" })).toBeNull());
    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith("p-new"));
    fireEvent.click(screen.getByRole("button", { name: "프로젝트 선택" }));
    expect(await screen.findByRole("option", { name: "가을 캠페인" })).toBeTruthy();
  });

  it("기간이 역전되면 서버의 거절을 «쓰던 자리에서» 말하고 모달이 안 닫힌다 (L-21)", async () => {
    vi.mocked(api.createProject).mockRejectedValue(new Error("끝나는 날이 시작하는 날보다 앞설 수 없습니다"));
    renderPage();
    const modal = await openCreate();
    fireEvent.change(within(modal).getByLabelText("이름"), { target: { value: "뒤집힌 기간" } });
    fireEvent.click(within(modal).getByRole("button", { name: "만들기" }));

    expect(await within(modal).findByText("끝나는 날이 시작하는 날보다 앞설 수 없습니다")).toBeTruthy();
    /* 닫히지 않는다 — 쓰던 값을 **고쳐 다시 보낼 수 있어야** 한다. 기간 판정을 화면이 다시 하지 않는다. */
    expect(screen.getByRole("dialog", { name: "프로젝트 추가" })).toBeTruthy();
    expect(within(modal).getByLabelText("이름")).toHaveProperty("value", "뒤집힌 기간");
  });

  it("루프2 동안 생성 경로가 둘이다 — 관리 모달의 「새 프로젝트」 폼을 이 판이 떼지 않았다 (D-30)", async () => {
    renderPage();
    const modal = await openManage();
    /* 루프3 이 뗀다. **조용한 중복이 아니라 감수하는 중복**이라 검사가 그 사실을 들고 있는다. */
    expect(within(modal).getByRole("region", { name: "새 프로젝트" })).toBeTruthy();
    expect(within(modal).getByRole("button", { name: "열기" })).toBeTruthy();
  });

  /* ─────────────────────────── FE-5 ②  좌 레일 머리 한 줄 ─────────────────────────── */

  it("좌 레일 머리가 한 줄이다 — 아이콘 + 「프로젝트」 + 건수, 오른쪽 끝에 셀렉터 (L-34 · L-35)", async () => {
    renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-inbox-card__title" });
    const rail = screen.getByTestId("rail-left");
    const head = rail.querySelector(".scax-gutter-list__header") as HTMLElement;
    /* 두 줄로 세우던 modifier 가 **없다** — 레일 머리의 기본(row · 32px)이 그대로 이 배치다. */
    expect(head.className).not.toContain("scax-gutter-list__header--sub");
    /* 제목 줄과 셀렉터가 **같은 한 줄 안**에 있다. */
    expect(within(head).getByRole("heading", { name: /프로젝트/ })).toBeTruthy();
    expect(within(head).getByRole("button", { name: "프로젝트 선택" })).toBeTruthy();
    /* 시안의 「업무」가 아니라 **「프로젝트」** 다 (D-34, 사용자 지시가 시안보다 우선한다). */
    expect(head.querySelector(".scax-gutter-list__title")?.textContent).toContain("프로젝트");
    expect(head.querySelector(".scax-gutter-list__title")?.textContent).not.toContain("업무");
    /* 배지는 **좌 레일 카드 수**다 — 요약 스트립의 「전체 업무」(취소를 뺀 8)보다 크다. */
    expect(head.querySelector(".scax-badge--count")?.textContent).toBe("9");
    expect(rail.querySelectorAll(".scax-inbox-card").length).toBe(9);
  });

  /** 셀렉터 하나만 세운다 — 이름의 길이가 이 판의 관측 대상이다. */
  function renderWithProjectNamed(name: string) {
    vi.mocked(api.listProjects).mockResolvedValue([{ ...project, name }] as never);
    vi.mocked(api.getProject).mockResolvedValue({ ...detail, name } as never);
    vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    vi.mocked(api.getTask).mockResolvedValue(taskDetail({ task_id: "t-1", title: "한빛 9월 통합 마케팅" }) as never);
    return render(<Harness />);
  }

  it("셀렉터가 프로젝트 이름을 «제 요소 안에» 든다 — 아주 긴 이름도 아주 짧은 이름도 (사용자 지시 A-1)", async () => {
    /* 화면에서 「하반기 제**풀**」처럼 **글자 중간에서** 잘렸다. 트리거는 flex 라 벌거벗은 글자가
       익명 item 이 되고, 그 위에 얹은 `text-overflow` 가 닿지 않는다 — 이름은 제 요소를 가져야 한다. */
    const long = "2026년 하반기 제품 통합 마케팅 캠페인 · 브랜드 리뉴얼과 채널 확장";
    renderWithProjectNamed(long);
    const trigger = await screen.findByRole("button", { name: "프로젝트 선택" });
    const name = trigger.querySelector(".scax-pj-rail__project-name") as HTMLElement;
    expect(name).toBeTruthy();
    /* 이름이 **끝까지** 실린다 — 잘라서 넣지 않는다. 화면에서 줄이는 것은 CSS 의 말줄임표다. */
    expect(name.textContent).toBe(long);
    /* 트리거의 직접 자식에 **벌거벗은 글자 마디가 없다** — 있으면 말줄임표가 다시 안 걸린다. */
    expect(
      Array.from(trigger.childNodes).some((node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== ""),
    ).toBe(false);
    /* 머리는 **여전히 한 줄**이다 — 이 판이 늘린 것은 폭이지 높이가 아니다. */
    const head = screen.getByTestId("rail-left").querySelector(".scax-gutter-list__header") as HTMLElement;
    expect(head.className).not.toContain("scax-gutter-list__header--sub");
    expect(head.querySelector(".scax-pj-rail__title")).toBeTruthy();
    cleanup();

    /* **아주 짧은 이름** — 같은 자리에 그대로 든다. 폭을 다 쓰는 것과 글자를 늘리는 것은 다르다. */
    renderWithProjectNamed("9월");
    const short = await screen.findByRole("button", { name: "프로젝트 선택" });
    expect((short.querySelector(".scax-pj-rail__project-name") as HTMLElement).textContent).toBe("9월");
  });

  it("남는 폭을 «이름» 이 가져간다 — 제목은 안 줄고 셀렉터에 폭 상한이 없다 (사용자 지시 A-1)", async () => {
    /* jsdom 은 레이아웃을 계산하지 않는다 — 규칙 자체를 읽어 잰다(선례 `ds/HoverContrast.test.tsx:22`).
       재는 것은 셋이다: **제목이 안 줄고 · 셀렉터에 상한이 없고 · 이름에 말줄임표가 걸린다.** */
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다 (선례 `ds/HoverContrast.test.tsx:21`).
    const { readFileSync } = await import("node:fs");
    const css: string = (readFileSync("src/styles/projects.css", "utf8") as string)
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\s+/g, " ")
      .replace(/\s*([{};:,])\s*/g, "$1")
      .trim();
    const rule = (selector: string): string => {
      const block = css.split("}").find((chunk) => chunk.split("{")[0].split(",").includes(selector));
      expect(block).toBeTruthy();
      return (block as string).split("{")[1];
    };
    /* **제목이 남는 폭을 먹어 셀렉터를 오른쪽 끝으로 민다** (사용자 지시 2026-09-22:
       「아이콘 + 프로젝트 ~~~ 셀렉터」). 늘어나는 것은 제목 «내용» 이 아니라 그 뒤의 빈 자리다 —
       `.scax-gutter-list__header` 가 공유라 `justify-content` 를 못 건드려서 이 방향으로 풀었다.
       ~~`flex:0 0 auto`~~ 였던 것을 뒤집은 자리다: 그때는 셀렉터가 제목에 딱 붙었다. */
    expect(rule(".scax-pj-rail__title")).toContain("flex:1 1 auto");
    /* 셀렉터는 **자기 내용만큼만** 차지하고 자리가 모자랄 때만 줄어든다.
       `flex:1 1 auto` 로 두면 남는 폭을 먹어 **제목에 붙는다**(그것이 위에서 뒤집힌 짝이다).
       **`max-width` 는 여전히 없다** — 60% 가 글자를 잘랐던 자리다. */
    expect(rule(".scax-pj-rail__project")).toContain("flex:0 1 auto");
    expect(rule(".scax-pj-rail__project")).not.toContain("max-width");
    /* **오늘은 «선» 이 아니라 «칸» 이다** (사용자 지시 2026-09-22). 1px 선이면 머리줄의 날짜와
       아래 칸을 눈으로 세어 맞춰야 했다. 폭은 `GANTT.day` 에서 인라인으로 온다 —
       숫자를 CSS 에 박으면 하루 폭이 두 자리에 살게 된다(격자가 `backgroundSize` 로 하는 것과 같다). */
    expect(rule(".scax-pj-gantt__now")).not.toContain("width");

    /* 정말 긴 이름은 **말줄임표로** 끝난다 — 규칙이 «이름 요소» 에 걸려 있다. */
    expect(rule(".scax-pj-rail__project-name")).toContain("text-overflow:ellipsis");
    expect(rule(".scax-pj-rail__project-name")).toContain("overflow:hidden");
    /* **32px 머리를 안 넘는다** — 트리거 높이(26px, `components.css:45`)를 이 파일이 안 건드린다. */
    expect(rule(".scax-pj-rail__project")).not.toContain("height");
    expect(rule(".scax-pj-rail__project-name")).not.toContain("height");
  });

  /* ─────────────────────────── FE-5 ③  우 레일: 블록 순서와 업무 정보 ─────────────────────────── */

  it("블록 순서가 넷이고 「업무 정보」가 설명과 체크리스트를 «함께» 든다 (L-24 · L-25)", async () => {
    renderPage("mina", {}, {
      task: {
        task_id: "t-5",
        title: "플레이스 등록",
        description: "시안 세 벌을 만들고 고른다",
        checklist: [
          { item_id: "c-1", text: "레퍼런스 모으기", position: 1, done: true },
          { item_id: "c-2", text: "시안 세 벌", position: 2, done: false },
        ],
      },
    });
    const side = await pick("플레이스 등록");

    await waitFor(() => expect(within(side).getByText("시안 세 벌을 만들고 고른다")).toBeTruthy());
    const titles = Array.from(side.querySelectorAll(".scax-pj-side__block-title")).map((node) => node.textContent);
    expect(titles).toEqual(["메타 정보", "업무 정보", "관계", "하위 업무"]);

    /* 체크리스트가 **「관계」 아래에 홀로 서 있지 않다** — 설명과 같은 블록 안이다. */
    const info = within(side).getByText("업무 정보").closest(".scax-pj-side__block") as HTMLElement;
    expect(within(info).getByText("시안 세 벌을 만들고 고른다")).toBeTruthy();
    expect(within(info).getByText("레퍼런스 모으기")).toBeTruthy();
    expect(within(info).getByText("시안 세 벌")).toBeTruthy();
    /* 집계의 재료는 프로젝트 상세 `tasks[]` 의 것이다 — t-5 는 1/4 이고 상세의 항목 수(2)가 아니다. */
    expect(info.textContent).toContain("1/4");
  });

  it("설명이 없는 업무는 그 자리를 비운다 — 지어낸 문구가 없다 (L-26)", async () => {
    renderPage("mina", {}, { task: { task_id: "t-5", title: "플레이스 등록" } });
    const side = await pick("플레이스 등록");
    const info = within(side).getByText("업무 정보").closest(".scax-pj-side__block") as HTMLElement;
    await waitFor(() => expect(info.querySelector(".scax-pj-side__none")).toBeTruthy());
    /* 설명 요소 **자체가 없다.** 「없음」도 「—」도 「설명이 없습니다」도 만들지 않는다. */
    expect(info.querySelector(".scax-pj-side__desc")).toBeNull();
    expect(info.textContent).not.toContain("—");
    expect(info.textContent).not.toContain("없음");
  });

  it("접근 값이 read_only 인데 항목이 온 응답을 «정상으로» 그린다 — 감추는 자리가 0건이다 (L-10)", async () => {
    /* BE-3 실측: 같은 프로젝트의 «남의» 업무는 `access: "read_only"` 이면서 `checklist` 가 실린다.
       그 조합이 정상이다 — 접근 값은 이제 **쓰기 범위**만 뜻한다 (D-29). */
    renderPage("mina", {}, {
      task: {
        task_id: "t-5",
        title: "플레이스 등록",
        access: "read_only",
        description: "남의 업무인데 내용이 온다",
        checklist: [{ item_id: "c-1", text: "레퍼런스 모으기", position: 1, done: true }],
      },
    });
    const side = await pick("플레이스 등록");
    await waitFor(() => expect(within(side).getByText("레퍼런스 모으기")).toBeTruthy());
    /* **설명도 온다** — 「읽기 전용이면 설명이 없겠지」는 틀리다 (L-13). */
    expect(within(side).getByText("남의 업무인데 내용이 온다")).toBeTruthy();
    /* 감췄다고 말하는 문구가 **나오지 않는다.** */
    expect(side.textContent).not.toContain("참여한 사람에게 보입니다");
  });

  it("프로젝트 «밖»에서 읽으면 항목 키 자체가 없다 — 터지지 않고 집계와 미터만 그린다 (L-12)", async () => {
    /* ⚠ 안 실릴 때는 `null` 도 `[]` 도 아니라 **키 자체가 없다.** `detail.checklist.length` 로 읽으면
       이 갈래에서 터진다 — 그래서 이 테스트의 상세에는 `checklist` 키를 «넣지 않는다». */
    renderPage("mina", {}, {
      task: { task_id: "t-5", title: "플레이스 등록", access: "read_only", description: "설명은 온다" },
    });
    const side = await pick("플레이스 등록");
    await waitFor(() => expect(within(side).getByText(/참여한 사람에게 보입니다/)).toBeTruthy());
    /* 미터는 **프로젝트 상세 `tasks[]` 의 집계**로 그린다 — 상세가 집계를 안 실어도 비지 않는다. */
    expect(side.querySelector(".scax-pj-side__pct")?.textContent).toBe("25%");
    expect(side.textContent).toContain("1/4");
    expect(within(side).getByText("설명은 온다")).toBeTruthy();
  });

  it("체크리스트가 읽기 전용이다 — 이 화면에서 항목을 끄고 켤 수 없다", async () => {
    /* 쓰기 네 표면은 담당이 아니면 **403 이 아니라 404** 다. 눌러 보고 아는 자리를 만들지 않는다. */
    renderPage("mina", {}, {
      canManageOwnTasks: true,
      task: {
        task_id: "t-5",
        title: "플레이스 등록",
        access: "owner",
        checklist: [{ item_id: "c-1", text: "레퍼런스 모으기", position: 1, done: false }],
      },
    });
    const side = await pick("플레이스 등록");
    await waitFor(() => expect(within(side).getByText("레퍼런스 모으기")).toBeTruthy());
    const list = side.querySelector(".scax-pj-check") as HTMLElement;
    /* **담당(`owner`)이고 역량도 있는데도** 체크박스가 없다 — 「권한이 있으면 켜진다」가 아니다. */
    expect(list.querySelectorAll("input, button, [role=checkbox]").length).toBe(0);
  });

  it("우 레일이 선택된 하나에만 상세를 부르고, 상세가 «오기 전»에는 내용도 항목도 없다 (L-33)", async () => {
    renderPage();
    /* 상세를 **영원히 pending** 으로 둔다. `tasks[]` 가 설명·항목을 싣고 있었다면 이 프레임에
       이미 그려졌을 것이다 — **화면이 그린 것**으로 재고 픽스처의 모양을 되묻지 않는다 (D-38). */
    let land: (row: Record<string, unknown>) => void = () => undefined;
    vi.mocked(api.getTask).mockImplementation(
      () => new Promise((resolve) => {
        land = (row) => resolve(row as never);
      }),
    );
    const side = await pick("플레이스 등록");
    await waitFor(() => expect(vi.mocked(api.getTask).mock.calls.length).toBe(1));
    expect(vi.mocked(api.getTask).mock.calls[0][0]).toBe("t-5");

    const info = () => within(side).getByText("업무 정보").closest(".scax-pj-side__block") as HTMLElement;
    await waitFor(() => expect(info().querySelector(".scax-skeleton")).toBeTruthy());
    expect(info().querySelector(".scax-pj-side__desc")).toBeNull();
    expect(info().querySelector(".scax-pj-check")).toBeNull();
    /* **집계만은 이미 있다** — 그것 하나를 `tasks[]` 가 싣는다(t-5 는 1/4).
       「아직 안 왔다」와 「없다」가 다르다는 것이 이 한 줄이다. */
    expect(info().textContent).toContain("1/4");

    /* 상세가 «오면» 그때 둘이 선다 — 재료가 상세라는 사실이 두 프레임의 차이로 관측된다. */
    land(
      taskDetail({
        task_id: "t-5",
        title: "플레이스 등록",
        description: "시안 세 벌을 만들고 고른다",
        checklist: [{ item_id: "c-1", text: "레퍼런스 모으기", position: 1, done: false }],
      }),
    );
    await waitFor(() => expect(within(side).getByText("시안 세 벌을 만들고 고른다")).toBeTruthy());
    expect(within(side).getByText("레퍼런스 모으기")).toBeTruthy();
  });

  /* ─────────────────────────── FE-5 ④⑤  읽히게 ─────────────────────────── */

  it("누를 수 있는 두 자리가 «같은 부품»이다 — 메타의 상위 업무와 관계 줄, 그리고 눌리는 동작이 남는다 (L-40 · L-41)", async () => {
    renderPage();
    const side = await pick("시안 확정");

    /* 테두리·배경 규칙이 `.scax-pj-rel__item` 하나에 걸린다 — **두 자리가 그 클래스를 함께 쓸 때만**
       「두 자리 모두」가 성립한다. 한쪽이 다른 클래스로 갈라지면 그 자리는 hover 없이 안 보이게 된다. */
    const facts = side.querySelector(".scax-pj-facts") as HTMLElement;
    const parentButton = facts.querySelector("button.scax-pj-rel__item") as HTMLElement;
    expect(parentButton.textContent).toContain("홈페이지 디자인 기획");
    const preceding = side.querySelector('[data-relation="preceding"]') as HTMLElement;
    expect(preceding.querySelector("button.scax-pj-rel__item")).toBeTruthy();

    /* **누르면 여전히 그 업무가 선택된다** — 꼴만 바뀌었고 동작은 그대로다 (L-41). */
    fireEvent.click(parentButton);
    await waitFor(() =>
      expect(screen.getByTestId("rail-right").querySelector(".scax-pj-side__title")?.textContent).toBe("홈페이지 디자인 기획"),
    );
    fireEvent.click(
      (screen.getByTestId("rail-right").querySelector('[data-relation="preceding"] button.scax-pj-rel__item') ??
        parentButton) as HTMLElement,
    );
    await waitFor(() =>
      expect(screen.getByTestId("rail-right").querySelector(".scax-pj-side__title")?.textContent).toBe("한빛 9월 통합 마케팅"),
    );
  });

  /* ─────────────────────────── FE-5 ⑥  상태 드롭다운 — 관문 열 개 ─────────────────────────── */

  /** 내 업무 하나. 회차는 **상세의 것**이고 `tasks[]` 에는 아예 없는 값이다. */
  const mine: Record<string, unknown> = { task_id: "t-5", title: "플레이스 등록", state: "in_progress", version: 7, access: "owner" };

  async function openState(session: { canManageOwnTasks?: boolean } = { canManageOwnTasks: true }, detailRow = mine) {
    vi.mocked(api.transitionDirectTask).mockReset();
    vi.mocked(api.transitionDirectTask).mockResolvedValue(undefined as never);
    renderPage("mina", {}, { ...session, task: detailRow });
    const side = await pick("플레이스 등록");
    await waitFor(() => expect(vi.mocked(api.getTask).mock.calls.length).toBeGreaterThan(0));
    return side;
  }

  it("내 업무면 상태 칸이 드롭다운이고, 아니면 읽기 배지다 (L-27 · L-28 · L-29)", async () => {
    const side = await openState();
    await waitFor(() => expect(within(side).getByRole("button", { name: "플레이스 등록 상태" })).toBeTruthy());
    cleanup();

    /* 남의 업무 — 같은 프로젝트라 **항목은 보이는데** 상태는 배지다. 접근 값이 가르는 자리다. */
    const readOnly = await openState({ canManageOwnTasks: true }, { ...mine, access: "read_only" });
    await waitFor(() => expect(readOnly.querySelector(".scax-pj-facts .scax-badge")).toBeTruthy());
    expect(within(readOnly).queryByRole("button", { name: "플레이스 등록 상태" })).toBeNull();
    cleanup();

    /* **수락 전 배정** — 서버가 접근 값을 이미 `read_only` 로 낸다. 화면이 담당을 다시 추론하지 않는다. */
    const pending = await openState(
      { canManageOwnTasks: true },
      { ...mine, access: "read_only", derived: { assignment: "awaiting_acceptance" } },
    );
    await waitFor(() => expect(pending.querySelector(".scax-pj-facts .scax-badge")).toBeTruthy());
    expect(within(pending).queryByRole("button", { name: "플레이스 등록 상태" })).toBeNull();
  });

  it("게이트가 둘이다 — 역량이 없으면 활성 담당이라도 드롭다운이 안 선다 (L-50)", async () => {
    /* 접근 값은 `owner` 인데 **세션 역량이 없다.** 하나만 보면 여기서 드롭다운이 서고
       고를 때마다 서버가 거절한다. 관측은 **역량을 뺀 세션 값**으로 한다. */
    const side = await openState({ canManageOwnTasks: false });
    await waitFor(() => expect(side.querySelector(".scax-pj-facts .scax-badge")).toBeTruthy());
    expect(within(side).queryByRole("button", { name: "플레이스 등록 상태" })).toBeNull();
    /* 배지가 내는 값은 **프로젝트 상세 `tasks[]` 의 상태**다 — 업무 상세가 아직 안 와도 서야 하므로
       그 줄을 정본으로 쓴다(t-5 는 `open`). 「드롭다운이 안 선다」와 「값이 비어 있다」는 다른 말이다. */
    expect(side.querySelector(".scax-pj-facts .scax-badge")?.textContent).toBe("시작 전");
  });

  it("갈 곳이 없으면 배지다 — 취소와 «승인 대기 중인 완료» 둘 다 (L-51)", async () => {
    const cancelled = await openState({ canManageOwnTasks: true }, { ...mine, state: "cancelled" });
    await waitFor(() => expect(cancelled.querySelector(".scax-pj-facts .scax-badge")).toBeTruthy());
    expect(within(cancelled).queryByRole("button", { name: "플레이스 등록 상태" })).toBeNull();
    cleanup();

    /* `done` 이라고 다 같지 않다 — **승인을 기다리는 done** 은 내가 되돌릴 자리가 아니다.
       업무 화면의 `allowedTaskTransitions` 와 **같은 판정**이고, 이 화면이 다시 짜지 않았다. */
    const awaiting = await openState(
      { canManageOwnTasks: true },
      { ...mine, state: "done", derived: { approval: "awaiting_review" } },
    );
    await waitFor(() => expect(awaiting.querySelector(".scax-pj-facts .scax-badge")).toBeTruthy());
    expect(within(awaiting).queryByRole("button", { name: "플레이스 등록 상태" })).toBeNull();
    cleanup();

    /* 대비군 — 승인 축이 없는 `done` 은 재개가 말이 되므로 **드롭다운이 선다.** 이 줄이 없으면
       위 둘은 「`done` 이면 늘 배지」로도 통과한다. */
    const settled = await openState({ canManageOwnTasks: true }, { ...mine, state: "done" });
    await waitFor(() => expect(within(settled).getByRole("button", { name: "플레이스 등록 상태" })).toBeTruthy());
  });

  it("지금 상태가 목록에 있고, 다시 골라도 아무 일도 일어나지 않는다 (L-30)", async () => {
    const side = await openState();
    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    /* 무엇이 골라져 있는지 보이려고 지금 상태도 목록에 둔다. */
    expect(await screen.findByRole("option", { name: "진행 중" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "진행 중" }));
    await waitFor(() => expect(screen.queryByRole("option", { name: "진행 중" })).toBeNull());
    expect(api.transitionDirectTask).not.toHaveBeenCalled();
  });

  it("「막힘」은 사유 입력을 띄우고, 사유 없이 전이가 나가지 않는다 (L-49)", async () => {
    const side = await openState();
    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "막힘" }));

    /* **드롭다운에서 바로 보내지 않는다.** 업무 화면의 `BlockReasonPrompt` 가 그대로 뜬다. */
    expect(await screen.findByText("막힘 사유를 남겨 주세요")).toBeTruthy();
    expect(api.transitionDirectTask).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("textbox", { name: "막힘 사유" }), { target: { value: "디자이너 일정이 밀렸다" } });
    fireEvent.click(screen.getByRole("button", { name: "막힘 처리" }));
    /* 회차는 **업무 상세로 읽은 7** 이다 — `tasks[]` 행에는 그 값이 아예 없다 (한 걸음 뒤지면 409). */
    await waitFor(() =>
      expect(api.transitionDirectTask).toHaveBeenCalledWith("t-5", "block", 7, "디자이너 일정이 밀렸다"),
    );
  });

  it("요청 업무의 「완료」는 드롭다운이 직접 보내지 않는다 — 완료 보고 모달로 간다 (L-31)", async () => {
    vi.mocked(api.getTaskMaterials).mockResolvedValue([] as never);
    const side = await openState({ canManageOwnTasks: true }, { ...mine, origin: { kind: "work_request" } });
    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));

    expect(await screen.findByRole("dialog", { name: /완료 보고/ })).toBeTruthy();
    /* 서버가 늘 거절하는 전이라(「요청자의 확인이 필요합니다」) 사용자가 그 문구를 보는 자리가 없다. */
    expect(api.transitionDirectTask).not.toHaveBeenCalled();
  });

  it("요청이 «아닌» 업무의 완료는 그대로 전이다 — 모달이 끼어들지 않는다", async () => {
    const side = await openState();
    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));
    await waitFor(() => expect(api.transitionDirectTask).toHaveBeenCalledWith("t-5", "complete", 7, undefined));
    expect(screen.queryByRole("dialog", { name: /완료 보고/ })).toBeNull();
  });

  it("바꾸고 나면 좌 레일 카드와 간트 바까지 새 상태다 — 낙관적 갱신 0건 (L-52)", async () => {
    const side = await openState();
    const card = () =>
      (screen.getByTestId("rail-left").querySelector('.scax-inbox-card[data-task-id="t-5"]') as HTMLElement).textContent;
    expect(card()).toContain("시작 전");

    /* 다음에 읽힐 프로젝트 상세를 새 상태로 갈아 둔다 — 화면이 **서버에서 온 값으로만** 바뀐다. */
    const moved = tasks.map((row) => (row.task_id === "t-5" ? { ...row, state: "done" as const } : row));
    vi.mocked(api.getProject).mockResolvedValue({ ...detail, tasks: moved } as never);
    vi.mocked(api.getTask).mockResolvedValue(taskDetail({ ...mine, state: "done" }) as never);

    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));

    await waitFor(() => expect(card()).toContain("완료"));
    /* 프로젝트 상세를 **다시 읽었다** — 우 레일만 고치면 좌 레일·간트가 옛 상태로 남는다. */
    expect(vi.mocked(api.getProject).mock.calls.length).toBeGreaterThan(1);
    expect(
      document.querySelector('.scax-pj-gantt__row[data-task-id="t-5"] .scax-pj-gantt__bar')?.className,
    ).toContain("scax-pj-gantt__bar--done");
    /* 업무 상세도 다시 읽는다 — 다음 전이가 **새 회차**로 나가야 한다. */
    expect(vi.mocked(api.getTask).mock.calls.length).toBeGreaterThan(1);
  });

  it("거절되면 상태가 그대로이고 오류만 뜬다 — 화면이 미리 바꾸지 않는다 (L-52 뒷절반)", async () => {
    const side = await openState();
    vi.mocked(api.transitionDirectTask).mockRejectedValue(new Error("선행 업무가 끝나지 않았습니다"));
    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));

    await waitFor(() => expect(noop).toHaveBeenCalledWith("선행 업무가 끝나지 않았습니다"));
    /* 프로젝트 상세를 **다시 읽지 않았다** — 실패한 쓰기로 화면을 흔들지 않는다. */
    expect(vi.mocked(api.getProject).mock.calls.length).toBe(1);
    expect(
      screen.getByTestId("rail-left").querySelector('.scax-inbox-card[data-task-id="t-5"]')?.textContent,
    ).toContain("시작 전");
  });

  it("보내는 중에는 트리거가 잠긴다 — 두 번 눌러도 두 번 나가지 않는다 (관문 아)", async () => {
    const side = await openState();
    /* **안 끝나는 전이** 하나로 「보내는 중」 프레임을 붙든다 — 경주가 없다.
       (앞 판이 「흔들리는 테스트가 된다」고 적었지만, `waitFor` 가 등록 왕복을 그대로 흡수한다.) */
    let settle: () => void = () => undefined;
    vi.mocked(api.transitionDirectTask).mockImplementation(
      () => new Promise((resolve) => {
        settle = () => resolve(undefined as never);
      }),
    );
    const trigger = await within(side).findByRole("button", { name: "플레이스 등록 상태" });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));

    await waitFor(() => expect(trigger.hasAttribute("disabled")).toBe(true));
    /* 잠긴 동안 **한 번만** 나갔다 — 두 번째 누름이 두 번째 전이가 되지 않는다. */
    fireEvent.click(trigger);
    expect(vi.mocked(api.transitionDirectTask).mock.calls.length).toBe(1);

    /* 끝나면 **도로 열린다** — 잠금이 「보내는 동안」이지 영구가 아니다. */
    settle();
    await waitFor(() =>
      expect(
        (within(screen.getByTestId("rail-right")).getByRole("button", { name: "플레이스 등록 상태" }) as HTMLElement)
          .hasAttribute("disabled"),
      ).toBe(false),
    );
  });

  /** 요청 업무의 완료 보고 모달을 열고 결과 요약까지 채운다 — 거절/성공 둘이 같은 자리에서 갈린다. */
  async function openReport() {
    vi.mocked(api.getTaskMaterials).mockResolvedValue([] as never);
    const side = await openState({ canManageOwnTasks: true }, { ...mine, origin: { kind: "work_request" } });
    fireEvent.click(await within(side).findByRole("button", { name: "플레이스 등록 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));
    const modal = await screen.findByRole("dialog", { name: /완료 보고/ });
    fireEvent.change(within(modal).getByLabelText("결과 요약"), { target: { value: "시안 세 벌을 넘겼다" } });
    return modal;
  }

  it("완료 보고가 거절되면 «화면이 말한다» — 오류 통로를 막지 않는다 (검수 FAIL-1)", async () => {
    /* 이 모달은 **자기 안에 오류를 그리지 않는다** — `onError` 가 유일한 통로다. 빈 함수로 받으면
       사람은 버튼을 눌렀는데 아무 일도 없는 화면을 본다. 완료 거절은 갈래가 **넷**이라 자주 지난다. */
    vi.mocked(api.submitTaskCompletion).mockRejectedValue(new Error("끝나지 않은 선행 업무가 있습니다"));
    const modal = await openReport();
    fireEvent.click(within(modal).getByRole("button", { name: "보고 보내기" }));

    expect((await screen.findByTestId("error-banner")).textContent).toBe("끝나지 않은 선행 업무가 있습니다");
    /* 모달은 **열린 채**다 — 쓴 문장을 지우지 않고 고쳐 다시 보낼 수 있다. */
    expect(screen.getByRole("dialog", { name: /완료 보고/ })).toBeTruthy();
    expect(within(modal).getByLabelText("결과 요약")).toHaveProperty("value", "시안 세 벌을 넘겼다");
  });

  it("완료 보고가 올라가면 확인 문구가 뜨고 프로젝트를 «다시 읽는다» (③-차의 보고 쪽 절반)", async () => {
    vi.mocked(api.submitTaskCompletion).mockResolvedValue(taskDetail({ ...mine, state: "done" }) as never);
    const modal = await openReport();
    const before = vi.mocked(api.getProject).mock.calls.length;
    fireEvent.click(within(modal).getByRole("button", { name: "보고 보내기" }));

    /* 회차는 **업무 상세로 읽은 7** 이다 — 전이와 같은 규칙이고 한 걸음 뒤지면 409 다. */
    await waitFor(() =>
      expect(api.submitTaskCompletion).toHaveBeenCalledWith("t-5", 7, {
        summary: "시안 세 벌을 넘겼다",
        output_material_ids: [],
      }),
    );
    /* 보고는 **전이를 안 지나고** 상태를 옮긴다 — 그래서 할 일이 「다시 읽기」 하나다.
       이 줄이 없으면 좌 레일 카드·간트 바가 옛 상태로 남는다. */
    await waitFor(() => expect(vi.mocked(api.getProject).mock.calls.length).toBe(before + 1));
    /* **전이는 한 번도 안 나갔다** — 보고가 상태를 옮겼으므로 또 보내지 않는다. */
    expect(api.transitionDirectTask).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /완료 보고/ })).toBeNull());
    expect((await screen.findByTestId("toast")).textContent).toBe("완료 보고를 보냈습니다. 요청자의 확인을 기다립니다.");
  });

  it("프로젝트를 못 읽으면 «목록부터» 다시 읽는다 — 거절된 배정은 목록에서 사라진다 (BE ⑪)", async () => {
    const other = { ...project, project_id: "p-2", name: "가을 캠페인" };
    vi.mocked(api.listProjects).mockResolvedValue([project, other] as never);
    vi.mocked(api.getProject).mockResolvedValue(detail as never);
    vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
    vi.mocked(api.getMemberDirectory).mockResolvedValue([] as never);
    vi.mocked(api.getTask).mockResolvedValue(taskDetail({ task_id: "t-1", title: "한빛 9월 통합 마케팅" }) as never);
    vi.mocked(api.listProjects).mockClear();
    render(<Harness />);
    await screen.findByTestId("rail-left");
    await waitFor(() => expect(vi.mocked(api.listProjects).mock.calls.length).toBe(1));

    /* 고르려는 그 프로젝트를 **못 읽는다** — 캐시한 목록만 들고 있으면 「없는 프로젝트를 고른 상태」가
       셀렉터에 남는다. 그래서 상세가 아니라 **목록부터** 다시 읽는다. */
    vi.mocked(api.getProject).mockRejectedValue(new Error("프로젝트를 찾을 수 없습니다"));
    fireEvent.click(screen.getByRole("button", { name: "프로젝트 선택" }));
    fireEvent.click(await screen.findByRole("option", { name: "가을 캠페인" }));

    await waitFor(() => expect(vi.mocked(api.listProjects).mock.calls.length).toBe(2));
    expect(noop).toHaveBeenCalledWith("프로젝트를 찾을 수 없습니다");
  });

  it("액션 버튼(재개·막힘·완료)이 이 화면에 없다 — 상태 드롭다운만이다 (L-32)", async () => {
    const side = await openState();
    await within(side).findByRole("button", { name: "플레이스 등록 상태" });
    const labels = Array.from(document.querySelectorAll("button")).map((button) => button.textContent?.trim());
    for (const forbidden of ["재개", "막힘", "완료", "시작"]) {
      expect(labels).not.toContain(forbidden);
    }
  });
  it("오늘은 «선»이 아니라 «칸»이다 — 하루 폭만큼 칠해진다 (사용자 지시 2026-09-22)", async () => {
    const { container } = await renderPage();
    await screen.findByText("한빛 9월 통합 마케팅", { selector: ".scax-pj-gantt__name-text" });
    const now = await waitFor(() => {
      const el = container.querySelector<HTMLElement>(".scax-pj-gantt__now");
      expect(el).not.toBeNull();
      return el;
    });
    /* **하루 폭(34px)이다.** 1px 이면 옛 「세로선」이 그대로 남은 것이다 —
       이 단언이 그 되돌림을 잡는다. 숫자는 `GANTT.day` 와 같은 값을 리터럴로 적는다
       (상수를 되계산해 비교하면 상수를 바꿔도 통과하는 «자기참조» 단언이 된다). */
    expect(now!.style.width).toBe("34px");
    /* 그리고 **막대를 가리지 않는다** — 행들보다 DOM 에서 먼저 선다. */
    const plot = now!.parentElement!;
    const kids = Array.from(plot.children);
    const firstRow = kids.findIndex((el) => el.classList.contains("scax-pj-gantt__row"));
    expect(kids.indexOf(now!)).toBeLessThan(firstRow);
  });

});
