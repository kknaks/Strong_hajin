import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  getActionItems: vi.fn().mockResolvedValue([]),
  runActionCommand: vi.fn(),
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
  listProjects: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer, TaskQuickActions } from "./WorkModals";

const base = {
  task_id: "task-1",
  title: "후행 업무",
  state: "open",
  version: 2,
  block_reason: null,
} as unknown as DirectTask;

/** 선행 요약은 `preceding_task_ids` 와 **같은 순서·같은 길이**다. 볼 수 없는 것은 자리만 남는다. */
const withPreceding = (rows: Array<{ task_id: string; title: string | null; state: string | null }>, extra: Record<string, unknown> = {}) =>
  ({ ...base, predecessors: rows, preceding_task_ids: rows.map((row) => row.task_id), ...extra }) as unknown as DirectTask;

function renderDetail(task: DirectTask) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ checklist: [], references: [], children: [], ...task } as never);
  const onTransition = vi.fn().mockResolvedValue(true);
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={vi.fn()}
      onClose={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      onOpenTask={vi.fn()}
      onTransition={onTransition}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
    />,
  );
  return { onTransition };
}

/**
 * WORK-003 Phase 6 (SPEC-001 U-13 · U-14) — **막혔다는 사실과 무엇이 막는지.**
 *
 * 선행은 상위·참고와 **다른 세 번째 관계**다. 상위는 「어느 업무의 일부인가」, 참고는 맥락,
 * 선행은 「무엇이 먼저 끝나야 하는가」다 — 한 줄에 섞지 않는다.
 */
describe("선행업무 표시와 시작 게이트", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("선행이 없으면 줄 자체가 없다", async () => {
    renderDetail(base);
    await screen.findByLabelText("체크리스트");
    expect(screen.queryByLabelText("선행업무")).toBeNull();
  });

  it("선행 줄에 제목과 상태가 서고, 끝나지 않은 것이 눈에 띈다", async () => {
    renderDetail(withPreceding([
      { task_id: "p-1", title: "설계 확정", state: "in_progress" },
      { task_id: "p-2", title: "예산 승인", state: "done" },
    ]));
    const section = await screen.findByLabelText("선행업무");

    expect(within(section).getByRole("button", { name: "설계 확정 열기" })).toBeTruthy();
    expect(within(section).getByRole("button", { name: "예산 승인 열기" })).toBeTruthy();
    // 끝나지 않은 선행만 danger 로 선다 — 그것이 시작을 막는 이유다.
    const badges = section.querySelectorAll(".scax-badge--danger");
    expect(badges).toHaveLength(1);
    expect(badges[0].textContent).toBe("진행 중");
  });

  it("볼 수 없는 선행은 제목 없이 건수만 낸다 — 배열의 빈 자리가 그 건수다", async () => {
    renderDetail(withPreceding([
      { task_id: "p-9", title: null, state: null },
      { task_id: "p-8", title: null, state: null },
    ]));
    const section = await screen.findByLabelText("선행업무");
    expect(within(section).getByText("볼 수 없는 선행업무 2건")).toBeTruthy();
  });

  it("미완 선행이 있으면 [시작] 과 [완료] 가 «누르기 전에» 막히고 막는 이름이 선다", async () => {
    const { onTransition } = renderDetail(withPreceding([{ task_id: "p-1", title: "설계 확정", state: "in_progress" }]));
    await screen.findByLabelText("체크리스트");

    const start = screen.getByRole("button", { name: "시작" }) as HTMLButtonElement;
    const complete = screen.getByRole("button", { name: "완료 처리" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    // `시작 전 → 완료` 직행이 열려 있으면 그것이 시작 게이트의 우회로가 된다.
    expect(complete.disabled).toBe(true);
    fireEvent.click(start);
    expect(onTransition).not.toHaveBeenCalled();

    expect(screen.getAllByText("끝나지 않은 선행업무가 있습니다: 설계 확정").length).toBeGreaterThan(0);
  });

  it("취소된 선행은 막지 않는다 — 전부 완료·취소면 버튼이 열린다", async () => {
    const { onTransition } = renderDetail(withPreceding([
      { task_id: "p-1", title: "접은 설계", state: "cancelled" },
      { task_id: "p-2", title: "예산 승인", state: "done" },
    ]));
    await screen.findByLabelText("체크리스트");

    const start = screen.getByRole("button", { name: "시작" }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    fireEvent.click(start);
    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-1" }), "start"));
    expect(screen.queryByText(/끝나지 않은 선행업무가 있습니다/)).toBeNull();
  });

  it("이미 시작한 업무의 완료는 선행을 보지 않는다 — 막는 것은 시작이다", async () => {
    const { onTransition } = renderDetail(
      withPreceding([{ task_id: "p-1", title: "설계 확정", state: "in_progress" }], { state: "in_progress" }),
    );
    await screen.findByLabelText("체크리스트");

    const complete = screen.getByRole("button", { name: "완료 처리" }) as HTMLButtonElement;
    expect(complete.disabled).toBe(false);
    fireEvent.click(complete);
    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-1" }), "complete"));
  });

  it("목록의 행도 같은 사실을 같은 문장으로 말한다", () => {
    const onTransition = vi.fn();
    render(
      <TaskQuickActions
        busy={false}
        onTransition={onTransition}
        task={withPreceding([{ task_id: "p-1", title: "설계 확정", state: "open" }])}
      />,
    );
    expect((screen.getByRole("button", { name: "시작" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("끝나지 않은 선행업무가 있습니다: 설계 확정")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "시작" }));
    expect(onTransition).not.toHaveBeenCalled();
  });

  it("하위와 선행은 다른 축이다 — 선행을 달아도 하위 완료 규칙이 바뀌지 않는다", async () => {
    renderDetail(withPreceding([{ task_id: "p-1", title: "설계 확정", state: "done" }], {
      state: "in_progress",
      children: [{ task_id: "c-1", title: "남은 하위", state: "in_progress" }],
    }));
    await screen.findByLabelText("체크리스트");
    // 하위는 «완료» 를 막고 선행은 «시작» 을 막는다. 두 구획이 각자 선다.
    await waitFor(() => expect(screen.getByLabelText("완료를 막는 하위")).toBeTruthy());
    expect(screen.getByLabelText("선행업무")).toBeTruthy();
  });
});
