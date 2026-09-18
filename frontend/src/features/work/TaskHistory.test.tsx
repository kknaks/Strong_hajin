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
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  addChecklistItem: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  attachTaskMaterialLink: vi.fn(),
  attachTaskMaterialReference: vi.fn(),
  getTasks: vi.fn(),
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
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";

const task: DirectTask = {
  task_id: "task-1",
  title: "고친 제목",
  state: "in_progress",
  version: 3,
  block_reason: null,
  created_at: "2026-09-05T00:10:00Z",
  updated_at: "2026-09-05T01:12:00Z",
} as DirectTask;

const history = {
  task_id: "task-1",
  versions: [
    { version: 1, change_kind: "task.created", actor_id: "mina", reason: null, captured_at: "2026-09-05T00:10:00Z", snapshot: {} },
    { version: 2, change_kind: "task.updated", actor_id: "mina", reason: null, captured_at: "2026-09-05T00:40:00Z", snapshot: {} },
    { version: 3, change_kind: "task.state_changed", actor_id: "jiho", reason: "자료를 기다립니다", captured_at: "2026-09-05T01:12:00Z", snapshot: {} },
  ],
  activity: [
    {
      event_kind: "task.state_changed",
      actor: { member_id: "jiho", display_name: "지호 (팀장)" },
      actor_kind: "member",
      summary: "업무 상태 open → in_progress: 고친 제목",
      reason: "자료를 기다립니다",
      version: 3,
      occurred_at: "2026-09-05T01:12:00Z",
    },
    {
      event_kind: "task.updated",
      actor: { member_id: "mina", display_name: "민아 (구성원)" },
      actor_kind: "member",
      summary: "업무 수정: 고친 제목",
      reason: null,
      version: 2,
      occurred_at: "2026-09-05T00:40:00Z",
    },
    {
      event_kind: "task.created",
      actor: { member_id: "mina", display_name: "민아 (구성원)" },
      actor_kind: "member",
      summary: "업무 생성: 처음 제목",
      reason: null,
      version: 1,
      occurred_at: "2026-09-05T00:10:00Z",
    },
  ],
};

function renderDrawer(canManage = true) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [] } as never);
  vi.mocked(api.getTaskHistory).mockResolvedValue(history as never);
  render(
    <TaskDetailDrawer
      busy={false}
      canManage={canManage}
      onClose={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      onTransition={vi.fn()}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
    />,
  );
}

describe("task history", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("is a discoverable section that is not fetched until someone asks for it", async () => {
    renderDrawer();
    const section = await screen.findByLabelText("활동·이력");
    expect(api.getTaskHistory).not.toHaveBeenCalled();
    // The summary a person reads without opening anything.
    expect(section.textContent).toContain("2026/09/05");

    fireEvent.click(within(section).getByRole("button", { name: "이력 보기" }));
    await waitFor(() => expect(api.getTaskHistory).toHaveBeenCalledWith("task-1"));
    const rows = await within(section).findAllByRole("listitem");
    expect(rows).toHaveLength(3);
    // Newest first, with a person, a time, the sentence and the reason they gave.
    expect(rows[0].textContent).toContain("지호");
    expect(rows[0].textContent).toContain("2026/09/05 10:12");
    expect(rows[0].textContent).toContain("업무 상태 open → in_progress");
    expect(rows[0].textContent).toContain("자료를 기다립니다");
    expect(rows[0].getAttribute("data-version")).toBe("3");
    expect(rows[2].textContent).toContain("민아");
    // Member ids are not what a person reads.
    expect(section.textContent).not.toContain("jiho");
  });

  it("opens what actually changed between the version before and the one a line produced", async () => {
    vi.mocked(api.getTaskHistoryDiff).mockResolvedValue({
      task_id: "task-1",
      from: 1,
      to: 2,
      changes: {
        title: { before: "처음 제목", after: "고친 제목" },
        checklist: { added: ["자료 모으기"], removed: [] },
      },
    } as never);
    renderDrawer();
    const section = await screen.findByLabelText("활동·이력");
    fireEvent.click(within(section).getByRole("button", { name: "이력 보기" }));
    const rows = await within(section).findAllByRole("listitem");

    fireEvent.click(within(rows[1]).getByRole("button", { name: "변경 내용" }));
    await waitFor(() => expect(api.getTaskHistoryDiff).toHaveBeenCalledWith("task-1", 1, 2));
    const diff = await within(rows[1]).findByRole("group", { name: "변경 내용" });
    expect(diff.textContent).toContain("제목");
    expect(diff.textContent).toContain("처음 제목");
    expect(diff.textContent).toContain("고친 제목");
    expect(diff.textContent).toContain("체크리스트");
    expect(diff.textContent).toContain("자료 모으기");
  });

  it("has nothing to compare on the first version, so it does not offer a diff", async () => {
    renderDrawer();
    const section = await screen.findByLabelText("활동·이력");
    fireEvent.click(within(section).getByRole("button", { name: "이력 보기" }));
    const rows = await within(section).findAllByRole("listitem");
    expect(within(rows[2]).queryByRole("button", { name: "변경 내용" })).toBeNull();
  });

  it("says a change came through an AX confirmation without making AX the actor", async () => {
    renderDrawer();
    const section = await screen.findByLabelText("활동·이력");
    vi.mocked(api.getTaskHistory).mockResolvedValue({
      ...history,
      activity: [
        {
          ...history.activity[0],
          event_kind: "task.checklist.added",
          summary: "체크리스트 추가: AX가 제안한 단계",
          causation: { kind: "action_item", id: "action-9" },
        },
        history.activity[1],
      ],
    } as never);
    fireEvent.click(within(section).getByRole("button", { name: "이력 보기" }));
    const rows = await within(section).findAllByRole("listitem");

    // The person is still the one who did it; the badge only says what it travelled through.
    expect(rows[0].textContent).toContain("지호");
    expect(within(rows[0]).getByText("AX를 통해")).toBeTruthy();
    expect(within(rows[1]).queryByText("AX를 통해")).toBeNull();
  });

  it("narrows to the kind of change someone is looking for, without changing what the server sent", async () => {
    renderDrawer();
    const section = await screen.findByLabelText("활동·이력");
    vi.mocked(api.getTaskHistory).mockResolvedValue({
      ...history,
      activity: [
        { ...history.activity[0], event_kind: "task.material_attached", summary: "참고 자료 등록: 설계.pdf", version: 5 },
        { ...history.activity[0], event_kind: "task.checklist.added", summary: "체크리스트 추가: 자료 모으기", version: 4 },
        ...history.activity,
      ],
    } as never);
    fireEvent.click(within(section).getByRole("button", { name: "이력 보기" }));
    expect(await within(section).findAllByRole("listitem")).toHaveLength(5);

    fireEvent.click(within(section).getByRole("button", { name: "체크리스트" }));
    const steps = within(section).getAllByRole("listitem");
    expect(steps).toHaveLength(1);
    expect(steps[0].textContent).toContain("체크리스트 추가");

    fireEvent.click(within(section).getByRole("button", { name: "내용·상태" }));
    expect(within(section).getAllByRole("listitem")).toHaveLength(3);

    fireEvent.click(within(section).getByRole("button", { name: "전체" }));
    expect(within(section).getAllByRole("listitem")).toHaveLength(5);
  });

  it("says so instead of showing an empty list when the history cannot be read", async () => {
    renderDrawer(false);
    vi.mocked(api.getTaskHistory).mockRejectedValue(new Error("이 업무를 볼 수 없습니다"));
    const section = await screen.findByLabelText("활동·이력");
    fireEvent.click(within(section).getByRole("button", { name: "이력 보기" }));
    expect(await within(section).findByText("이 업무를 볼 수 없습니다")).toBeTruthy();
  });
});
