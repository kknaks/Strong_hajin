import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  createDirectTask: vi.fn(),
  submitTaskCompletion: vi.fn(),
  getTasks: vi.fn(),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
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
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";

const parent: DirectTask = { task_id: "task-1", title: "분기 마감", state: "in_progress", version: 3, block_reason: null };

const child = {
  task_id: "task-2",
  title: "매출 집계",
  state: "in_progress" as const,
  due_date: "2026-10-10",
  assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
};

function renderDrawer(detail: Record<string, unknown>, onOpenTask = vi.fn()) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...parent, checklist: [], references: [], ...detail } as never);
  const onError = vi.fn();
  const onChanged = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={onError}
      onNotice={vi.fn()}
      onOpenTask={onOpenTask}
      onTransition={vi.fn()}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={parent}
    />,
  );
  return { onError, onChanged, onOpenTask };
}

describe("하위 업무", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("lists the parts of the work with who holds each and where it stands", async () => {
    const { onOpenTask } = renderDrawer({ children: [child], child_progress: { done: 0, total: 1 } });
    const section = await screen.findByLabelText("하위 업무");
    expect(within(section).getByText("0/1")).toBeTruthy();
    const row = within(section).getByRole("listitem");
    expect(row.textContent).toContain("매출 집계");
    expect(row.textContent).toContain("진행 중");
    expect(row.textContent).toContain("지호");
    expect(row.textContent).toContain("2026/10/10");

    fireEvent.click(within(row).getByRole("button", { name: "매출 집계 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-2");

    // Steps and subtasks are different things, in different places.
    expect(screen.getByLabelText("체크리스트")).toBeTruthy();
    expect(within(screen.getByLabelText("체크리스트")).queryByRole("button", { name: "하위 업무 추가" })).toBeNull();
  });

  it("creates a part of this work rather than a free-standing task", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ ...child, task_id: "task-3", title: "비용 정리" } as never);
    const { onChanged } = renderDrawer({ children: [], child_progress: { done: 0, total: 0 } });
    const section = await screen.findByLabelText("하위 업무");
    expect(within(section).getByText(/하위 업무가 없습니다/)).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: "하위 업무 추가" }));
    fireEvent.change(within(section).getByLabelText("하위 업무 제목"), { target: { value: "  비용 정리 " } });
    fireEvent.click(within(section).getByRole("button", { name: "만들기" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledWith("비용 정리", { parent_task_id: "task-1" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(within(section).getByText("비용 정리")).toBeTruthy();
  });

  it("shows a part what it belongs to, and does not offer parts of a part", async () => {
    const { onOpenTask } = renderDrawer({
      parent: { task_id: "task-0", title: "분기 마감", state: "in_progress" },
      children: [],
      child_progress: { done: 0, total: 0 },
    });
    const chip = await screen.findByLabelText("상위 업무");
    expect(chip.textContent).toContain("분기 마감");
    fireEvent.click(within(chip).getByRole("button", { name: "분기 마감 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-0");
    expect(screen.queryByLabelText("하위 업무")).toBeNull();
  });
});
