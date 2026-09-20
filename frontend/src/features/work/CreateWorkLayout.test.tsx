import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  getTasks: vi.fn(),
  listProjects: vi.fn(),
  getTask: vi.fn(),
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
  submitTaskCompletion: vi.fn(),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
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
  getActionItems: vi.fn(),
  runActionCommand: vi.fn(),
}));

import * as api from "../../lib/api";
import { CreateWorkModal } from "./WorkModals";

const jiho = { id: "jiho", display_name: "지호 (팀장)" } as never;
const ccPeople = [
  { id: "sora", display_name: "소라 (기획)" },
  { id: "yuna", display_name: "유나 (대표)" },
] as never;

const earlier = [
  {
    task_id: "task-0",
    title: "1분기 정산",
    state: "done",
    version: 1,
    block_reason: null,
    assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
    project_id: "p-1",
  },
  {
    task_id: "task-1",
    title: "2분기 전망치 취합",
    state: "in_progress",
    version: 1,
    block_reason: null,
    assignee: null,
    project_id: null,
  },
];

function renderModal(props: Record<string, unknown> = {}) {
  vi.mocked(api.getTasks).mockResolvedValue(earlier as never);
  vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "p-1", name: "AX 고도화" }] as never);
  const onError = vi.fn();
  render(
    <CreateWorkModal
      assignCandidates={[]}
      assigneeCandidates={[jiho]}
      canCreateRequest
      canCreateTask
      ccCandidates={ccPeople}
      onClose={vi.fn()}
      onCreated={vi.fn()}
      onError={onError}
      ownerName="민아"
      {...props}
    />,
  );
  return { onError };
}

const modal = () => screen.getByRole("dialog", { name: "새 업무 추가" });
const openPanel = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));
const panelOf = (name: string) => document.getElementById(`create-panel-${name}`) as HTMLElement;

/**
 * 최종 발주 1·2·7 — **고정된 골격 안의 왼쪽 세로 탭.**
 *
 * 지금까지 필드는 한 기둥에 길게 이어 붙어 있어서, 갈래를 바꾸거나 필드가 늘 때마다 모달이 세로로
 * 자라 화면 밖으로 밀렸다. 여기서 못박는 것은 셋이다 — 골격이 스스로 크지 않는가 · 판을 옮겨도 적어
 * 둔 값이 남는가 · 머리가 한 줄인가.
 */
describe("업무 생성 모달의 골격", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("필수 하나와 선택 셋으로 나눠 세우고, 기본 정보를 먼저 연다", () => {
    renderModal();
    const nav = within(modal());
    expect(nav.getByRole("tablist", { name: "필수" })).toBeTruthy();
    expect(nav.getByRole("tablist", { name: "선택" })).toBeTruthy();
    expect(nav.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "내 업무",
      "요청 업무",
      "기본 정보",
      "체크리스트",
      "업무 연결",
      "자료",
    ]);
    expect(screen.getByRole("tab", { name: "기본 정보" }).getAttribute("aria-selected")).toBe("true");
    // 판은 넷 다 그려 둔 채 숨긴다 — 그래야 옮겨도 적어 둔 값이 남는다.
    expect(panelOf("checklist").hasAttribute("hidden")).toBe(true);
    expect(panelOf("basic").hasAttribute("hidden")).toBe(false);
  });

  it("스크롤 기둥은 판 하나다 — 골격은 자라지 않는다", () => {
    renderModal();
    // 크기는 골격이 CSS 로 못박는다. 여기서 보는 것은 «그 약속을 떠받치는 마크업» 이다:
    // 본문 안에 탭 판이 서고, 늘어나는 것은 그 판뿐이다.
    const dialog = modal();
    expect(dialog.className).toContain("scax-modal--create");
    const body = dialog.querySelector(".scax-modal__body") as HTMLElement;
    expect(body.firstElementChild?.className).toBe("scax-create-tabs");
    expect(panelOf("basic").className).toContain("scax-create-tabs__panel");
    // 머리와 발은 판 «밖» 이다 — 판이 아무리 길어져도 고정이다.
    expect(dialog.querySelector(".scax-modal__head")?.contains(panelOf("basic"))).toBe(false);
    expect(dialog.querySelector(".scax-modal__foot")?.contains(panelOf("basic"))).toBe(false);
  });

  it("판을 옮겨도 적어 둔 값이 그대로 남는다", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "옮겨도 남는 제목" } });

    openPanel("체크리스트");
    const steps = screen.getByLabelText("시작 단계");
    fireEvent.change(within(steps).getByLabelText("추가할 단계"), { target: { value: "자료 모으기" } });
    fireEvent.click(within(steps).getByRole("button", { name: "단계 추가" }));

    openPanel("업무 연결");
    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());

    openPanel("기본 정보");
    expect((screen.getByLabelText("업무 제목") as HTMLInputElement).value).toBe("옮겨도 남는 제목");
    openPanel("체크리스트");
    expect(within(screen.getByLabelText("시작 단계")).getByText("자료 모으기")).toBeTruthy();
  });

  it("세로 탭은 화살표로도 옮겨진다", () => {
    renderModal();
    openPanel("체크리스트");
    fireEvent.keyDown(screen.getByRole("tab", { name: "체크리스트" }), { key: "ArrowDown" });
    expect(screen.getByRole("tab", { name: "업무 연결" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "업무 연결" }), { key: "ArrowUp" });
    expect(screen.getByRole("tab", { name: "체크리스트" }).getAttribute("aria-selected")).toBe("true");
  });

  it("모달 이름이 고른 갈래를 따른다 — 토글은 그대로 선다", async () => {
    renderModal();
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();

    const toggle = within(screen.getByRole("tablist", { name: "생성 유형" }));
    fireEvent.click(toggle.getByRole("tab", { name: "요청 업무" }));
    expect(await screen.findByRole("dialog", { name: "새 업무 요청" })).toBeTruthy();
    // 이름만 따라 움직인다 — 토글을 걷지 않는다.
    expect(screen.getByRole("tablist", { name: "생성 유형" })).toBeTruthy();

    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "내 업무" }));
    expect(await screen.findByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();
  });

  it("머리는 한 줄이다 — 이름과 갈래 토글뿐이고 설명 문구가 없다", () => {
    renderModal();
    const head = modal().querySelector(".scax-modal__head") as HTMLElement;
    expect(within(head).getByRole("tablist", { name: "생성 유형" })).toBeTruthy();
    expect(head.textContent).not.toMatch(/내가 할 업무를 만듭니다|수락해야 그 사람의 업무|배정합니다/);
  });
});

/**
 * WORK-003 Phase 4 (SPEC-001 U-6-b · U-13) — **`업무 연결` 판의 줄 셋.**
 *
 * `상위 업무`(단일) · `프로젝트`(단일) · `선행 업무`(복수)다. **참고 업무는 이 창에서 내렸다** —
 * 참조자(사람)와 참고 업무(업무)가 한 판에서 「참조」라는 같은 낱말로 섞여 읽혔기 때문이고,
 * 계약·저장 모델·상세의 자리는 그대로다.
 */
describe("업무 연결 판", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const linksPanel = () => within(panelOf("links"));
  const pickProject = async (name: string) => {
    fireEvent.click(linksPanel().getByLabelText("프로젝트"));
    fireEvent.click(await screen.findByRole("option", { name }));
  };

  it("줄 넷이 선다 — 상위·프로젝트 한 행에 참고 업무와 선행 업무가 따른다", async () => {
    renderModal();
    openPanel("업무 연결");
    const panel = panelOf("links");
    await within(panel).findByLabelText("상위 업무");

    const row = panel.querySelector(".scax-field-row") as HTMLElement;
    expect(within(row).getByLabelText("상위 업무")).toBeTruthy();
    expect(within(row).getByLabelText("프로젝트")).toBeTruthy();
    // 참고와 선행은 **서로 다른 관계**이고 각자의 칸으로 선다.
    expect(within(panel).getByRole("group", { name: "참고 업무" })).toBeTruthy();
    expect(within(panel).getByRole("group", { name: "선행 업무" })).toBeTruthy();
    expect(panel.textContent).not.toContain("후속 업무");
  });

  /**
   * WORK-003 정정 — **참고 업무는 프로젝트를 가리지 않는다.**
   *
   * 선행은 같은 묶음 안의 순서라 프로젝트를 먼저 골라야 열리지만, 참고는 「함께 읽히는 업무」라
   * 묶음과 무관하다. 표 모양(업무명·프로젝트·담당자)은 둘이 같은 것을 쓴다.
   */
  it("참고 업무 표는 프로젝트를 고르기 전에도 읽을 수 있는 업무를 모두 세운다", async () => {
    renderModal();
    openPanel("업무 연결");
    const table = await linksPanel().findByRole("table", { name: "참고 업무" });
    await within(table).findByRole("checkbox", { name: "1분기 정산" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "고르기",
      "업무명",
      "프로젝트",
      "담당자",
    ]);
    // 프로젝트가 있는 업무도 없는 업무도 함께 선다 — 선행 표와 후보가 다르다.
    expect(within(table).getByRole("checkbox", { name: "1분기 정산" })).toBeTruthy();
    expect(within(table).getByRole("checkbox", { name: "2분기 전망치 취합" })).toBeTruthy();
    const row = within(table).getByRole("checkbox", { name: "1분기 정산" }).closest(".scax-pick-table__row") as HTMLElement;
    expect(row.textContent).toContain("AX 고도화");
    expect(row.textContent).toContain("지호");
  });

  it("고른 참고 업무는 체크된 채 남고 reference_task_ids 로 실려 나간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "참고를 단 업무" } });
    openPanel("업무 연결");
    const table = await linksPanel().findByRole("table", { name: "참고 업무" });
    const box = (await within(table).findByRole("checkbox", { name: "1분기 정산" })) as HTMLInputElement;
    fireEvent.click(box);
    // 선행과 달리 고른 줄이 표에서 빠지지 않는다 — 체크박스 한 번으로 되돌릴 수 있어야 한다.
    expect((within(table).getByRole("checkbox", { name: "1분기 정산" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.reference_task_ids).toEqual(["task-0"]);
  });

  it("참고 업무와 선행 업무는 서로 다른 관계다 — 하나를 골라도 다른 쪽이 따라가지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "두 관계를 가른 업무" } });
    openPanel("업무 연결");
    await linksPanel().findByLabelText("프로젝트");
    await pickProject("AX 고도화");

    const references = within(await linksPanel().findByRole("table", { name: "참고 업무" }));
    fireEvent.click(await references.findByRole("checkbox", { name: "2분기 전망치 취합" }));
    const preceding = within(await linksPanel().findByRole("table", { name: "선행 업무" }));
    fireEvent.click(await preceding.findByRole("checkbox", { name: "1분기 정산" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    const [, extra] = vi.mocked(api.createDirectTask).mock.calls[0];
    expect(extra?.reference_task_ids).toEqual(["task-1"]);
    expect(extra?.preceding_task_ids).toEqual(["task-0"]);
  });

  it("상위 업무는 고르기 전에 「상위 업무 선택」이고, 고르면 업무명이 선다", async () => {
    renderModal();
    openPanel("업무 연결");
    const parent = await linksPanel().findByLabelText("상위 업무");
    expect(parent.textContent).toContain("상위 업무 선택");

    fireEvent.click(parent);
    const option = await screen.findByRole("option", { name: /1분기 정산/ });
    expect(option.textContent).toContain("AX 고도화");
    expect(option.textContent).toContain("지호");
    fireEvent.click(option);

    expect(linksPanel().getByLabelText("상위 업무").textContent).toContain("1분기 정산");
  });

  it("「상위 업무 없음」으로 잘못 고른 상위를 되돌린다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "상위를 되돌린 업무" } });
    openPanel("업무 연결");
    const parent = await linksPanel().findByLabelText("상위 업무");

    fireEvent.click(parent);
    fireEvent.click(await screen.findByRole("option", { name: /1분기 정산/ }));
    fireEvent.click(linksPanel().getByLabelText("상위 업무"));
    fireEvent.click(await screen.findByRole("option", { name: "상위 업무 없음" }));
    expect(linksPanel().getByLabelText("상위 업무").textContent).toContain("상위 업무 선택");

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    const [, extra] = vi.mocked(api.createDirectTask).mock.calls[0];
    expect(extra?.parent_task_id).toBeUndefined();
    expect(JSON.stringify(extra)).not.toContain("no_parent");
  });

  it("여는 쪽이 정해 준 상위는 고치지 못하고 parent_task_id 로 그대로 간다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "하위 요청" } as never);
    renderModal({ canCreateTask: false, initial: { parentTaskId: "task-0" } });
    fireEvent.click(screen.getByRole("tab", { name: "업무 연결" }));
    const parent = await linksPanel().findByLabelText("상위 업무");
    await waitFor(() => expect(parent.textContent).toContain("1분기 정산"));
    expect(parent.hasAttribute("disabled")).toBe(true);
    expect(linksPanel().getByText("상위 업무 아래의 하위 요청입니다.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "하위 요청" } });
    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.parent_task_id).toBe("task-0");
  });

  /**
   * SPEC-001 U-13 — **프로젝트를 고른 뒤에만 선행이 열린다.**
   *
   * 선행은 같은 묶음 안에서만 말이 되는 관계라, 묶음이 없으면 고를 목록 자체가 없다.
   */
  it("프로젝트를 고르기 전에는 선행 업무 대신 무엇이 먼저인지 말한다", async () => {
    renderModal();
    openPanel("업무 연결");
    const preceding = await linksPanel().findByRole("group", { name: "선행 업무" });
    expect(within(preceding).getByText("프로젝트를 먼저 선택하면 그 프로젝트의 업무 중에서 고를 수 있습니다.")).toBeTruthy();
    expect(within(preceding).queryByRole("table")).toBeNull();
  });

  it("프로젝트를 고르면 그 프로젝트에서 읽을 수 있는 업무만 후보다", async () => {
    renderModal();
    openPanel("업무 연결");
    await linksPanel().findByLabelText("프로젝트");
    await pickProject("AX 고도화");

    const table = await linksPanel().findByRole("table", { name: "선행 업무" });
    expect(await within(table).findByRole("checkbox", { name: "1분기 정산" })).toBeTruthy();
    // 다른 묶음(프로젝트 없음)의 업무는 이 목록에 없다.
    expect(within(table).queryByRole("checkbox", { name: "2분기 전망치 취합" })).toBeNull();
  });

  it("고른 선행은 후보에서 빠지고 칩으로 서며 preceding_task_ids 로 실려 나간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "선행을 단 업무" } });
    openPanel("업무 연결");
    await linksPanel().findByLabelText("프로젝트");
    await pickProject("AX 고도화");

    const table = await linksPanel().findByRole("table", { name: "선행 업무" });
    fireEvent.click(await within(table).findByRole("checkbox", { name: "1분기 정산" }));
    // 이미 고른 것은 후보에서 빠진다 — 빼는 길은 칩의 「빼기」다 (U-13).
    expect(within(table).queryByRole("checkbox", { name: "1분기 정산" })).toBeNull();
    const chip = linksPanel().getByRole("button", { name: /1분기 정산/ });

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    const [, extra] = vi.mocked(api.createDirectTask).mock.calls[0];
    // **실제로 저장된다** — 「아직 저장되지 않습니다」류의 안내도 남기지 않는다.
    expect(extra?.preceding_task_ids).toEqual(["task-0"]);
    expect(extra?.project_id).toBe("p-1");
    expect(panelOf("links").textContent).not.toContain("아직 저장되지 않습니다");
    expect(chip).toBeTruthy();
  });

  it("선행이 남아 있으면 프로젝트를 바꾸지 못한다", async () => {
    renderModal();
    openPanel("업무 연결");
    await linksPanel().findByLabelText("프로젝트");
    await pickProject("AX 고도화");
    const table = await linksPanel().findByRole("table", { name: "선행 업무" });
    fireEvent.click(await within(table).findByRole("checkbox", { name: "1분기 정산" }));

    // 누른 뒤에 막지 않는다 — 고를 수 없게 해 두고 왜인지 말한다 (U-13).
    expect(linksPanel().getByLabelText("프로젝트").hasAttribute("disabled")).toBe(true);
    expect(linksPanel().getByText("선행업무를 먼저 비워야 프로젝트를 바꿀 수 있습니다.")).toBeTruthy();
  });

  it("업무 연결 판의 제목·라벨은 한 벌의 글자다 — 선행 업무 제목이 기준이다", async () => {
    renderModal();
    openPanel("업무 연결");
    await linksPanel().findByLabelText("상위 업무");
    const fields = panelOf("links").querySelector(".scax-links-fields") as HTMLElement;
    expect(fields).toBeTruthy();

    const headings = [...fields.querySelectorAll(":scope > * > legend, :scope > * > .scax-field__label, :scope > .scax-field-row > * > legend, :scope > .scax-field-row > * > .scax-field__label")]
      .map((node) => node.textContent);
    expect(headings).toEqual(["상위 업무", "프로젝트", "참고 업무", "선행 업무"]);
  });
});

/**
 * WORK-003 정정 — **기본 정보의 갈래별 필드와 라벨.**
 *
 * 두 갈래가 같은 판을 쓰지만 같은 칸을 다른 이름으로 부른다: 「업무 제목」↔「요청할 업무」,
 * 「마감일」↔「희망 기한」, 「업무 내용」↔「요청 내용」. 담당 후보는 요청에만 있고, 참조자와
 * 결재자는 둘 다 있다. 그 목록이 조용히 어긋나지 않게 여기서 한 번에 못박는다.
 */
describe("기본 정보의 갈래별 필드", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("내 업무: 업무 제목 · 시작일 · 마감일 · 업무 내용 · 참조자 · 결재자", () => {
    renderModal();
    const basic = within(panelOf("basic"));
    expect(basic.getByLabelText("업무 제목")).toBeTruthy();
    expect(basic.getByRole("button", { name: "시작일 달력 열기" })).toBeTruthy();
    expect(basic.getByRole("button", { name: "마감일 달력 열기" })).toBeTruthy();
    expect(basic.getByLabelText("업무 내용")).toBeTruthy();
    expect(basic.getByRole("group", { name: "참조자" })).toBeTruthy();
    expect(basic.getByLabelText("결재자")).toBeTruthy();
    // 내 업무는 만드는 순간 내 업무다 — 담당을 고를 자리가 없다.
    expect(basic.queryByLabelText("담당 후보")).toBeNull();
    expect(basic.queryByLabelText("요청할 업무")).toBeNull();
    expect(basic.queryByRole("button", { name: "희망 기한 달력 열기" })).toBeNull();
  });

  it("요청 업무: 요청할 업무 · 시작일 · 희망 기한 · 담당 후보 · 요청 내용 · 참조자 · 결재자", () => {
    renderModal();
    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    const basic = within(panelOf("basic"));
    expect(basic.getByLabelText("요청할 업무")).toBeTruthy();
    expect(basic.getByRole("button", { name: "시작일 달력 열기" })).toBeTruthy();
    expect(basic.getByRole("button", { name: "희망 기한 달력 열기" })).toBeTruthy();
    expect(basic.getByLabelText("담당 후보")).toBeTruthy();
    expect(basic.getByLabelText("요청 내용")).toBeTruthy();
    expect(basic.getByRole("group", { name: "참조자" })).toBeTruthy();
    expect(basic.getByLabelText("결재자")).toBeTruthy();
    expect(basic.queryByLabelText("업무 제목")).toBeNull();
    expect(basic.queryByRole("button", { name: "마감일 달력 열기" })).toBeNull();
    /* **차례도 확정이다** — 요청은 «무엇을 · 언제까지 · 누구에게» 로 읽힌다. 담당 후보가 두 날짜
       앞에 서 있던 때가 있어, 여기서 자리를 글자가 아니라 DOM 순서로 못박는다. */
    const order = Array.from(panelOf("basic").querySelectorAll(".scax-field__label")).map((node) => node.textContent);
    expect(order).toEqual(["요청할 업무", "시작일", "희망 기한", "담당 후보", "요청 내용", "결재자"]);
    /* WORK-003 정정 — **상태·요청자 카드는 없다.** 둘 다 생성 입력값이 아니라 서버가 정하는
       값이고(요청자는 부르는 사람, 상태는 「판단 대기」), 카드로 내면 고칠 수 있는 값처럼 읽힌다. */
    expect(basic.queryByText("상태")).toBeNull();
    expect(basic.queryByText("판단 대기")).toBeNull();
    expect(basic.queryByText("요청자")).toBeNull();
    expect(basic.queryByText("민아")).toBeNull();
    // 두 줄을 걷고 남은 빈 표도 세우지 않는다.
    expect(panelOf("basic").querySelector(".meta-grid")).toBeNull();
  });

  it("요청 payload 에 status·requester_id 가 실리지 않는다 — 서버가 정하는 값이다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderModal();
    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    const sent = JSON.stringify(vi.mocked(api.createWorkRequest).mock.calls[0][2]);
    expect(sent).not.toContain("status");
    expect(sent).not.toContain("state");
    expect(sent).not.toContain("requester");
  });

  it("갈래를 오가도 적어 둔 값이 남는다 — 라벨만 바뀐다", () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "갈래를 오간 제목" } });
    const toggle = () => within(screen.getByRole("tablist", { name: "생성 유형" }));

    fireEvent.click(toggle().getByRole("tab", { name: "요청 업무" }));
    expect((screen.getByLabelText("요청할 업무") as HTMLInputElement).value).toBe("갈래를 오간 제목");
    fireEvent.click(toggle().getByRole("tab", { name: "내 업무" }));
    expect((screen.getByLabelText("업무 제목") as HTMLInputElement).value).toBe("갈래를 오간 제목");
  });
});

/**
 * 새 발주 1·2 — **기본 정보의 두 사람 칸.**
 *
 * 참조자는 계약이 받는 자리(요청)에서만 실려 나가고, 결재자는 아직 어디에도 실리지 않는다.
 * 둘 다 «저장되는가» 를 화면이 먼저 말한다.
 */
describe("기본 정보의 참조자와 결재자", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("참조자는 여럿 고르고, 요청으로 보낼 때 cc_member_ids 로 간다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderModal();
    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });

    const cc = screen.getByRole("group", { name: "참조자" });
    fireEvent.click(within(cc).getByRole("checkbox", { name: "소라" }));
    fireEvent.click(within(cc).getByRole("checkbox", { name: "유나" }));

    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.cc_member_ids).toEqual(["sora", "yuna"]);
  });

  /**
   * WORK-003 Phase 4 (SPEC-001 U-6-a) — **참조자는 두 갈래 모두 저장된다.**
   *
   * 「내 업무로 만들면 함께 저장되지 않습니다」라고 적어 둔 때가 있었다. `cc_member_ids` 가 공통
   * 한 벌로 묶이면서 그 문장은 사실이 아니게 됐고, 사실이 아닌 안내는 고른 값을 버리는 것만큼 나쁘다.
   */
  it("업무 갈래에서도 참조자가 저장된다 — 틀린 안내를 남기지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    renderModal();
    const cc = screen.getByRole("group", { name: "참조자" });
    expect(within(cc).queryByText(/내 업무로 만들면 함께 저장되지 않습니다/)).toBeNull();
    expect(within(cc).getByText(/두 갈래 모두 함께 저장됩니다/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "참조자를 단 업무" } });
    fireEvent.click(within(cc).getByRole("checkbox", { name: "소라" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.cc_member_ids).toEqual(["sora"]);
  });

  /**
   * WORK-003 정정 · SPEC-001 §4 — **결재자는 두 갈래 모두 `approver_id` 로 나간다.**
   *
   * 한때 여기 「요청 갈래는 OQ-M 이 닫힐 때까지 422 일 수 있다」는 메모가 있었다. **그 미결은
   * 닫혔다** — 요청 생성이 `approver_id` 를 받아 `valid_approver` 로 검증하고, 요청 조회도 같은
   * 이름으로 낸다. 못박는 것은 그대로다: **두 갈래 payload 에 그 키가 실린다.**
   */
  it("업무 갈래의 결재자는 approver_id 로 실려 나간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "결재자를 고른 업무" } });
    fireEvent.click(screen.getByLabelText("결재자"));
    fireEvent.click(await screen.findByRole("option", { name: "소라" }));
    expect(screen.getByLabelText("결재자").textContent).toContain("소라");
    // 「아직 저장되지 않는다」는 이 갈래의 말이 아니다.
    expect(screen.queryByText(/아직 저장되지 않습니다/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.approver_id).toBe("sora");
  });

  it("요청 갈래의 결재자도 approver_id 로 실려 나간다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderModal();
    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    fireEvent.click(screen.getByLabelText("결재자"));
    fireEvent.click(await screen.findByRole("option", { name: "소라" }));
    // 「이 갈래에서는 저장되지 않는다」는 더 이상 사실이 아니다 — 그 안내도 함께 걷었다.
    expect(screen.queryByText(/아직 저장되지 않습니다/)).toBeNull();

    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.approver_id).toBe("sora");
  });

  /**
   * 최종 프레임 6 — **자료 판은 두 갈래 모두 서고, 두 갈래 모두 저장된다** (WORK-003).
   *
   * 한때 요청에서 이 판을 내렸고(붙일 자리가 없다는 이유로), 다시 세운 뒤에는 「저장되지 않습니다」를
   * 판이 말했다. 요청 자료 계약이 열리면서 그 안내도 끝났다 — 이제 요청도 내 업무와 같은 **두
   * 단계**로 붙는다. 바뀌지 않은 것은 하나다: **생성 payload 는 한 글자도 늘지 않는다.**
   */
  it("두 갈래 모두 자료 탭과 판이 서고, 요청 payload 에는 자료가 없다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderModal();
    expect(screen.getByRole("tab", { name: "자료" })).toBeTruthy();

    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    expect(screen.getByRole("tab", { name: "자료" })).toBeTruthy();
    expect(document.getElementById("create-panel-materials")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "자료" }));
    expect(screen.getByLabelText("파일 추가")).toBeTruthy();
    expect(screen.getByLabelText("링크 주소")).toBeTruthy();
    // 「저장되지 않습니다」는 더 이상 사실이 아니다 — 그 경고도 함께 걷었다.
    expect(within(screen.getByLabelText("첨부파일")).queryByRole("alert")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "기본 정보" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(JSON.stringify(vi.mocked(api.createWorkRequest).mock.calls[0][2])).not.toContain("material");
    expect(api.uploadTaskMaterial).not.toHaveBeenCalled();
  });

  it("자료 판을 보다가 갈래를 바꿔도 그 판에 그대로 있다 — 판이 사라지지 않는다", () => {
    renderModal();
    fireEvent.click(screen.getByRole("tab", { name: "자료" }));
    expect(screen.getByRole("tab", { name: "자료" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    // 읽던 자리를 뺏지 않는다 — 두 갈래가 같은 탭 한 벌을 쓰는 이유가 이것이다.
    expect(screen.getByRole("tab", { name: "자료" }).getAttribute("aria-selected")).toBe("true");
    expect(document.getElementById("create-panel-materials")?.hasAttribute("hidden")).toBe(false);
  });

  it("요청 갈래에도 시작일이 서고 그대로 실려 나간다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderModal();
    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });

    // 요청 생성 입력은 원래 이 값을 받고 있었고 화면이 접고 있었을 뿐이다 (U-6-a).
    fireEvent.click(screen.getByRole("button", { name: "시작일 달력 열기" }));
    fireEvent.click(screen.getByRole("group", { name: "시작일" }).querySelector('[data-date="2026-09-20"]') as HTMLElement);

    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.start_date).toBe("2026-09-20");
  });
});
