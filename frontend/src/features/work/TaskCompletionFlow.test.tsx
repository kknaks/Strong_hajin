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
import { TaskDetailDrawer } from "./WorkModals";

const ownTask: DirectTask = {
  task_id: "task-own",
  title: "내가 잡은 업무",
  state: "in_progress",
  version: 2,
  block_reason: null,
} as DirectTask;

const requestedTask: DirectTask = {
  task_id: "task-req",
  title: "요청받은 업무",
  state: "in_progress",
  version: 4,
  block_reason: null,
  origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "jiho", display_name: "지호 (팀장)" } },
} as DirectTask;

function renderDetail(task: DirectTask, extra: Record<string, unknown> = {}, detail: Record<string, unknown> = {}) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [], references: [], children: [], ...detail } as never);
  const onTransition = vi.fn().mockResolvedValue(true);
  const onChanged = vi.fn();
  const onError = vi.fn();
  const onNotice = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={onError}
      onNotice={onNotice}
      onTransition={onTransition}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
      {...extra}
    />,
  );
  return { onTransition, onChanged, onError, onNotice };
}

/**
 * 4차 발주 3·4·5 — **가운데 모달 · 막힘 사유 · 완료의 두 갈래.**
 *
 * 「완료」가 갈래를 갖는 이유는 계약이 그렇기 때문이다: 요청 업무의 직접 완료는 서버가 막고
 * (`이 업무는 요청자의 확인이 필요합니다`), 완료 보고 → 요청자 확인만이 그 업무를 끝낸다.
 */
describe("task detail: one modal, one reason field, two ways to finish", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("opens as a centre modal, not as a right-hand drawer", async () => {
    renderDetail(ownTask);
    const dialog = await screen.findByRole("dialog", { name: "업무 상세" });
    expect(dialog.classList.contains("scax-modal")).toBe(true);
    expect(dialog.classList.contains("scax-drawer")).toBe(false);
    expect(document.querySelector(".scax-drawer-overlay")).toBeNull();
  });

  it("finishes plain work straight away — no report is asked for", async () => {
    const { onTransition } = renderDetail(ownTask);
    await screen.findByLabelText("체크리스트");
    expect(screen.queryByRole("button", { name: "완료 보고" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "완료 처리" }));
    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-own" }), "complete"));
    expect(screen.queryByRole("dialog", { name: "완료 보고" })).toBeNull();
  });

  it("asks requested work for a report in its own modal, and keeps 보내기 shut until something is written", async () => {
    vi.mocked(api.submitTaskCompletion).mockResolvedValue({ ...requestedTask, version: 5, delivery: { status: "awaiting_review", rounds: 1 } } as never);
    const { onNotice } = renderDetail(requestedTask);
    await screen.findByLabelText("체크리스트");
    expect(screen.queryByRole("button", { name: "완료 처리" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "완료 보고" }));
    const report = await screen.findByRole("dialog", { name: "완료 보고" });
    const send = within(report).getByRole("button", { name: "보고 보내기" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);

    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "초안을 붙여 두었습니다" } });
    expect((within(report).getByRole("button", { name: "보고 보내기" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(within(report).getByRole("button", { name: "보고 보내기" }));
    await waitFor(() =>
      expect(api.submitTaskCompletion).toHaveBeenCalledWith("task-req", 4, { summary: "초안을 붙여 두었습니다", output_material_ids: [] }),
    );
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(expect.stringContaining("확인을 기다립니다")));
  });

  it("OQ-203: unfinished children block the report too, and say which ones", async () => {
    const { onError } = renderDetail(requestedTask, {}, {
      children: [{ task_id: "child-1", title: "자료 정리", state: "in_progress" }],
    });
    await screen.findByLabelText("체크리스트");
    await waitFor(() => expect(screen.getByLabelText("완료를 막는 하위")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "완료 보고" }));
    const report = await screen.findByRole("dialog", { name: "완료 보고" });
    expect(within(report).getByLabelText("보고를 막는 하위")).toBeTruthy();
    expect(within(report).getByText("자료 정리")).toBeTruthy();
    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "다 했습니다" } });
    // 쓴 것이 있어도 보내지 못한다 — 하위가 먼저 끝나야 한다.
    expect((within(report).getByRole("button", { name: "보고 보내기" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.submitTaskCompletion).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("shows the reporter they are waiting, and the requester the decision that ends it", async () => {
    const submitted = { ...requestedTask, state: "done", derived: { approval: "awaiting_review" } } as DirectTask;

    // 보고를 낸 쪽 — 기다린다는 사실만 읽힌다.
    const reporter = renderDetail(submitted);
    await screen.findByLabelText("완료 확인 대기");
    expect(screen.queryByRole("button", { name: "완료 인정" })).toBeNull();
    reporter;
    cleanup();
    vi.clearAllMocks();

    // 요청자 — 같은 자리에 판단이 선다. 명령은 예전부터 있던 판단 항목 그대로다.
    vi.mocked(api.getActionItems).mockResolvedValue([
      {
        action_item_id: "delivery-1",
        kind: "task.delivery",
        status: "awaiting_review",
        subject: "요청받은 업무",
        operation_label: "업무 결과 확인",
        current_question: "요청한 결과가 충족됐는지 확인하세요",
        preview: [],
        allowed_commands: [{ id: "accept", label: "완료 인정", tone: "primary" }, { id: "request_changes", label: "보완 요청", tone: "neutral", requires_reason: true }],
        submission_version: 1,
        waiting_on: { member_id: "jiho", display_name: "지호 (팀장)" },
        resource: { type: "task", id: "task-req" },
        expected_version: 5,
      },
    ] as never);
    vi.mocked(api.runActionCommand).mockResolvedValue({} as never);
    const requester = renderDetail(submitted, { viewerIsRecordRequester: true, personaId: "jiho" });
    const notice = await screen.findByLabelText("완료 확인 대기");
    fireEvent.click(await within(notice).findByRole("button", { name: "완료 인정" }));
    await waitFor(() => expect(api.runActionCommand).toHaveBeenCalledWith("delivery-1", "accept", { expected_version: 5 }));
    await waitFor(() => expect(requester.onChanged).toHaveBeenCalled());
  });

  it("the requester can send it back — the reason is required and travels with the command", async () => {
    const submitted = { ...requestedTask, state: "done", derived: { approval: "awaiting_review" } } as DirectTask;
    vi.mocked(api.getActionItems).mockResolvedValue([
      {
        action_item_id: "delivery-1",
        kind: "task.delivery",
        status: "awaiting_review",
        subject: "요청받은 업무",
        operation_label: "업무 결과 확인",
        current_question: "요청한 결과가 충족됐는지 확인하세요",
        preview: [],
        allowed_commands: [{ id: "accept", label: "완료 인정", tone: "primary" }],
        submission_version: 1,
        waiting_on: { member_id: "jiho", display_name: "지호 (팀장)" },
        resource: { type: "task", id: "task-req" },
        expected_version: 5,
      },
    ] as never);
    vi.mocked(api.runActionCommand).mockResolvedValue({} as never);
    renderDetail(submitted, { viewerIsRecordRequester: true, personaId: "jiho" });
    const notice = await screen.findByLabelText("완료 확인 대기");
    fireEvent.click(await within(notice).findByRole("button", { name: "보완 요청" }));
    const prompt = await screen.findByRole("dialog", { name: "보완 요청 사유" });
    const confirm = within(prompt).getByRole("button", { name: "보완 요청" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(within(prompt).getByLabelText("무엇이 더 필요한가"), { target: { value: "수치 근거가 빠졌습니다" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "보완 요청" }));
    await waitFor(() =>
      expect(api.runActionCommand).toHaveBeenCalledWith("delivery-1", "request_changes", { expected_version: 5, reason: "수치 근거가 빠졌습니다" }),
    );
  });

  it("blocked work asks for a reason in a field that fits it, and refuses an empty one", async () => {
    const { onTransition } = renderDetail(ownTask);
    await screen.findByLabelText("체크리스트");
    fireEvent.click(screen.getByRole("button", { name: "막힘" }));

    const section = await screen.findByLabelText("막힘 사유 입력");
    const submit = within(section).getByRole("button", { name: "막힘 처리" }) as HTMLButtonElement;
    // 공백이면 비활성이다 — 눌러도 아무 일이 없는 단추를 두지 않는다.
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(onTransition).not.toHaveBeenCalled();

    const field = within(section).getByLabelText("막힘 사유") as HTMLInputElement;
    // 입력은 한 줄을 통째로 쓴다 — 단추에 밀려 잘리던 자리였다.
    expect(field.className).toContain("scax-block-reason__input");
    fireEvent.change(field, { target: { value: "법무 검토를 기다립니다" } });
    expect((within(section).getByRole("button", { name: "막힘 처리" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(within(section).getByRole("button", { name: "막힘 처리" }));
    await waitFor(() =>
      expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-own" }), "block", "법무 검토를 기다립니다"),
    );
  });
});
