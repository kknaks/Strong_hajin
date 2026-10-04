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
import { chooseState, proposalItems, stateOptions } from "./taskDetailHarness.test-utils";

/**
 * 업무 상세의 **진행 상태 셀렉트 · 푸터 없음 · 한 줄** (SPEC-007 §2.10.5 · §2.10.1 · §2.10.4 · WORK-010 2b).
 *
 * 셀렉트의 목록은 SPEC 표 「서버 허용표 × 명령 권한」을 그대로 옮긴 것이다 — 화면이 규칙을 새로 만들지 않는다.
 * 행마다 한 줄씩 못박는다.
 */

const DERIVED = { assignment: null, approval: null, proposal: null, blocking_children: [], overdue_days: null };
const base = {
  task_id: "task-s",
  title: "상태를 고를 업무",
  state: "open",
  version: 3,
  block_reason: null,
  description: null,
  assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
  origin: null,
  derived: DERIVED,
} as unknown as DirectTask;
/** 수락된 요청 업무 — 요청자는 민아, 담당은 지호. */
const requested = {
  ...base,
  origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: null },
} as unknown as DirectTask;

function renderDrawer(task: DirectTask, props: Record<string, unknown> = {}) {
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [], references: [], children: [], predecessors: [], successors: [] } as never);
  const onTransition = vi.fn().mockResolvedValue(true);
  const onUpdate = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={vi.fn()}
      onClose={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      onTransition={onTransition}
      onUpdate={onUpdate}
      ownerName="지호"
      personaId="jiho"
      task={task}
      {...props}
    />,
  );
  return { onTransition, onUpdate };
}

beforeEach(() => {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([] as never);
  vi.mocked(api.listProjects).mockResolvedValue([] as never);
  vi.mocked(api.getTaskAssignments).mockResolvedValue(null as never);
  vi.mocked(api.getTaskProposals).mockResolvedValue({ task_id: "task-s", pending: [], history: [] } as never);
  vi.mocked(api.getActionItems).mockResolvedValue([] as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("진행 상태 셀렉트 — 상태 × 역할 (SPEC-007 §2.10.5 표)", () => {
  const cases: Array<[string, DirectTask, Record<string, unknown>, string[] | null]> = [
    ["활성 담당자 · 일반 · 시작 전", { ...base, state: "open" } as DirectTask, {}, ["시작 전", "진행 중", "완료", "업무 취소"]],
    ["활성 담당자 · 일반 · 진행 중", { ...base, state: "in_progress" } as DirectTask, {}, ["진행 중", "막힘", "완료", "업무 취소"]],
    ["활성 담당자 · 일반 · 막힘", { ...base, state: "blocked" } as DirectTask, {}, ["막힘", "진행 중", "업무 취소"]],
    ["활성 담당자 · 일반 · 완료(최종) — 재개만, 업무 취소 없음", { ...base, state: "done" } as DirectTask, {}, ["완료", "진행 중"]],
    ["활성 담당자 · 요청 · 시작 전", { ...requested, state: "open" } as DirectTask, {}, ["시작 전", "진행 중"]],
    ["활성 담당자 · 요청 · 진행 중", { ...requested, state: "in_progress" } as DirectTask, {}, ["진행 중", "막힘", "완료"]],
    ["활성 담당자 · 요청 · 막힘", { ...requested, state: "blocked" } as DirectTask, {}, ["막힘", "진행 중", "완료"]],
    ["활성 담당자 · 요청 · 완료(최종) — 재개는 요청자의 몫", { ...requested, state: "done", derived: { ...DERIVED, approval: "approved" } } as DirectTask, {}, null],
    ["요청자 · 요청 · 진행 중 — 전이가 없다", { ...requested, state: "in_progress" } as DirectTask, { personaId: "mina", viewerIsRequester: true, viewerIsRecordRequester: true }, null],
    ["요청자 · 요청 · 완료(최종) — 재개", { ...requested, state: "done", derived: { ...DERIVED, approval: "approved" } } as DirectTask, { personaId: "mina", viewerIsRequester: true, viewerIsRecordRequester: true }, ["완료", "진행 중"]],
    ["완료(확인 대기) — 셀렉트 없이 글자", { ...requested, state: "done", derived: { ...DERIVED, approval: "awaiting_review" } } as DirectTask, { personaId: "mina", viewerIsRequester: true, viewerIsRecordRequester: true }, null],
    ["완료(확인 대기) — 담당자에게도 글자", { ...base, state: "done", derived: { ...DERIVED, approval: "awaiting_review" } } as DirectTask, {}, null],
    ["취소 — 셀렉트가 없다", { ...base, state: "cancelled" } as DirectTask, {}, null],
    ["읽기 전용 입구 — 셀렉트가 없다", { ...base, state: "in_progress" } as DirectTask, { canManage: false }, null],
    ["남의 업무 — 셀렉트가 없다", { ...base, state: "in_progress" } as DirectTask, { personaId: "sora" }, null],
  ];
  for (const [name, task, props, expected] of cases) {
    it(name, async () => {
      renderDrawer(task, props);
      await waitFor(async () => expect(await stateOptions()).toEqual(expected));
      if (expected === null) {
        // 셀렉트가 없으면 상태 글자가 선다
        expect(within(screen.getByLabelText("메타 정보")).getByText(/시작 전|진행 중|막힘|완료|취소/)).toBeTruthy();
      }
    });
  }

  it("「업무 취소」는 맨 아래 빨강이다", async () => {
    renderDrawer({ ...base, state: "in_progress" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    fireEvent.click(screen.getByRole("button", { name: "진행 상태 바꾸기" }));
    const options = screen.getAllByRole("option");
    const last = options[options.length - 1];
    expect(last.textContent).toBe("업무 취소");
    expect(last.classList.contains("danger")).toBe(true);
    expect(options.slice(0, -1).some((option) => option.classList.contains("danger"))).toBe(false);
  });

  it("지금 상태를 다시 고르면 아무 일도 없다", async () => {
    const { onTransition } = renderDrawer({ ...base, state: "in_progress" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    chooseState("진행 중");
    expect(onTransition).not.toHaveBeenCalled();
  });

  it("시작·재개처럼 사유가 없는 전이는 바로 보낸다 — 셀렉트는 서버가 다시 준 값을 가리킨다", async () => {
    const { onTransition } = renderDrawer({ ...base, state: "blocked" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    chooseState("진행 중");
    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-s", version: 3 }), "resume"));
  });

  it("막힘 모달을 닫으면 아무것도 보내지 않고 셀렉트는 원래 값이며 업무 상세는 남는다", async () => {
    const { onTransition } = renderDrawer({ ...base, state: "in_progress" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    chooseState("막힘");
    await screen.findByRole("dialog", { name: "막힘 사유" });
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "막힘 사유" })).toBeNull());
    expect(screen.getByRole("dialog", { name: "업무 상세" })).toBeTruthy();
    expect(onTransition).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "진행 상태 바꾸기" }).textContent).toContain("진행 중");
  });

  it("남은 단계 확인창에서 「돌아가기」면 아무것도 보내지 않는다", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      ...base,
      state: "in_progress",
      checklist: [{ item_id: "i1", text: "남은 단계", position: 1, done: false, state: "active", version: 1, completed_by: null, completed_at: null }],
      references: [],
      children: [],
      predecessors: [],
      successors: [],
    } as never);
    const onTransition = vi.fn().mockResolvedValue(true);
    render(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onTransition={onTransition} onUpdate={vi.fn()} ownerName="지호" personaId="jiho" task={{ ...base, state: "in_progress" } as DirectTask} />,
    );
    await screen.findByText("남은 단계");
    chooseState("완료");
    fireEvent.click(await screen.findByRole("button", { name: "돌아가기" }));
    expect(onTransition).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "업무 상세" })).toBeTruthy();
  });

  it("재개 사유 모달은 선택이다 — 비워도 보낸다", async () => {
    vi.mocked(api.reopenTask).mockResolvedValue({ ...base, state: "in_progress", version: 4 } as never);
    renderDrawer({ ...base, state: "done" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    chooseState("진행 중");
    const prompt = await screen.findByRole("dialog", { name: "재개 사유" });
    fireEvent.click(within(prompt).getByRole("button", { name: "재개" }));
    await waitFor(() => expect(api.reopenTask).toHaveBeenCalledWith("task-s", 3, undefined));
  });
});

describe("푸터 · `⋯` (SPEC-007 §2.10.1 · §2.10.9)", () => {
  const states = ["open", "in_progress", "blocked", "done", "cancelled"] as const;
  for (const state of states) {
    it(`푸터가 없다 — ${state}`, async () => {
      renderDrawer({ ...base, state } as DirectTask);
      const dialog = await screen.findByRole("dialog", { name: "업무 상세" });
      expect(dialog.querySelector(".scax-modal__foot")).toBeNull();
      for (const name of ["업무 취소", "취소 제안", "조건 변경 제안", "변경 저장", "막힘", "시작", "완료 처리", "완료 보고", "재개", "닫기"]) {
        expect(within(dialog).queryByRole("button", { name })).toBeNull();
      }
      expect(dialog.querySelector(".scax-blocked-note")).toBeNull();
      expect(screen.queryByText("진행과 판단")).toBeNull();
    });
  }

  it("`⋯` 는 수락된 요청 업무의 요청자에게만 — 항목은 「취소 제안」·「조건 변경 제안」 둘", async () => {
    renderDrawer({ ...requested, state: "in_progress" } as DirectTask, { personaId: "mina", viewerIsRequester: true });
    expect(await proposalItems()).toEqual(["취소 제안", "조건 변경 제안"]);
  });

  it("`⋯` 는 담당자·읽기 전용 입구·대기 제안이 있을 때 그려지지 않는다", async () => {
    renderDrawer({ ...requested, state: "in_progress" } as DirectTask);
    expect(await proposalItems()).toBeNull();
    cleanup();
    renderDrawer({ ...requested, state: "in_progress" } as DirectTask, { personaId: "mina", viewerIsRequester: true, canManage: false });
    expect(await proposalItems()).toBeNull();
    cleanup();
    vi.mocked(api.getTaskProposals).mockResolvedValue({
      task_id: "task-s",
      pending: [{ proposal_id: "p1", kind: "cancellation", reason: "범위가 바뀌었습니다", proposed_by: "mina", payload: null }],
      history: [],
    } as never);
    renderDrawer({ ...requested, state: "in_progress" } as DirectTask, { personaId: "mina", viewerIsRequester: true });
    await screen.findByLabelText("응답 대기 제안");
    expect(await proposalItems()).toBeNull();
  });
});

describe("한 줄 — 인라인 저장 · 전이가 같은 줄에 선다 (SPEC-007 §2.10.4 · WORK-010 2b W1)", () => {
  it("제목 저장이 도는 동안 고른 전이는 그 뒤에 나가고, 저장 응답의 version 을 싣는다", async () => {
    let finishSave: (task: DirectTask) => void = () => undefined;
    const onUpdate = vi.fn().mockImplementation(() => new Promise<DirectTask>((resolve) => { finishSave = resolve; }));
    const { onTransition } = renderDrawer({ ...base, state: "in_progress" } as DirectTask, { onUpdate });
    await screen.findByLabelText("메타 정보");
    const slot = screen.getByLabelText("업무 제목");
    fireEvent.click(slot);
    slot.textContent = "새 제목";
    fireEvent.keyDown(slot, { key: "Enter" });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    // 저장 중에는 셀렉트가 잠긴다(§2.10.5 「보내는 중」)
    expect((screen.getByRole("button", { name: "진행 상태 바꾸기" }) as HTMLButtonElement).disabled).toBe(true);
    // 「저장 중…」이 제목 옆에 작게 선다 (W4)
    expect(screen.getByText("저장 중…")).toBeTruthy();
    finishSave({ ...base, state: "in_progress", title: "새 제목", version: 4 } as DirectTask);
    await waitFor(() => expect((screen.getByRole("button", { name: "진행 상태 바꾸기" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText("저장 중…")).toBeNull();
    chooseState("막힘");
    const prompt = await screen.findByRole("dialog", { name: "막힘 사유" });
    fireEvent.change(within(prompt).getByLabelText("막힘 사유"), { target: { value: "외부 회신 대기" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "막힘 처리" }));
    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ version: 4 }), "block", "외부 회신 대기"));
  });
});

describe("업무 내용 — 누른 자리에서 연다 (WORK-010 2b W3)", () => {
  it("열면 캐럿이 글 끝(또는 누른 자리)에 선다 — 맨 앞이 아니다", async () => {
    renderDrawer({ ...base, description: "첫 줄 내용" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    fireEvent.click(screen.getByRole("button", { name: "업무 내용 고치기" }));
    const area = screen.getByRole("textbox", { name: "업무 내용 고치기" }) as HTMLTextAreaElement;
    await waitFor(() => expect(document.activeElement).toBe(area));
    expect(area.selectionStart).toBe("첫 줄 내용".length);
    expect(area.selectionEnd).toBe("첫 줄 내용".length);
  });

  it("읽는 칸과 입력이 같은 활자 클래스(`.desc`)를 쓴다 — 읽는 칸은 미리 같은 상자를 쥔다", async () => {
    renderDrawer({ ...base, description: "첫 줄 내용" } as DirectTask);
    await screen.findByLabelText("메타 정보");
    const reading = screen.getByRole("button", { name: "업무 내용 고치기" });
    expect(reading.classList.contains("desc")).toBe(true);
    expect(reading.classList.contains("desc--editable")).toBe(true);
    fireEvent.click(reading);
    expect(screen.getByRole("textbox", { name: "업무 내용 고치기" }).classList.contains("desc")).toBe(true);
  });
});

describe("한 줄 — 나머지 명령도 같은 줄에 선다 (WORK-010 2b fix1 W1)", () => {
  it("완료 보고 모달을 연 채 제목을 저장해도 — 보고는 저장이 끝난 뒤 그 응답의 version 으로 나간다", async () => {
    let finishSave: (task: DirectTask) => void = () => undefined;
    const onUpdate = vi.fn().mockImplementation(() => new Promise<DirectTask>((resolve) => { finishSave = resolve; }));
    vi.mocked(api.submitTaskCompletion).mockResolvedValue({ ...requested, version: 5, delivery: { status: "awaiting_review", rounds: 1 } } as never);
    renderDrawer({ ...requested, state: "in_progress" } as DirectTask, { onUpdate });
    await screen.findByLabelText("메타 정보");
    chooseState("완료");
    const report = await screen.findByRole("dialog", { name: "완료 보고" });

    // 모달이 열린 채 머리의 제목을 고친다 — 저장이 줄에 선다
    const slot = screen.getByLabelText("업무 제목");
    fireEvent.click(slot);
    slot.textContent = "고친 제목";
    fireEvent.keyDown(slot, { key: "Enter" });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));

    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "다 했습니다" } });
    fireEvent.click(within(report).getByRole("button", { name: "보고 보내기" }));
    // 앞 저장이 끝나기 전에는 보고가 나가지 않는다
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(api.submitTaskCompletion).not.toHaveBeenCalled();

    finishSave({ ...requested, state: "in_progress", title: "고친 제목", version: 4 } as DirectTask);
    await waitFor(() => expect(api.submitTaskCompletion).toHaveBeenCalledWith("task-s", 4, { summary: "다 했습니다", output_material_ids: [] }));
  });

  it("제안에 동의하는 명령도 마지막 응답의 version 을 싣는다", async () => {
    vi.mocked(api.getTaskProposals).mockResolvedValue({
      task_id: "task-s",
      pending: [{ proposal_id: "p1", kind: "cancellation", reason: "범위가 바뀌었습니다", proposed_by: "mina", payload: null }],
      history: [],
    } as never);
    vi.mocked(api.respondTaskProposal).mockResolvedValue({ task_version: 5 } as never);
    const onUpdate = vi.fn().mockResolvedValue({ ...requested, title: "고친 제목", version: 4 } as DirectTask);
    renderDrawer({ ...requested, state: "in_progress" } as DirectTask, { onUpdate });
    const box = await screen.findByLabelText("응답 대기 제안");
    const slot = screen.getByLabelText("업무 제목");
    fireEvent.click(slot);
    slot.textContent = "고친 제목";
    fireEvent.keyDown(slot, { key: "Enter" });
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));

    fireEvent.click(within(box).getByRole("button", { name: "동의" }));
    const prompt = await screen.findByRole("dialog", { name: "제안 동의" });
    fireEvent.click(within(prompt).getByRole("button", { name: "동의" }));
    await waitFor(() => expect(api.respondTaskProposal).toHaveBeenCalledWith("task-s", "p1", 4, { agree: true, reason: undefined }));
  });
});

describe("담당 후보와 제안 대상의 이름 (WORK-010 2b fix1 W2)", () => {
  it("후보는 상세를 연 동안 한 번만 읽고, 「변경 제안 중」의 이름은 후보 목록에서 찾는다", async () => {
    vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([{ id: "yuna", display_name: "유나 (대표)" }] as never);
    vi.mocked(api.reassignTask).mockResolvedValue({ assignment_id: "as-2", assignee_id: "yuna" } as never);
    renderDrawer({ ...base, state: "in_progress" } as DirectTask, { canAssign: true });
    await screen.findByRole("button", { name: "담당 변경 제안" });
    await waitFor(() => expect(api.getTaskAssignmentCandidates).toHaveBeenCalledTimes(1));

    // 제안을 보내면 셀렉트가 사라진다(대기 제안) — 담당 관계를 다시 읽으면 대기 중이다
    vi.mocked(api.getTaskAssignments).mockResolvedValue({ task_id: "task-s", current: null, pending: { assignee_id: "yuna", decline_reason: null }, history: [] } as never);
    fireEvent.click(screen.getByRole("button", { name: "담당 변경 제안" }));
    fireEvent.click(screen.getByRole("option", { name: "유나" }));
    const modal = await screen.findByRole("dialog", { name: "담당자 변경" });
    expect(modal.textContent).toContain("유나에게 담당 변경을 제안합니다");
    fireEvent.click(within(modal).getByRole("button", { name: "변경" }));
    // personas 를 넘기지 않은 화면에서도 이름은 후보 목록에서 온다 — 날 id 도 빈칸도 아니다
    await waitFor(() => expect(within(screen.getByLabelText("메타 정보")).getByText("유나에게 변경 제안 중")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "담당 변경 제안" })).toBeNull();

    // 셀렉트가 사라지고(대기 제안) 상세를 다시 읽은 뒤에도 후보는 다시 묻지 않았다
    expect(api.getTaskAssignmentCandidates).toHaveBeenCalledTimes(1);
  });

  it("이름을 어디서도 못 찾으면 날 id 가 아니라 「새 담당 후보」다", async () => {
    vi.mocked(api.getTaskAssignments).mockResolvedValue({ task_id: "task-s", current: null, pending: { assignee_id: "ghost-42", decline_reason: null }, history: [] } as never);
    renderDrawer({ ...base, state: "in_progress" } as DirectTask);
    const meta = await screen.findByLabelText("메타 정보");
    await waitFor(() => expect(within(meta).getByText("새 담당 후보에게 변경 제안 중")).toBeTruthy());
    expect(meta.textContent).not.toContain("ghost-42");
    // `task.assign` 이 없는 화면은 후보를 묻지 않는다
    expect(api.getTaskAssignmentCandidates).not.toHaveBeenCalled();
  });
});
