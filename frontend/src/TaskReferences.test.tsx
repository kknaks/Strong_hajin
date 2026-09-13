import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "./viewModels";

vi.mock("./api", () => ({
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
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
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
}));

import * as api from "./api";
import { TaskDetailDrawer } from "./WorkModals";

const task: DirectTask = { task_id: "task-1", title: "2분기 정산", state: "open", version: 3, block_reason: null };

const reference = {
  reference_id: "ref-1",
  created_by: "mina",
  created_at: "2026-09-06T00:00:00Z",
  task: { task_id: "task-0", title: "1분기 정산", state: "done", due_date: "2026-06-30", assignee: { member_id: "mina", display_name: "민아 (구성원)" } },
};

function renderDrawer(references: unknown[], onOpenTask = vi.fn()) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [], references } as never);
  vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "task-9", title: "다른 업무" }] as never);
  const onError = vi.fn();
  const onChanged = vi.fn();
  const onClose = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={onClose}
      onError={onError}
      onNotice={vi.fn()}
      onOpenTask={onOpenTask}
      onTransition={vi.fn()}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
    />,
  );
  return { onError, onChanged, onOpenTask, onClose };
}

describe("참고 업무", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('holds the current task while its selected file is uploading', async () => {
    let reject!: (error: Error) => void;
    vi.mocked(api.uploadTaskMaterial).mockReturnValue(new Promise((_, fail) => { reject = fail; }));
    const { onOpenTask, onClose, onError } = renderDrawer([reference]);
    const file = await screen.findByLabelText('참고 자료 파일');
    fireEvent.change(file, { target: { files: [new File(['original'], 'source.txt')] } });
    await waitFor(() => expect(api.uploadTaskMaterial).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '1분기 정산 열기' }));
    expect(onOpenTask).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    reject(new Error('파일 업로드 실패'));
    await waitFor(() => expect(onError).toHaveBeenCalledWith('파일 업로드 실패'));
    fireEvent.click(screen.getByRole('button', { name: '1분기 정산 열기' }));
    expect(onOpenTask).toHaveBeenCalledWith('task-0');
  });

  it("shows the earlier work this one points at, and opens it inside the product", async () => {
    const { onOpenTask } = renderDrawer([reference]);
    const section = await screen.findByLabelText("참고 업무");
    const row = within(section).getByRole("listitem");
    expect(row.textContent).toContain("1분기 정산");
    expect(row.textContent).toContain("완료"); // the state, in the words the product uses
    expect(row.textContent).toContain("2026/06/30");

    fireEvent.click(within(row).getByRole("button", { name: "1분기 정산 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-0");
  });

  it("says a reference exists even when its work cannot be opened, and nothing else about it", async () => {
    renderDrawer([{ ...reference, task: null }]);
    const section = await screen.findByLabelText("참고 업무");
    const row = within(section).getByRole("listitem");
    expect(row.textContent).toContain("볼 수 없는 업무");
    expect(row.textContent).not.toContain("1분기 정산");
    expect(within(row).queryByRole("button", { name: /열기/ })).toBeNull();
  });

  it("connects earlier work chosen from what this person can read, and settles the version", async () => {
    vi.mocked(api.addTaskReference).mockResolvedValue({ ...reference, reference_id: "ref-2", task_version: 4 } as never);
    const { onChanged } = renderDrawer([]);
    const section = await screen.findByLabelText("참고 업무");
    expect(within(section).getByText(/연결된 업무가 없습니다/)).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: "업무 연결" }));
    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());
    fireEvent.click(await within(section).findByLabelText("연결할 업무"));
    // 목록은 포털로 body 에 선다 (DS-18) — 구획 안이 아니라 화면에서 찾는다
    fireEvent.click(await screen.findByRole("option", { name: "다른 업무" }));
    fireEvent.click(within(section).getByRole("button", { name: "연결" }));

    await waitFor(() => expect(api.addTaskReference).toHaveBeenCalledWith("task-1", "task-9"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(within(section).getByText("1분기 정산")).toBeTruthy();
  });

  it("lets go of a reference without pretending it deleted the work", async () => {
    vi.mocked(api.releaseTaskReference).mockResolvedValue({ reference_id: "ref-1", task_version: 4 } as never);
    renderDrawer([reference]);
    const section = await screen.findByLabelText("참고 업무");
    fireEvent.click(within(section).getByRole("button", { name: "1분기 정산 연결 해제" }));
    await waitFor(() => expect(api.releaseTaskReference).toHaveBeenCalledWith("task-1", "ref-1"));
    await waitFor(() => expect(within(section).queryByText("1분기 정산")).toBeNull());
  });
});
