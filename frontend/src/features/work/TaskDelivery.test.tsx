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
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";

const base: DirectTask = {
  task_id: "task-1",
  title: "요청받은 업무",
  state: "in_progress",
  version: 4,
  block_reason: null,
  origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "mina", display_name: "민아 (구성원)" } } as never,
};

const material = {
  material_id: "m1", binding_id: "binding-m1",
  task_id: "task-1",
  kind: "output" as const,
  name: "최종 보고서",
  content_type: "text/uri-list",
  size_bytes: 0,
  uploaded_by: "jiho",
  created_at: "2026-09-06T00:00:00Z",
  removed_at: null,
  source_kind: "external_link",
  url: "https://docs.example.com/report",
  mutable_source: true,
  integrity_ref: "observed:2026-09-06T00:00:00Z",
  extraction: null,
};

function renderDrawer(task: DirectTask, materials: unknown[] = []) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue(materials as never);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [], references: [] } as never);
  const onTransition = vi.fn();
  const onChanged = vi.fn();
  const onError = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={onError}
      onNotice={vi.fn()}
      onTransition={onTransition}
      onUpdate={vi.fn()}
      ownerName="지호"
      task={task}
    />,
  );
  return { onTransition, onChanged, onError };
}

describe("handing requested work back", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("asks for a report instead of offering to close work someone else asked for", async () => {
    const { onTransition } = renderDrawer({ ...base, delivery: null } as DirectTask);
    await screen.findByLabelText("체크리스트");
    expect(screen.queryByRole("button", { name: "완료 처리" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "완료 보고" }));
    // Nothing is reported until there is something to say.
    fireEvent.click(screen.getByRole("button", { name: "보고 보내기" }));
    expect(api.submitTaskCompletion).not.toHaveBeenCalled();
    expect(onTransition).not.toHaveBeenCalled();
  });

  it("sends the summary and the outputs the reporter picked", async () => {
    vi.mocked(api.submitTaskCompletion).mockResolvedValue({ ...base, state: "done", derived: { approval: "awaiting_review" }, version: 5 } as never);
    const { onChanged } = renderDrawer({ ...base, delivery: null } as DirectTask, [material]);
    await screen.findByLabelText("체크리스트");

    fireEvent.click(screen.getByRole("button", { name: "완료 보고" }));
    const form = screen.getByLabelText("완료 보고");
    fireEvent.change(within(form).getByLabelText("결과 요약"), { target: { value: "보고서를 올렸습니다" } });
    fireEvent.click(within(form).getByRole("checkbox", { name: "최종 보고서" }));
    fireEvent.click(within(form).getByRole("button", { name: "보고 보내기" }));

    await waitFor(() =>
      expect(api.submitTaskCompletion).toHaveBeenCalledWith("task-1", 4, {
        summary: "보고서를 올렸습니다",
        output_material_ids: ["m1"],
      }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("says the work is waiting on the person who asked, and offers nothing to press", async () => {
    /* 제출이 성공하면 **밖으로는 `done` + `derived.approval=awaiting_review`** 다 —
       `completion_submitted` 는 외부 계약에 없다 (SPEC-003 §4 · SPEC-001 §4 State). */
    renderDrawer({
      ...base,
      state: "done",
      derived: { assignment: null, approval: "awaiting_review", proposal: null, blocking_children: [], overdue_days: null },
      delivery: { action_item_id: "d1", status: "awaiting_review", rounds: 1, reported_by: "jiho", reported_at: "2026-09-06T01:00:00Z", summary: "1차 결과", last_reason: null },
    } as DirectTask);
    const banner = await screen.findByLabelText("완료 확인 대기");
    expect(banner.textContent).toContain("민아");
    expect(banner.textContent).toContain("1차 결과");
    expect(screen.queryByRole("button", { name: "완료 보고" })).toBeNull();
    expect(screen.queryByRole("button", { name: "완료 처리" })).toBeNull();
  });

  it("shows what was still missing so the work can carry on", async () => {
    renderDrawer({
      ...base,
      delivery: { action_item_id: "d1", status: "awaiting_revision", rounds: 1, reported_by: "jiho", reported_at: "2026-09-06T01:00:00Z", summary: "1차 결과", last_reason: "지난달 수치가 빠졌습니다" },
    } as DirectTask);
    const banner = await screen.findByLabelText("보완 필요");
    expect(banner.textContent).toContain("지난달 수치가 빠졌습니다");
    // The next report is another round of the same question.
    expect(screen.getByRole("button", { name: "완료 보고" })).toBeTruthy();
  });
});
