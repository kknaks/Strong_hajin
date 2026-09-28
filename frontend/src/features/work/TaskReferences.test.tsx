import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
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
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  getTasks: vi.fn(),
  // 업무 상세가 프로젝트 «이름»을 이 목록에서 맞춘다 (SPEC-007 §2.4.4).
  listProjects: vi.fn().mockResolvedValue([]),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  updateTask: vi.fn(),
  releaseSuccessor: vi.fn(),
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

import * as api from "../../lib/api";
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
    const row = await within(section).findByRole("listitem");
    expect(row.textContent).toContain("1분기 정산");
    expect(row.textContent).toContain("완료"); // the state, in the words the product uses
    // 줄에 내는 것은 «상태 · 담당» 둘이다 — 기한은 시안이 그 줄에 두지 않는다 (§2.4.1 · `:210`).
    expect(row.textContent).not.toContain("2026/06/30");
    expect(row.textContent).toContain("민아");

    fireEvent.click(within(row).getByRole("button", { name: "1분기 정산 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-0");
  });

  it("says a reference exists even when its work cannot be opened, and nothing else about it", async () => {
    renderDrawer([{ ...reference, task: null }]);
    /* 구획(`참고 업무`)은 상세를 읽기 «전에» 이미 서 있다 — 그 자리를 기다린 것과 «그 안의 값» 이
       도착한 것은 다른 사건이다. 목록 자체가 올 때까지 기다린다. */
    const section = await screen.findByLabelText("참고 업무");
    const row = await within(section).findByRole("listitem");
    expect(row.textContent).toContain("볼 수 없는 업무");
    expect(row.textContent).not.toContain("1분기 정산");
    expect(within(row).queryByRole("button", { name: /열기/ })).toBeNull();
  });

  /**
   * **참고를 잇는 자리가 「연결 편집」으로 옮겼다** (SPEC-007 §2.8 · WORK-007 F-3).
   *
   * 예전에는 참고 칸 머리에 「업무 연결」이 따로 있었다. 이제 관계 여섯을 **한 자리에서** 고치고
   * 참고는 그중 한 칸이다 — 칸마다 명령이 다르므로(참고는 전용 명령 둘) **저장도 칸마다 따로**다.
   */
  it("「연결 편집」의 참고 칸에서 이전 업무를 잇는다", async () => {
    vi.mocked(api.addTaskReference).mockResolvedValue({ ...reference, reference_id: "ref-2", task_version: 4 } as never);
    const { onChanged } = renderDrawer([]);
    const section = await screen.findByLabelText("참고 업무");
    expect(within(section).getByText(/연결된 참고 업무가 없습니다/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());
    const editing = await screen.findByLabelText("참고 업무");
    fireEvent.click(await within(editing).findByLabelText("참고 업무 추가"));
    // 목록은 포털로 body 에 선다 (DS-18) — 구획 안이 아니라 화면에서 찾는다
    fireEvent.click(await screen.findByRole("option", { name: "다른 업무" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.addTaskReference).toHaveBeenCalledWith("task-1", "task-9"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // **낙관적 갱신이 없다** — 성공하면 상세를 다시 읽고 그 값으로 그린다.
    await waitFor(() => expect(api.getTask).toHaveBeenCalledTimes(2));
  });

  it("「연결 편집」의 해제가 업무를 지우지 않고 연결만 끊는다", async () => {
    vi.mocked(api.releaseTaskReference).mockResolvedValue({ reference_id: "ref-1", task_version: 4 } as never);
    renderDrawer([reference]);
    await screen.findByLabelText("참고 업무");

    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    const editing = await screen.findByLabelText("참고 업무");
    fireEvent.click(await within(editing).findByRole("button", { name: "1분기 정산 해제" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.releaseTaskReference).toHaveBeenCalledWith("task-1", "ref-1"));
  });

  /**
   * **거절은 그 칸 «아래»에 서버 문장 그대로** 선다 (SPEC-007 §2.8.3 · OQ-707 ②).
   *
   * HTTP 본문에 기계용 `code` 가 없으므로 화면이 코드로 갈라 문장을 고르지 않는다 —
   * 저장이 여러 칸을 건드리는데 문장이 한 자리에만 서면 **어느 칸이 거절됐는지** 알 수 없다.
   */
  it("저장이 거절되면 그 칸 아래에 서버 문장이 그대로 선다", async () => {
    vi.mocked(api.addTaskReference).mockRejectedValue(new Error("이미 연결된 업무입니다."));
    renderDrawer([]);
    await screen.findByLabelText("참고 업무");

    fireEvent.click(screen.getByRole("button", { name: "연결 편집" }));
    const editing = await screen.findByLabelText("참고 업무");
    fireEvent.click(await within(editing).findByLabelText("참고 업무 추가"));
    fireEvent.click(await screen.findByRole("option", { name: "다른 업무" }));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    const message = await within(await screen.findByLabelText("참고 업무")).findByRole("alert");
    expect(message.textContent).toBe("이미 연결된 업무입니다.");
    // 거절이면 **편집을 닫지 않는다** — 고칠 자리가 그대로 남아야 한다.
    expect(screen.getByRole("button", { name: "저장" })).toBeTruthy();
  });
});
