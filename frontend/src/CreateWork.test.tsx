import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  createDirectTask: vi.fn(),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  attachTaskMaterialLink: vi.fn(),
  attachTaskMaterialReference: vi.fn(),
  getTasks: vi.fn(),
  listProjects: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  reassignTask: vi.fn(),
  taskMaterialContentUrl: () => "",
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  amendWorkRequest: vi.fn(),
}));

import * as api from "./api";
import { CreateWorkDrawer } from "./WorkModals";

const jiho = { id: "jiho", display_name: "지호 (팀장)", role: "manager" } as never;

function renderDrawer(projectCandidates: Array<{ project_id: string; name: string }> = []) {
  const onCreated = vi.fn();
  const rendered = render(
    <CreateWorkDrawer
      assigneeCandidates={[jiho]}
      assignCandidates={[jiho]}
      canCreateRequest
      canCreateTask
      ccCandidates={[]}
      onClose={vi.fn()}
      onCreated={onCreated}
      onError={vi.fn()}
      ownerName="민아"
      projectCandidates={projectCandidates as never}
    />,
  );
  return { ...rendered, onCreated };
}

const addStep = (text: string) => {
  const steps = screen.getByLabelText("시작 단계");
  fireEvent.change(within(steps).getByLabelText("추가할 단계"), { target: { value: text } });
  fireEvent.click(within(steps).getByRole("button", { name: "단계 추가" }));
};

describe("만들 수 있는 것이 한 가지뿐일 때 (D10)", () => {
  afterEach(cleanup);

  it("요청만 가능하면 토글 없이 「업무 요청」으로 연다 — 한 칸짜리 세그먼트를 두지 않는다", () => {
    render(
      <CreateWorkDrawer
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    expect(screen.getByRole("dialog", { name: "업무 요청" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "생성 유형" })).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.getByText("동료가 수락해야 그 사람의 업무가 됩니다. 희망 기한을 함께 보낼 수 있습니다.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "업무 요청 보내기" })).toBeTruthy();
  });

  it("업무만 가능하면 「업무 추가」로 연다", () => {
    render(
      <CreateWorkDrawer
        assigneeCandidates={[]}
        canCreateRequest={false}
        canCreateTask
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    expect(screen.getByRole("dialog", { name: "업무 추가" })).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("둘 다 가능하면 지금 그대로 — 이름은 「새 업무 추가」이고 토글이 선다", () => {
    renderDrawer();
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["업무", "요청"]);
  });
});

describe("writing down the first steps with the work", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("sends the steps a person listed, in their order, with a new task", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "단계까지 아는 업무" } });
    addStep("자료 모으기");
    addStep("초안 쓰기");
    // What was typed is visible before anything is sent, and can be taken back out.
    const steps = screen.getByLabelText("시작 단계");
    expect(within(steps).getAllByRole("listitem").map((row) => row.textContent)).toEqual([
      expect.stringContaining("자료 모으기"),
      expect.stringContaining("초안 쓰기"),
    ]);
    addStep("잘못 적은 단계");
    fireEvent.click(within(steps).getByRole("button", { name: "잘못 적은 단계 빼기" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.checklist).toEqual(["자료 모으기", "초안 쓰기"]);
  });

  it("sends them with a request too, so the steps survive into the accepted work", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderDrawer();
    fireEvent.click(screen.getByRole("tab", { name: "요청" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    addStep("현황 파악");

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.checklist).toEqual(["현황 파악"]);
  });

  it("points the new work at earlier work chosen from what this person can read", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([
      { task_id: "task-0", title: "1분기 정산" },
      { task_id: "task-8", title: "다른 업무" },
    ] as never);
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "2분기 정산" } });

    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("checkbox", { name: "1분기 정산" }));
    expect(screen.getByText("1분기 정산")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.reference_task_ids).toEqual(["task-0"]);
  });

  it("sends the same pointers with a request, so they travel to the work it becomes", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "task-0", title: "지난 분기 보고" }] as never);
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "이번 분기 보고" } as never);
    renderDrawer();
    fireEvent.click(screen.getByRole("tab", { name: "요청" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "이번 분기 보고" } });
    fireEvent.click(screen.getByRole("button", { name: "참고 업무 연결" }));
    fireEvent.click(await screen.findByLabelText("연결할 이전 업무"));
    fireEvent.click(screen.getByRole("option", { name: "지난 분기 보고" }));
    fireEvent.click(screen.getByRole("button", { name: "연결" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.reference_task_ids).toEqual(["task-0"]);
  });

  it("sends nothing about steps when nobody wrote any", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "단계 없는 업무" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.checklist).toBeUndefined();
  });

  it("reuses the typed Task fields and sends the selected project with direct creation", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { container } = renderDrawer([{ project_id: "11111111-1111-1111-1111-111111111111", name: "AX 고도화" }]);
    expect(container.querySelector(".action-task-fields")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "프로젝트 업무" } });
    fireEvent.change(screen.getByLabelText("프로젝트"), { target: { value: "11111111-1111-1111-1111-111111111111" } });

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.project_id).toBe("11111111-1111-1111-1111-111111111111");
  });
});
