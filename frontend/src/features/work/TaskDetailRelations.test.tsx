import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  getTask: vi.fn(),
  getTasks: vi.fn(),
  listProjects: vi.fn(),
  updateTask: vi.fn(),
  releaseSuccessor: vi.fn(),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  getTaskMaterials: vi.fn(),
  getTaskAssignments: vi.fn(),
  getTaskProposals: vi.fn(),
  createTaskProposal: vi.fn(),
  respondTaskProposal: vi.fn(),
  withdrawTaskProposal: vi.fn(),
  reopenTask: vi.fn(),
  getTaskChildren: vi.fn(),
  withdrawWorkRequest: vi.fn(),
  hideWorkRequestListEntry: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  attachTaskMaterialLink: vi.fn(),
  attachTaskMaterialReference: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  reassignTask: vi.fn(),
  taskMaterialContentUrl: () => "",
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  amendWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  submitTaskCompletion: vi.fn(),
  getActionItems: vi.fn(),
  runActionCommand: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";
import { stateOptions } from "./taskDetailHarness.test-utils";

/**
 * 업무 상세 — 덩어리 여섯과 연관 업무 2열 (WORK-007 F-1·F-2·F-3 · SPEC-007 §2).
 *
 * **여기서 검사하는 것은 «화면 구조»다.** 값·권한·오류는 서버가 정하고 이 파일은 그 값이
 * 어느 칸에 어떤 셈으로 서는지만 본다 — 확정 시안 `TaskDetail.html` 의 A·B·C 세 무대가 기준이다.
 */

/** 목록 투영 — `predecessors` 도 `successors` 도 `project_id` 도 **없다**. 이것이 결함 ① 의 씨앗이었다. */
const listRow: DirectTask = {
  task_id: "task-1",
  title: "연동 규격 확인",
  state: "open",
  version: 3,
  block_reason: null,
  description: "외부 연동 스펙을 확인한다.",
};

const detail = (extra: Record<string, unknown> = {}) =>
  ({
    ...listRow,
    checklist: [],
    references: [],
    children: [],
    predecessors: [],
    successors: [],
    hidden_successor_count: 0,
    project_id: null,
    ...extra,
  }) as unknown as DirectTask;

function renderDrawer(props: Record<string, unknown> = {}, task: DirectTask = listRow) {
  const onTransition = vi.fn().mockResolvedValue(true);
  const onOpenTask = vi.fn();
  const onChanged = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      onOpenTask={onOpenTask}
      onTransition={onTransition}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
      {...props}
    />,
  );
  return { onTransition, onOpenTask, onChanged };
}

/* 기본값은 **여기 한 자리**다 — 조회를 세 개나 거는 화면이라, 판마다 다시 깔면 한 줄만 빠져도
   서랍이 「불러오지 못했습니다」로 선다. 판이 필요한 것만 위에 덮어쓴다. */
beforeEach(() => {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue(detail() as never);
  vi.mocked(api.getTasks).mockResolvedValue([] as never);
  vi.mocked(api.listProjects).mockResolvedValue([] as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ─────────────────────────────── F-1 ─────────────────────────────── */

describe("F-1 · 덩어리 여섯과 선행 배선", () => {
  /**
   * **이 판이 고치는 그 배선이다** (BASE-005 결함 ① · SPEC-007 §4 · S-8).
   *
   * 목록 투영에는 `predecessors` 가 없다 — 계약상 **상세 조회에만** 실린다. 예전에는 그리는 쪽이
   * `task` prop 을 읽어서, 목록에서 연 서랍은 선행 칸도 시작 게이트도 **통째로 잃었다.**
   */
  it("목록 투영으로 열어도 상세 조회가 채운 선행이 선다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        predecessors: [{ task_id: "p-1", title: "설계 확정", state: "in_progress" }],
        preceding_task_ids: ["p-1"],
      }) as never,
    );
    renderDrawer();

    const cell = await screen.findByLabelText("선행 업무");
    await waitFor(() => expect(within(cell).getByRole("button", { name: "설계 확정 열기" })).toBeTruthy());
    expect(cell.querySelector(".cell__n")!.textContent).toBe("1 · 미완 1");
    // 상세는 선행으로 미리 막지 않는다 — 셀렉트에 진행 중이 있고, 막는 것은 서버다 (R5 · SPEC-007 §2.10.5).
    expect(await stateOptions()).toContain("진행 중");
  });

  /**
   * **0건과 「아직 안 왔다」를 다르게 그린다** (§2 읽는 규칙 3 · AC).
   * 상세가 오기 전에 「없음」으로 그리면 **있는 선행을 없다고 말한다.**
   */
  it("상세가 오기 전에는 「불러오는 중」이고 「없음」이 아니다", async () => {
    let settle: ((value: unknown) => void) | null = null;
    vi.mocked(api.getTask).mockReturnValue(new Promise((resolve) => { settle = resolve; }) as never);
    renderDrawer();

    const cell = await screen.findByLabelText("선행 업무");
    expect(within(cell).getByText("불러오는 중…")).toBeTruthy();
    expect(within(cell).queryByText("연결된 선행 업무가 없습니다.")).toBeNull();

    settle!(detail());
    await waitFor(() => expect(within(screen.getByLabelText("선행 업무")).getByText("연결된 선행 업무가 없습니다.")).toBeTruthy());
  });

  /** 덩어리 여섯의 **순서가 계약이다** — 앞의 넷이 시안 그대로다 (§2.1 · AC). */
  it("덩어리가 정해진 차례로 서고, 안이 비면 그 덩어리도 서지 않는다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");

    const heads = Array.from(document.querySelectorAll(".block__row > h3")).map((node) => node.textContent);
    // 「진행과 판단」은 기다리는 것이 하나도 없으면 서지 않는다 — 머리만 남은 빈 덩어리를 두지 않는다.
    // 「메타 정보」가 헤더 바로 아래 첫 구역이다 (SPEC-007 §2.10.2 · WORK-010 2a-2).
    expect(heads).toEqual(["메타 정보", "업무 정보", "연관 업무", "자료", "이력"]);
  });

  /**
   * **구획 여덟을 하나도 지우지 않았다** (§2.1 표 · AC).
   *
   * 이 판은 **자리를 정하는 것**이고 그 여덟의 조건·내용·명령은 그대로다. 「완료를 막는 하위」가
   * `진행과 판단` 에 서고 하위 **관계**는 `연관 업무` 에 따로 선다 — **같은 값을 두 자리에서
   * 다르게 쓴다**: 관계는 구조, 이것은 지금 못 끝내는 이유다.
   */
  /* WORK-010 2b-3 · SPEC-007 §2.10.3 — 「진행과 판단」 구역은 없다. 구획 중 조건이 참인 것만 메타 정보 바로 아래
     **걸린 일 상자**로 선다(순서는 SPEC 표). 「담당자 변경」 단추는 담당 셀렉트로, 막힘 사유 입력은 작은 모달로 갔다. */
  it("걸린 일 상자 여섯이 메타 정보 바로 아래 SPEC 순서로 서고, 「진행과 판단」·「담당자 변경」은 없다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        block_reason: "외부 응답을 기다리는 중",
        delivery: { action_item_id: "a1", status: "awaiting_revision", rounds: 2, reported_by: "mina", reported_at: null, summary: "1차 보고", last_reason: "표가 빠졌습니다" },
        derived: { approval: "awaiting_review" },
        children: [{ task_id: "c-1", title: "남은 하위", state: "in_progress" }],
      }) as never,
    );
    vi.mocked(api.getTaskAssignments).mockResolvedValue({
      current: { assignee_id: "mina" },
      pending: { assignee_id: "jiho", decline_reason: null },
    } as never);
    vi.mocked(api.getTaskProposals).mockResolvedValue({
      pending: [{ proposal_id: "p1", kind: "cancellation", reason: "범위가 바뀌었습니다", proposed_by: "jiho", payload: null }],
    } as never);
    vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([] as never);
    renderDrawer({ canAssign: true, personaId: "mina", personas: [{ id: "mina", display_name: "민아 (구성원)" }, { id: "jiho", display_name: "지호 (팀장)" }] });

    const group = await screen.findByRole("group", { name: "걸린 일" });
    await waitFor(() =>
      expect([...group.querySelectorAll(":scope > section")].map((box) => box.getAttribute("aria-label"))).toEqual([
        "막힘 사유",
        "완료 확인 대기",
        "보완 필요",
        "담당 변경 대기",
        "응답 대기 제안",
        "완료를 막는 하위",
      ]),
    );
    // 메타 정보 «바로 아래»다.
    expect(screen.getByLabelText("메타 정보").nextElementSibling).toBe(group);
    expect(screen.queryByLabelText("진행과 판단")).toBeNull();
    expect(screen.queryByText("진행과 판단")).toBeNull();
    expect(screen.queryByRole("button", { name: "담당자 변경" })).toBeNull();
    // 담당 변경 대기 중이면 담당 칸에 대상이 서고 셀렉트는 열리지 않는다(§2.10.6).
    expect(within(screen.getByLabelText("메타 정보")).getByText("지호에게 변경 제안 중")).toBeTruthy();
    // 이력은 자기 덩어리를 혼자 쓴다.
    expect(within(screen.getByLabelText("이력")).getByLabelText("활동·이력")).toBeTruthy();
    // 하위 **관계**는 여전히 `연관 업무` 쪽이다 — 두 자리가 같은 값을 다르게 쓴다.
    expect(within(screen.getByLabelText("연관 업무")).getByLabelText("하위 업무")).toBeTruthy();
  });

  it("걸린 일이 하나도 없으면 아무것도 서지 않는다 — 제목도 빈 문구도 없다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    vi.mocked(api.getTaskAssignments).mockResolvedValue(null as never);
    vi.mocked(api.getTaskProposals).mockResolvedValue(null as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");
    expect(screen.queryByRole("group", { name: "걸린 일" })).toBeNull();
  });

  /** **「산출물」을 이 화면에서만 걷는다** (D-05 · OQ-705). 저장 쪽 값 이름은 그대로다. */
  it("자료 두 칸이 「참고 자료」·「결과 자료」이고 「산출물」이 한 글자도 없다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");

    expect(screen.getByLabelText("참고 자료")).toBeTruthy();
    expect(screen.getByLabelText("결과 자료")).toBeTruthy();
    expect(within(screen.getByLabelText("결과 자료")).getByText("등록된 결과 자료가 없습니다.")).toBeTruthy();
    /* 개명 범위는 **이 드로어 안**이다 — 완료 보고 모달처럼 행 액션에서도 열리는 다른 표면은
       그대로 두므로(§2.5 · OQ-705) `document.body` 가 아니라 이 겹을 센다. */
    expect(screen.getByRole("dialog", { name: "업무 상세" }).textContent).not.toContain("산출물");
  });

  /** 「편집」은 **A(기본)에만**, 「AX」는 **세 무대 모두** (§2.2 · OQ-702). */
  /* WORK-010 2a · SPEC-007 §2.10.1·§2.10.9 — 「편집」과 「AX」는 **어느 무대에도** 없다(편집 모드 폐지 · 결정 d). */
  it("「편집」·「AX」는 기본 무대에도 연결 편집 중에도 없다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");

    expect(screen.queryByRole("button", { name: "편집" })).toBeNull();
    expect(screen.queryByRole("button", { name: "AX에게 이 업무 묻기" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    expect(screen.queryByRole("button", { name: "편집" })).toBeNull();
    expect(screen.queryByRole("button", { name: "AX에게 이 업무 묻기" })).toBeNull();
  });
});

/* ─────────────────────────────── F-2 ─────────────────────────────── */

describe("F-2 · 연관 업무 2열 여섯 칸", () => {
  /** **짝이 계약이다** — 상위\|프로젝트 · 하위\|선행 · 참고\|후행 (§2.4 · AC). */
  it("여섯 칸이 정해진 짝과 차례로 선다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");

    const cells = Array.from(screen.getByLabelText("연관 업무").querySelectorAll(".cell")).map((node) =>
      node.getAttribute("aria-label"),
    );
    expect(cells).toEqual(["상위 업무", "프로젝트", "하위 업무", "선행 업무", "참고 업무", "후행 업무"]);
  });

  /**
   * **후행 셈은 읽을 수 있는 수 + 못 읽는 수**다 (§2.4.5 · AC).
   * 줄 수만 세면 「나를 기다리는 일의 규모」가 샌다.
   */
  it("후행 셈에 비공개 건수가 들어가고, 그 줄은 제목 없이 건수만 낸다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        successors: [
          { task_id: "s-1", title: "연동 구현", state: "open", version: 2, assignee: { member_id: "jiho", display_name: "지호 (팀장)" } },
          { task_id: "s-2", title: "연동 테스트 시나리오", state: "open", version: 5, assignee: null },
        ],
        hidden_successor_count: 1,
      }) as never,
    );
    renderDrawer();

    const cell = await screen.findByLabelText("후행 업무");
    await waitFor(() => expect(cell.querySelector(".cell__n")!.textContent).toBe("3"));
    expect(within(cell).getByText("🔒 비공개 업무 1건이 이 업무를 기다립니다.")).toBeTruthy();
    // 못 읽는 후행은 **제목도 담당도 기한도** 없다 — 배열에 자리조차 없기 때문이다 (D-10).
    expect(within(cell).getAllByRole("listitem")).toHaveLength(2);
  });

  /** 프로젝트 칸 — `project_id` 는 아는데 이름을 못 읽는 조합이 **정상 갈래다** (§2.4.4). */
  it("프로젝트 이름을 목록에서 맞추고, 못 읽으면 「비공개 프로젝트」다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail({ project_id: "pj-9" }) as never);
    vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "pj-1", name: "하반기 제품 개편" }] as never);
    renderDrawer();

    const cell = await screen.findByLabelText("프로젝트");
    await waitFor(() => expect(within(cell).getByText("비공개 프로젝트")).toBeTruthy());
  });

  /**
   * **선행은 시작을, 하위는 완료를 막는다** (D-11 · AC).
   * 두 사실이 **다른 구획·다른 문구**로 서고 오류 코드도 합치지 않는다.
   */
  /* R5 · SPEC-007 §2.10.9 — 「시작할 수 없습니다」 배너는 **없다**. 완료를 막는 하위 안내는 그대로다. */
  it("시작 막힘 배너는 없고, 완료 막힘 안내는 그대로 선다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        predecessors: [{ task_id: "p-1", title: "연동 규격 확인", state: "in_progress" }],
        children: [{ task_id: "c-1", title: "남은 하위", state: "in_progress" }],
      }) as never,
    );
    renderDrawer();

    const childBlock = await screen.findByLabelText("완료를 막는 하위");
    expect(within(childBlock).getByText("아직 끝나지 않은 하위가 있습니다")).toBeTruthy();
    expect(screen.queryByLabelText("시작할 수 없습니다")).toBeNull();
    expect(screen.queryByText("시작할 수 없습니다")).toBeNull();
    expect(screen.queryByText(/이 끝나지 않았습니다\./)).toBeNull();
  });

  /** 배너 본문 — 못 읽는 선행이 섞이면 **건수를 덧붙인다** (§2.6 W-2). */
  it("제목을 쓸 수 있는 선행과 못 읽는 선행이 섞이면 선행 칸이 둘 다 센다 — 배너는 없다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        predecessors: [
          { task_id: "p-1", title: "설계 확정", state: "in_progress" },
          { task_id: "p-9", title: null, state: null },
        ],
      }) as never,
    );
    renderDrawer();

    const cell = await screen.findByLabelText("선행 업무");
    // 셈은 **못 읽는 것까지** 센다 — 확인할 수 없는 것을 끝난 것으로 치지 않는다.
    await waitFor(() => expect(cell.querySelector(".cell__n")!.textContent).toBe("2 · 미완 2"));
    expect(within(cell).getByText("설계 확정")).toBeTruthy();
    expect(within(cell).getByText("🔒 비공개 선행 업무 1건")).toBeTruthy();
    expect(screen.queryByLabelText("시작할 수 없습니다")).toBeNull();
  });

  /**
   * **못 읽는 선행만 남으면 막지 않는다** (SPEC-007 OQ-709 · 사용자 확정 2026-09-28).
   *
   * 서버는 그 선행의 상태를 «알고» 판정하므로 실제로 끝났으면 열어 준다 — 화면이 잠그면
   * **출구가 없다.** 아직 안 끝났으면 서버가 거절하고 그 문장이 그대로 뜬다.
   * 셈은 여전히 「미완」으로 세지만(모르는 것을 끝났다고 말하지 않는다) 단추는 열린다.
   */
  it("못 읽는 선행만 남으면 셈은 미완이되 단추와 배너는 열린다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({ predecessors: [{ task_id: "p-9", title: null, state: null }] }) as never,
    );
    renderDrawer();

    const cell = await screen.findByLabelText("선행 업무");
    await waitFor(() => expect(cell.querySelector(".cell__n")!.textContent).toBe("1 · 미완 1"));
    expect(within(cell).getByText("🔒 비공개 선행 업무 1건")).toBeTruthy();
    expect(screen.queryByLabelText("시작할 수 없습니다")).toBeNull();
    expect(await stateOptions()).toContain("진행 중");
  });

  /** **취소된 선행은 막지 않는다** — 그 일은 더 기다릴 것이 없다 (SPEC-001 계승). */
  it("취소·완료된 선행만 남으면 「모두 완료」이고 단추가 열린다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        predecessors: [
          { task_id: "p-1", title: "설계 확정", state: "cancelled" },
          { task_id: "p-2", title: "예산 승인", state: "done" },
        ],
      }) as never,
    );
    renderDrawer();

    const cell = await screen.findByLabelText("선행 업무");
    await waitFor(() => expect(cell.querySelector(".cell__n")!.textContent).toBe("2 · 모두 완료"));
    expect(screen.queryByLabelText("시작할 수 없습니다")).toBeNull();
    expect(await stateOptions()).toContain("진행 중");
  });

  /** 여섯 칸 전부 **줄을 누르면 그 업무가 열린다** (§2.4.1). */
  it("후행 줄을 누르면 그 업무가 열린다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({ successors: [{ task_id: "s-1", title: "연동 구현", state: "open", version: 2 }] }) as never,
    );
    const { onOpenTask } = renderDrawer();

    const cell = await screen.findByLabelText("후행 업무");
    fireEvent.click(await within(cell).findByRole("button", { name: "연동 구현 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("s-1");
  });
});

/* ─────────────────────────────── F-3 ─────────────────────────────── */

describe("F-3 · 연결 편집", () => {
  const rich = (over: Record<string, unknown> = {}) =>
    detail({
      parent: { task_id: "up-1", title: "제품 사용성 조사 정리", state: "in_progress" },
      children: [{ task_id: "c-1", title: "규격 문서 수집", state: "done" }],
      predecessors: [{ task_id: "p-1", title: "사용자 리서치 설문", state: "done" }],
      preceding_task_ids: ["p-1"],
      successors: [{ task_id: "s-1", title: "연동 구현", state: "open", version: 7 }],
      hidden_successor_count: 2,
      project_id: "pj-1",
      ...over,
    });

  const candidates = [
    { task_id: "task-1", title: "연동 규격 확인", version: 3 },
    { task_id: "c-1", title: "규격 문서 수집", version: 1 },
    { task_id: "p-1", title: "사용자 리서치 설문", version: 1 },
    { task_id: "free-1", title: "개편 범위 확정", version: 4 },
    { task_id: "up-1", title: "제품 사용성 조사 정리", version: 2 },
  ];

  async function openEditor(over: Record<string, unknown> = {}) {
    vi.mocked(api.getTask).mockResolvedValue(rich(over) as never);
    vi.mocked(api.getTasks).mockResolvedValue(candidates as never);
    vi.mocked(api.listProjects).mockResolvedValue([
      { project_id: "pj-1", name: "하반기 제품 개편" },
      { project_id: "pj-2", name: "2026 인프라 정비" },
    ] as never);
    const view = renderDrawer();
    await waitFor(() => expect(screen.getByRole("button", { name: "연결 편집" })).toBeTruthy());
    await waitFor(() => expect(within(screen.getByLabelText("하위 업무")).getByRole("button", { name: "규격 문서 수집 열기" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    // 후보가 도착해야 셀렉터에 고를 것이 선다 — 그 한 번이 여섯 칸을 다 먹인다.
    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: "저장" })).toBeTruthy());
    return view;
  }

  /** 편집은 **같은 2열 자리**다 — 칸의 순서·짝이 그대로다 (§2.8 · AC). */
  it("머리가 「연관 업무 편집」이 되고 칸 여섯의 차례가 그대로다", async () => {
    await openEditor();
    expect(document.querySelector(".block__row > h3")!.textContent).not.toBe("연관 업무 편집");
    expect(screen.getByText("연관 업무 편집")).toBeTruthy();
    expect(screen.getByRole("button", { name: "취소" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "저장" })).toBeTruthy();

    const cells = Array.from(screen.getByLabelText("연관 업무 편집").querySelectorAll(".cell")).map((node) =>
      node.getAttribute("aria-label"),
    );
    expect(cells).toEqual(["상위 업무", "프로젝트", "하위 업무", "선행 업무", "참고 업무", "후행 업무"]);
    // 편집 상태의 셈은 **건수만**이다 (§2.8 · 시안 `:359`).
    expect(screen.getByLabelText("하위 업무").querySelector(".cell__n")!.textContent).toBe("1");
  });

  /**
   * **후보를 미리 줄이지 않는다** (D-15 · §2.8.2 · AC).
   *
   * 거르는 것은 **자기 자신**과 **이미 그 칸에 걸린 항목** 둘뿐이다 — 그 둘은 이 화면이
   * 들고 있는 값으로 답할 수 있다. 순환·프로젝트 일치·읽기 권한은 **서버만 아는 판정**이다.
   */
  it("자기 자신과 이미 걸린 항목만 후보에서 빠지고, 조상도 후보에 남는다", async () => {
    await openEditor();
    fireEvent.click(within(screen.getByLabelText("선행 업무")).getByLabelText("선행 업무 추가"));

    const options = (await screen.findAllByRole("option")).map((node) => node.textContent);
    expect(options).toContain("개편 범위 확정");
    // 상위(= 조상)를 선행으로 고르면 순환이다 — 그래도 **후보에 있다.** 서버가 판정한다.
    expect(options).toContain("제품 사용성 조사 정리");
    // 이미 이 칸에 걸린 것과 지금 보고 있는 업무만 빠진다.
    expect(options).not.toContain("사용자 리서치 설문");
    expect(options).not.toContain("연동 규격 확인");
  });

  /** **하위는 대상이 «그 하위 업무»다** — 해제는 그 업무의 `clear_parent` 다 (§2.8.3 · D-17). */
  it("하위 해제가 그 하위 업무의 상위를 비운다", async () => {
    vi.mocked(api.updateTask).mockResolvedValue({ ...listRow, version: 4 } as never);
    await openEditor();
    fireEvent.click(within(screen.getByLabelText("하위 업무")).getByRole("button", { name: "규격 문서 수집 해제" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.updateTask).toHaveBeenCalledWith("c-1", expect.any(Number), { parent_task_id: null }));
  });

  /** 선행은 **배열 전체 교체 한 번**이다 — 그 안에 부분 성공이 없다 (§2.8.3). */
  it("선행의 해제와 추가가 한 번의 배열 교체로 나간다", async () => {
    vi.mocked(api.updateTask).mockResolvedValue({ ...listRow, version: 4 } as never);
    await openEditor();
    const cell = screen.getByLabelText("선행 업무");
    fireEvent.click(within(cell).getByRole("button", { name: "사용자 리서치 설문 해제" }));
    fireEvent.click(within(screen.getByLabelText("선행 업무")).getByLabelText("선행 업무 추가"));
    fireEvent.click(await screen.findByRole("option", { name: "개편 범위 확정" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() =>
      expect(api.updateTask).toHaveBeenCalledWith("task-1", expect.any(Number), { preceding_task_ids: ["free-1"] }),
    );
    // 한 칸이므로 명령도 하나다 — 해제와 추가가 따로 나가지 않는다.
    const precedingCalls = vi.mocked(api.updateTask).mock.calls.filter((call) => "preceding_task_ids" in (call[2] as object));
    expect(precedingCalls).toHaveLength(1);
  });

  /**
   * **상위와 프로젝트를 한 PATCH 에 싣지 않는다** — 서버가 그 조합을 422 로 거절한다.
   * 상위를 옮기면 프로젝트가 자손 전체로 따라가므로 둘이 한 요청에 오면 어느 쪽이 이기는지가
   * 정해지지 않는다. 화면도 **칸마다 따로 저장한다**(§2.8.3).
   */
  it("상위와 프로젝트가 갈라진 두 요청으로 나간다", async () => {
    vi.mocked(api.updateTask).mockResolvedValue({ ...listRow, version: 4 } as never);
    // 남은 선행이 있으면 프로젝트 칸이 잠긴다(아래 마지막 판) — 여기서는 그 축을 비워 둔다.
    await openEditor({ predecessors: [], preceding_task_ids: [] });
    fireEvent.click(within(screen.getByLabelText("상위 업무")).getByLabelText("상위 업무 고르기"));
    fireEvent.click(await screen.findByRole("option", { name: "개편 범위 확정" }));
    fireEvent.click(within(screen.getByLabelText("프로젝트")).getByLabelText("프로젝트 고르기"));
    fireEvent.click(await screen.findByRole("option", { name: "2026 인프라 정비" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.updateTask).toHaveBeenCalledWith("task-1", expect.any(Number), { parent_task_id: "free-1" }));
    await waitFor(() => expect(api.updateTask).toHaveBeenCalledWith("task-1", expect.any(Number), { project_id: "pj-2" }));
    for (const call of vi.mocked(api.updateTask).mock.calls) {
      const patch = call[2] as Record<string, unknown>;
      expect("parent_task_id" in patch && "project_id" in patch).toBe(false);
    }
  });

  /**
   * **부분 성공을 숨기지 않는다** (OQ-704 · AC).
   *
   * 한 트랜잭션으로 묶지 않는다 — 저장이 여러 «업무»를 건드리는데 기존 계약에 다중 업무
   * 원자 명령이 없다. 거절된 칸만 되돌리고 나머지는 반영된 채 남는다.
   */
  it("한 칸이 거절돼도 나머지 칸은 반영되고, 거절된 칸에만 서버 문장이 선다", async () => {
    vi.mocked(api.updateTask).mockImplementation((async (_id: string, _v: number, patch: Record<string, unknown>) => {
      if ("parent_task_id" in patch) throw new Error("자기 자신이나 자기 상위 업무를 상위로 둘 수 없습니다.");
      return { ...listRow, version: 4 };
    }) as never);
    await openEditor({ predecessors: [], preceding_task_ids: [] });
    fireEvent.click(within(screen.getByLabelText("상위 업무")).getByLabelText("상위 업무 고르기"));
    fireEvent.click(await screen.findByRole("option", { name: "개편 범위 확정" }));
    fireEvent.click(within(screen.getByLabelText("프로젝트")).getByLabelText("프로젝트 고르기"));
    fireEvent.click(await screen.findByRole("option", { name: "2026 인프라 정비" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    const message = await within(await screen.findByLabelText("상위 업무")).findByRole("alert");
    expect(message.textContent).toBe("자기 자신이나 자기 상위 업무를 상위로 둘 수 없습니다.");
    // 이 문장의 모양도 시안 CSS 가 쥔다 — 그 규칙이 닿으려면 스코프 «안»이어야 한다.
    expect(message.className).toBe("rel__error");
    expect(message.closest(".scax-td")).not.toBeNull();
    // 프로젝트 칸은 거절되지 않았으므로 그 명령은 **그대로 나갔다.**
    expect(api.updateTask).toHaveBeenCalledWith("task-1", expect.any(Number), { project_id: "pj-2" });
    // 거절이 있으면 편집을 닫지 않는다 — 고칠 자리가 남아야 한다.
    expect(screen.getByRole("button", { name: "저장" })).toBeTruthy();
    expect(within(screen.getByLabelText("프로젝트")).queryByRole("alert")).toBeNull();
  });

  /** 후행은 **해제만** 있고, 그 명령은 **B 의 회차**를 싣는다 (§4 · D-13 · D-19). */
  it("후행 해제가 그 후행 업무의 회차로 전용 명령을 부른다", async () => {
    vi.mocked(api.releaseSuccessor).mockResolvedValue({ successors: [], hidden_successor_count: 2 } as never);
    await openEditor();
    const cell = screen.getByLabelText("후행 업무");
    expect(within(cell).queryByLabelText(/후행 업무 추가/)).toBeNull();

    fireEvent.click(within(cell).getByRole("button", { name: "연동 구현 해제" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.releaseSuccessor).toHaveBeenCalledWith("task-1", "s-1", 7));
  });

  /** **비공개 후행에는 해제 단추가 서지 않는다** — 식별자도 내려오지 않는다 (D-16 · AC). */
  it("비공개 후행은 건수 한 줄로만 서고 해제할 수 없다", async () => {
    await openEditor();
    const cell = screen.getByLabelText("후행 업무");
    expect(within(cell).getByText("🔒 비공개 2건은 여기서 해제할 수 없습니다.")).toBeTruthy();
    // 해제 단추는 읽을 수 있는 후행 하나에만 있다.
    expect(within(cell).getAllByRole("button", { name: /해제$/ })).toHaveLength(1);
  });

  /** 전부 성공하면 **편집을 닫고 다시 읽는다** — 낙관적 갱신이 없다 (§2.8.3 · AC). */
  it("전부 성공하면 편집을 닫고 상세를 다시 읽는다", async () => {
    vi.mocked(api.updateTask).mockResolvedValue({ ...listRow, version: 4 } as never);
    await openEditor({ predecessors: [], preceding_task_ids: [] });
    fireEvent.click(within(screen.getByLabelText("프로젝트")).getByLabelText("프로젝트 고르기"));
    fireEvent.click(await screen.findByRole("option", { name: "2026 인프라 정비" }));
    const before = vi.mocked(api.getTask).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "저장" })).toBeNull());
    expect(vi.mocked(api.getTask).mock.calls.length).toBeGreaterThan(before);
    expect(screen.getByRole("button", { name: "연결 편집" })).toBeTruthy();
  });

  /**
   * **선행이 남아 있으면 프로젝트를 못 바꾼다** (`WORK_PROJECT_LOCKED_BY_PREDECESSORS`).
   *
   * 「화면이 미리 거르지 않는다」와 어긋나지 않는다 — **같은 화면이 들고 있는 값**으로 답할 수
   * 있는 판정이고(선행 칸이 바로 옆이다), 생성 모달이 이미 같은 자리를 같은 문장으로 잠근다.
   */
  it("선행이 남아 있으면 프로젝트 셀렉터가 잠기고 이유를 말한다", async () => {
    await openEditor();
    const cell = screen.getByLabelText("프로젝트");
    expect((within(cell).getByLabelText("프로젝트 고르기") as HTMLButtonElement).disabled).toBe(true);
    expect(within(cell).getByText("선행업무를 먼저 비워야 프로젝트를 바꿀 수 있습니다.")).toBeTruthy();

    fireEvent.click(within(screen.getByLabelText("선행 업무")).getByRole("button", { name: "사용자 리서치 설문 해제" }));
    expect((within(screen.getByLabelText("프로젝트")).getByLabelText("프로젝트 고르기") as HTMLButtonElement).disabled).toBe(false);
  });
});

/* ──────────────────────── 시안 CSS 가 «닿는가» (WORK-007 F-1 · 검수 FAIL-1) ────────────────────────
 *
 * **이 판이 없어서 129줄이 통째로 죽은 채 테스트 1082건이 초록이었다.**
 *
 * `styles/task-detail.css` 는 규칙을 전부 `.scax-td` 아래에 둔다 — 시안이 쓰는 이름이
 * `.block`·`.cols`·`.cell`·`.one`·`.meta` 처럼 일반적이라 전역에 풀면 다음 화면을 덮기 때문이다.
 * 그 스코프는 옳은데 **붙이는 자리를 안 만들었고**, jsdom 은 CSS 를 적용하지 않으므로
 * 렌더 테스트가 그것을 알아차릴 길이 없었다.
 *
 * 그래서 **계산된 스타일이 아니라 «선택자가 닿는 구조»를 센다.** CSS 파일에서 스코프 선택자를
 * 직접 읽어 와, 그 안쪽 조각(`.cols` 따위)에 맞는 요소가 화면에 **있고** 그 요소들이 **전부
 * `.scax-td` 안**에 있는지 본다. 스코프를 안 붙이면 둘째 조건이 깨지고, 클래스 이름을 바꾸면
 * 첫째 조건이 깨진다.
 */
/*
 * 시안 CSS 를 **글자 그대로** 읽어 온다. 목록을 손으로 베껴 적으면 CSS 가 바뀔 때 이 판이 같이
 * 안 바뀐다.
 *
 * ⚠ **`?raw` 로 가져오면 안 된다** — vitest 는 CSS 를 **빈 문자열로 스텁**한다(`test.css` 기본값).
 * 한때 그렇게 썼다가 선택자 목록이 0개가 됐고, 아래 판이 **아무것도 안 세면서 초록**이었다.
 * 그 사고를 막는 것이 `scopedSelectors()` 맨 끝의 「하나라도 읽혔는가」 검사다.
 */
/**
 * 이 화면이 쓰는 **스코프 안쪽 클래스 이름 전부** (`styles/task-detail.css`).
 *
 * ⚠ **목록을 CSS 에서 자동으로 읽어 오지 않는다.** 한때 `?raw` 로 읽었는데 **vitest 가 CSS 를 빈
 * 문자열로 스텁해**(`test.css` 기본값) 목록이 0개가 됐고, 그래서 이 판이 «아무것도 안 세면서»
 * 초록이었다. 자동화가 조용히 비면 검사가 아니라 **거짓 초록**이다 — 그래서 손으로 적고,
 * 아래에서 **하나하나 실제로 맞는지** 확인한다. 이름이 바뀌면 그 줄이 떨어진다.
 */
const SCOPED_CLASSES = [
  "block",
  "block__row",
  "cols",
  "stack",
  "cell",
  "cell__head",
  "cell__n",
  "one",
  "one--empty",
  "empty",
  "private",
  /* 메타 정보 격자 (WORK-010 2a-2) — 격자 자체는 공용 `.meta-grid` 이고, 이 화면의 촘촘함이 `.meta-info` 다. */
  "meta-info",
  "meta-info__value",
  "desc",
  "desc--editable",
  "rel__addrow",
  "rel__addlabel",
  "rel__select",
  "list-more",
  "blocked-reason",
] as const;

/**
 * CSS 가 **자손으로** 겨누는 이름들 — DS·공용 부품이라 화면의 다른 데서도 쓴다.
 *
 * 그래서 위와 규칙이 다르다: 「전부 `.scax-td` 안에 있어야 한다」가 아니라
 * **「`.scax-td` 안에서 한 번은 맞아야 한다」**다. 밖에도 있는 것이 정상이고
 * (겹의 머리줄에도 단추가 선다) 안에서 한 번도 안 맞으면 **그 규칙이 죽은 것**이다.
 */
const SCOPED_DESCENDANTS = [
  "material-list",
  "material-list--wide",
  "checklist",
  "scax-checklist__row",
  "drawer-section",
  "notice",
  /* ~~"danger"~~ — 이 판의 픽스처에서 `.scax-td` 안에 서던 `notice danger` 는 시작 막힘 배너였고 그 배너가
     없어졌다(WORK-010 2a-4 · R5). `.danger` 를 겨누는 규칙(`:not(.danger)`)은 「보완 필요」 구획을 위해 남고,
     그 구획은 완료 보고를 돌려받은 업무에만 서므로 이 세 무대에는 나오지 않는다. */
  "t-meta",
  "scax-button",
  "scax-button--inline",
  "scax-button--sm",
  "select-trigger",
  "popover-root",
  "date-field",
  "desc--empty",
] as const;

/*
 * ⚠ `rel__error` 는 이 목록에 **없다** — 저장이 거절됐을 때만 서는 자리라 위 세 무대에 안 나온다.
 * 「안 맞았다」와 「죽었다」를 섞지 않으려고 **그 사실을 만드는 판**에서 따로 센다
 * (「한 칸이 거절돼도 …」).
 */

describe("시안 CSS 의 스코프가 실제로 닿는다", () => {
  /**
   * **이 판이 없어서 CSS 129줄이 통째로 죽은 채 테스트가 초록이었다** (검수 FAIL-1).
   *
   * 규칙이 전부 `.scax-td` 아래인데 그 클래스를 붙이는 자리를 안 만들었고, **jsdom 은 CSS 를
   * 적용하지 않으므로** 렌더 테스트가 그것을 알아차릴 길이 없었다. 그래서 계산된 스타일이
   * 아니라 **«선택자가 닿는 구조»** 를 센다.
   */
  it("`.cols` 를 쓰는 요소가 있고, 그 조상에 `.scax-td` 가 있다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");

    const cols = Array.from(document.querySelectorAll(".cols"));
    // 연관 업무와 자료, 둘이 2열이다.
    expect(cols.length).toBe(2);
    for (const node of cols) expect(node.closest(".scax-td")).not.toBeNull();
  });

  /**
   * 두 가지를 본다. ① 스코프 안쪽 이름에 맞는 요소는 **전부** `.scax-td` 안에 있다 — 하나라도
   * 밖에 있으면 그 규칙이 그 요소에 안 닿는다 ② 세 무대를 통틀어 **한 번도 안 맞는 이름이 없다** —
   * 뜨면 그 규칙은 마크업에 짝이 없다(이름이 바뀌었거나 마크업이 사라졌다).
   */
  it("스코프 안쪽 클래스가 전부 `.scax-td` 안에서만 맞는다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({
        parent: { task_id: "up-1", title: "상위", state: "in_progress" },
        project_id: "pj-1",
        /* 하위를 **넷**으로 둔다 — 한도(3)를 넘겨야 「더 보기」 한 줄이 서고, 그래야
           그 규칙이 죽지 않았음을 셀 수 있다. */
        children: [1, 2, 3, 4].map((n) => ({ task_id: `c-${n}`, title: `하위 ${n}`, state: "in_progress" })),
        predecessors: [{ task_id: "p-1", title: "선행", state: "in_progress" }],
        successors: [{ task_id: "s-1", title: "후행", state: "open", version: 2 }],
        hidden_successor_count: 1,
        references: [{ reference_id: "r-1", created_by: "mina", task: { task_id: "t-0", title: "참고", state: "done" } }],
        checklist: [{ item_id: "i1", text: "단계", position: 1, done: false, state: "active", version: 1, created_by: "mina", completed_by: null, completed_at: null }],
        block_reason: "막힌 이유",
        /* 설명이 비면 `.desc--empty` 가 서고, 누르면 그 자리에 `textarea.desc` 가 선다 — 두 갈래를 다 지난다. */
        description: null,
      }) as never,
    );
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "free-1", title: "고를 업무", version: 1 }] as never);
    vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "pj-1", name: "프로젝트" }] as never);
    vi.mocked(api.getTaskMaterials).mockResolvedValue([
      { material_id: "m1", binding_id: "b1", task_id: "task-1", kind: "input", name: "자료.pdf", content_type: "application/pdf", size_bytes: 10, uploaded_by: "mina", created_at: "2026-09-25T00:00:00Z", removed_at: null, source_kind: "file" },
    ] as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");

    const seen = new Set<string>();
    const seenInside = new Set<string>();
    const escaped: string[] = [];
    const check = () => {
      for (const name of SCOPED_CLASSES) {
        const nodes = Array.from(document.querySelectorAll(`.${name}`));
        if (nodes.length > 0) seen.add(name);
        // 스코프 밖에서 맞으면 그 규칙은 **그 요소에 안 닿는다** — 죽은 스타일이다.
        for (const node of nodes) if (!node.closest(".scax-td")) escaped.push(name);
      }
      for (const name of SCOPED_DESCENDANTS) {
        if (document.querySelector(`.scax-td .${name}`)) seenInside.add(name);
      }
    };

    check(); // 무대 A — 기본
    fireEvent.click(screen.getByRole("button", { name: "업무 내용 고치기" }));
    check(); // 업무 내용이 그 자리 여러 줄 입력(`textarea.desc`)인 상태
    fireEvent.keyDown(screen.getByRole("textbox", { name: "업무 내용 고치기" }), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "저장" })).toBeTruthy());
    check(); // 무대 C — 연결 편집

    expect(escaped).toEqual([]);
    expect(SCOPED_CLASSES.filter((name) => !seen.has(name))).toEqual([]);
    expect(SCOPED_DESCENDANTS.filter((name) => !seenInside.has(name))).toEqual([]);
  });
});

/* ──────────────────────── 체크리스트 칸의 «통» (사용자 보고 2026-09-28) ────────────────────────
 *
 * 스크롤 통이 목록만 담는지 — 빈 상태와 입력폼까지 삼키면 **상자 안의 상자**가 되고
 * **목록이 길 때 적는 자리가 스크롤 아래로 숨는다.**
 */
/**
 * 셀렉터가 **칸 폭까지 닿는 사슬** — **두 자리 모두** (사용자 지시 2026-09-28).
 *
 * 폭은 조상에서 자손으로 «이어져» 내려온다 — 한 고리라도 내용 크기로 굳으면 거기서 끊기고,
 * 그 아래의 `width: 100%` 는 **쪼그라든 부모**의 100% 를 잰다. 세 판을 그렇게 헛짚었다:
 * `.rel__select` 를 늘려도 그 안의 `span.popover-root` 가 `display: inline-flex` 라 끊겼다.
 *
 * **jsdom 은 CSS 를 계산하지 않아 그 실패를 한 번도 못 잡았다.** 계산된 폭은 못 재도
 * **CSS 가 겨누는 그 길이 실제로 서 있는지**는 잴 수 있다 — 중간에 못 보던 겹이 끼면
 * `>` 결합자가 빗나가고, 그러면 규칙이 닿지 않는다. 그것이 이 판이 세는 것이다.
 *
 * ⚠ **두 자리를 다 센다.** 한때 「추가」 줄만 세고 있었고, 그 사이 값 칸(상위·프로젝트)이
 * 같은 자리에서 다시 끊겨 있었다 — 세는 자리가 하나면 같은 실패가 나머지에 숨는다.
 */
describe("셀렉터의 폭 사슬 — 값 칸과 「추가」 줄", () => {
  /** 트리거에서 `.scax-td` 까지 거슬러 올라가며 조상의 «태그.클래스» 를 적는다. */
  const chainOf = (trigger: Element | null): string[] => {
    expect(trigger).not.toBeNull();
    const chain: string[] = [];
    for (let node = trigger; node && !node.classList.contains("scax-td"); node = node.parentElement) {
      chain.push(`${node.tagName.toLowerCase()}.${Array.from(node.classList).join(".")}`);
    }
    return chain.reverse();
  };

  async function openEditing() {
    vi.mocked(api.getTask).mockResolvedValue(detail() as never);
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "free-1", title: "고를 업무", version: 1 }] as never);
    vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "pj-1", name: "프로젝트" }] as never);
    renderDrawer();
    await screen.findByLabelText("선행 업무");
    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "저장" })).toBeTruthy());
  }

  it("「추가」 줄 — 칸부터 트리거까지가 CSS 가 겨누는 그대로다", async () => {
    await openEditing();
    const trigger = document.querySelector(".rel__addrow .select-trigger");
    expect(chainOf(trigger)).toEqual([
      "section.block",
      "div.cols",
      "div.cell",
      "div.rel__addrow",
      "div.rel__select",
      "span.popover-root", // ← 끊기던 자리. `.scax-td .rel__select > .popover-root` 가 편다
      "button.select-trigger",
    ]);
  });

  it("값 칸(상위·프로젝트) — 같은 사슬이고 `.rel__addrow` 만 없다", async () => {
    await openEditing();
    const trigger = within(screen.getByLabelText("상위 업무")).getByLabelText("상위 업무 고르기");
    expect(chainOf(trigger)).toEqual([
      "section.block",
      "div.cols",
      "div.cell",
      "div.rel__select",
      "span.popover-root",
      "button.select-trigger",
    ]);
  });

  /**
   * 「추가」 줄에만 걸려 있으면 값 칸이 조용히 끊긴다 — 그것이 한 판 동안 숨어 있던 실패다.
   * 규칙이 **두 자리 다**에 닿는지를 직접 센다.
   */
  it("`> .popover-root` 규칙이 두 자리 모두에 닿는다", async () => {
    await openEditing();
    const roots = Array.from(document.querySelectorAll(".scax-td .rel__select > .popover-root"));
    // 상위·프로젝트 둘 + 하위·선행·참고의 「추가」 줄 셋 = 다섯. 후행에는 추가 줄이 없다.
    expect(roots.length).toBe(5);
    // `>` 로 겨누므로 **직속 자식**이어야 한다 — 중간에 겹이 끼면 규칙이 빗나간다.
    for (const root of roots) expect(root.parentElement!.classList.contains("rel__select")).toBe(true);
    // 값 칸 쪽도 그 집합 안에 있다.
    const valueTrigger = within(screen.getByLabelText("프로젝트")).getByLabelText("프로젝트 고르기");
    expect(roots).toContain(valueTrigger.parentElement);
  });
});

/* ──────────────────── 목록을 «통»에서 꺼냈다 (사용자 지시 2026-09-28) ────────────────────
 *
 * 실선 테두리 + 고정 높이 + 스크롤이 목록을 **표** 처럼 보이게 했다 — 「그냥 컴포넌트가 이어져
 * 있는 형태」가 맞는 모양이다. 길이는 통이 아니라 **「더 보기」** 가 잡는다.
 */
describe("목록은 통 없이 이어지고 길이는 「더 보기」가 잡는다", () => {
  const steps = (count: number) =>
    Array.from({ length: count }, (_, index) => ({
      item_id: `i${index + 1}`,
      text: `단계 ${index + 1}`,
      position: index + 1,
      done: false,
      state: "active",
      version: 1,
      created_by: "mina",
      completed_by: null,
      completed_at: null,
    }));
  const children = (count: number) =>
    Array.from({ length: count }, (_, index) => ({ task_id: `c-${index + 1}`, title: `하위 ${index + 1}`, state: "in_progress" }));

  it("통(`.scroll`)이 어디에도 서지 않는다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(
      detail({ children: children(2), checklist: steps(2), predecessors: [{ task_id: "p-1", title: "선행", state: "done" }] }) as never,
    );
    renderDrawer();
    await screen.findByLabelText("선행 업무");
    expect(document.querySelectorAll(".scroll").length).toBe(0);
  });

  it("체크리스트 0건이면 빈 상태와 입력폼이 맨몸으로 선다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail({ checklist: [] }) as never);
    renderDrawer();
    const cell = await screen.findByLabelText("체크리스트");

    expect(within(cell).getByText("단계가 없습니다.")).toBeTruthy();
    expect(cell.querySelector("ul.checklist")).toBeNull();
    // 적는 자리는 언제나 목록 아래에 있다.
    expect(within(cell).getByLabelText("체크리스트 단계")).toBeTruthy();
    expect((within(cell).getByLabelText("체크리스트 단계") as HTMLInputElement).type).toBe("text");
  });

  /** 한도 이하면 접을 것이 없다 — 단추를 세우면 누를 이유가 없는 단추가 된다. */
  it("관계 목록이 3개 이하면 「더 보기」가 없다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail({ children: children(3) }) as never);
    renderDrawer();
    const cell = await screen.findByLabelText("하위 업무");

    await waitFor(() => expect(within(cell).getAllByRole("listitem")).toHaveLength(3));
    expect(within(cell).queryByRole("button", { name: /더 보기/ })).toBeNull();
  });

  it("4개면 셋만 보이고 「더 보기 1개」를 누르면 전부 선다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail({ children: children(4), child_progress: { done: 0, total: 4 } }) as never);
    renderDrawer();
    const cell = await screen.findByLabelText("하위 업무");

    await waitFor(() => expect(within(cell).getAllByRole("listitem")).toHaveLength(3));
    // **셈은 머리에 그대로 있다** — 접혀 있어도 전체 수를 안다.
    expect(cell.querySelector(".cell__n")!.textContent).toBe("0 / 4 · 완료 막음 4");

    fireEvent.click(within(cell).getByRole("button", { name: "하위 업무 더 보기 1개" }));
    expect(within(cell).getAllByRole("listitem")).toHaveLength(4);

    fireEvent.click(within(cell).getByRole("button", { name: "하위 업무 접기" }));
    expect(within(cell).getAllByRole("listitem")).toHaveLength(3);
  });

  /** 체크리스트만 다섯 줄을 준다 — 「길어질 자리」이기 때문이다. */
  it("체크리스트는 5개까지 보이고 6개째부터 접힌다", async () => {
    vi.mocked(api.getTask).mockResolvedValue(detail({ checklist: steps(5) }) as never);
    renderDrawer();
    const cell = await screen.findByLabelText("체크리스트");
    await waitFor(() => expect(cell.querySelectorAll("li.scax-checklist__row")).toHaveLength(5));
    expect(within(cell).queryByRole("button", { name: /더 보기/ })).toBeNull();
    cleanup();

    vi.mocked(api.getTask).mockResolvedValue(detail({ checklist: steps(6) }) as never);
    renderDrawer();
    const six = await screen.findByLabelText("체크리스트");
    await waitFor(() => expect(six.querySelectorAll("li.scax-checklist__row")).toHaveLength(5));
    fireEvent.click(within(six).getByRole("button", { name: "체크리스트 더 보기 1개" }));
    expect(six.querySelectorAll("li.scax-checklist__row")).toHaveLength(6);
  });
});
