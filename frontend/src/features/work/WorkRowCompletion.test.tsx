import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  // 우 레일의 회의 절반 (증보 K23) — 업무 목록과 **다른 질의**다.
  getCalendar: vi.fn().mockResolvedValue([]),
  getMyWork: vi.fn(),
  getTask: vi.fn(),
  getTaskHistory: vi.fn(),
  getTasks: vi.fn(),
  getActionItems: vi.fn(),
  getActionItem: vi.fn(),
  runActionCommand: vi.fn(),
  getActions: vi.fn(),
  getWorkRequests: vi.fn(),
  getWorkRequestInbox: vi.fn(),
  getWorkRequest: vi.fn(),
  getSentTaskAssignments: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  getWorkRequestTimeline: vi.fn().mockResolvedValue(null),
  addWorkRequestComment: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  resubmitWorkRequest: vi.fn(),
  amendWorkRequest: vi.fn(),
  updateTask: vi.fn(),
  transitionDirectTask: vi.fn(),
  submitTaskCompletion: vi.fn(),
  generateDailyReportDraft: vi.fn(),
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  hideWorkRequestListEntry: vi.fn(),
  withdrawWorkRequest: vi.fn(),
  getTaskMaterials: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  taskMaterialContentUrl: () => "",
}));

import * as api from "../../lib/api";
import { MyWorkPage } from "./MyWorkPage";

const plainTask = {
  task_id: "task-plain",
  title: "내가 잡은 업무",
  state: "in_progress",
  version: 2,
  block_reason: null,
} as unknown as DirectTask;

const requestedTask = {
  task_id: "task-req",
  title: "요청받은 업무",
  state: "in_progress",
  version: 4,
  block_reason: null,
  origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "jiho", display_name: "지호 (팀장)" } },
} as unknown as DirectTask;

function renderPage(work: DirectTask[], detail?: Record<string, unknown>) {
  vi.mocked(api.getMyWork).mockResolvedValue(work as never);
  vi.mocked(api.getTasks).mockResolvedValue(work as never);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTaskHistory).mockResolvedValue([] as never);
  vi.mocked(api.getTask).mockResolvedValue({ ...(work[0] as object), checklist: [], references: [], children: [], ...detail } as never);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestInbox).mockResolvedValue([]);
  vi.mocked(api.getSentTaskAssignments).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([]);
  const props = {
    personaId: "mina",
    personaName: "민아 (구성원)",
    personas: [
      { id: "mina", display_name: "민아 (구성원)" },
      { id: "jiho", display_name: "지호 (팀장)" },
    ],
    canManageOwnTasks: true,
    canAssignTasks: false,
    canCreateWorkRequests: true,
    canDecideWorkRequests: true,
    canReadActions: true,
    onAskAboutTask: vi.fn(),
    onNotice: vi.fn(),
    onError: vi.fn(),
    onDecided: vi.fn().mockResolvedValue(true),
  };
  return { ...render(<MyWorkPage {...(props as unknown as Parameters<typeof MyWorkPage>[0])} />), props };
}

const row = (id: string) => document.querySelector(`[data-task-row="${id}"]`) as HTMLElement;

/**
 * 5차 발주 — **목록 행의 「완료」도 갈래를 안다.**
 *
 * 계약이 갈래를 만든다: 요청 업무의 직접 완료는 서버가 거절하고(`이 업무는 요청자의 확인이
 * 필요합니다`), 끝나는 길은 「완료 보고 → 요청자 확인」 하나다. 상세는 이미 그 갈래를 알고 있었는데
 * **행의 단추만 몰라서** 요청 업무에서도 `complete` 를 곧장 보내고 있었다 — 눌러도 늘 실패하는 단추다.
 */
describe("completing from the work list", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("finishes plain work straight from the row, as before", async () => {
    vi.mocked(api.transitionDirectTask).mockResolvedValue(undefined as never);
    renderPage([plainTask]);
    await screen.findByText("내가 잡은 업무");

    fireEvent.click(within(row("task-plain")).getByRole("button", { name: "완료" }));
    await waitFor(() => expect(api.transitionDirectTask).toHaveBeenCalledWith("task-plain", "complete", 2, undefined));
    // 일반 업무에는 물을 것이 없다 — 보고 모달을 세우지 않는다.
    expect(screen.queryByRole("dialog", { name: "완료 보고" })).toBeNull();
    expect(api.submitTaskCompletion).not.toHaveBeenCalled();
  });

  it("opens the same report modal for requested work instead of sending a transition that always fails", async () => {
    vi.mocked(api.submitTaskCompletion).mockResolvedValue({ ...requestedTask, version: 5 } as never);
    const { props } = renderPage([requestedTask]);
    await screen.findByText("요청받은 업무");

    // 라벨부터 다르다 — 이 행에서 일어나는 일이 「완료」가 아니라 「완료 보고」다.
    const button = within(row("task-req")).getByRole("button", { name: "완료 보고" });
    fireEvent.click(button);

    const report = await screen.findByRole("dialog", { name: "완료 보고" });
    expect(api.transitionDirectTask).not.toHaveBeenCalled();

    // 상세에서 연 것과 같은 자리다: 요약이 비면 보내지 못한다.
    expect((within(report).getByRole("button", { name: "보고 보내기" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "초안을 붙여 두었습니다" } });
    fireEvent.click(within(report).getByRole("button", { name: "보고 보내기" }));

    await waitFor(() =>
      expect(api.submitTaskCompletion).toHaveBeenCalledWith("task-req", 4, { summary: "초안을 붙여 두었습니다", output_material_ids: [] }),
    );
    // 성공 안내는 토스트로 나간다 — 입력 자체를 토스트가 대신하지 않는다.
    await waitFor(() => expect(props.onNotice).toHaveBeenCalledWith(expect.stringContaining("확인을 기다립니다")));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "완료 보고" })).toBeNull());
    expect(api.transitionDirectTask).not.toHaveBeenCalled();
  });

  it("keeps the written summary when the server refuses the report", async () => {
    vi.mocked(api.submitTaskCompletion).mockRejectedValue(new Error("잠시 후 다시 시도해 주세요"));
    const { props } = renderPage([requestedTask]);
    await screen.findByText("요청받은 업무");
    fireEvent.click(within(row("task-req")).getByRole("button", { name: "완료 보고" }));

    const report = await screen.findByRole("dialog", { name: "완료 보고" });
    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "다시 보낼 문장" } });
    fireEvent.click(within(report).getByRole("button", { name: "보고 보내기" }));

    await waitFor(() => expect(props.onError).toHaveBeenCalledWith("잠시 후 다시 시도해 주세요"));
    // 실패는 토스트로 말하고, 쓴 문장은 그 자리에 남는다.
    expect((within(report).getByLabelText("결과 요약") as HTMLTextAreaElement).value).toBe("다시 보낼 문장");
    expect(screen.getByRole("dialog", { name: "완료 보고" })).toBeTruthy();
  });

  it("OQ-203: an unfinished child blocks the report opened from the row too", async () => {
    renderPage([requestedTask], { children: [{ task_id: "child-1", title: "자료 정리", state: "in_progress" }] });
    await screen.findByText("요청받은 업무");
    fireEvent.click(within(row("task-req")).getByRole("button", { name: "완료 보고" }));

    const report = await screen.findByRole("dialog", { name: "완료 보고" });
    await within(report).findByLabelText("보고를 막는 하위");
    expect(within(report).getByText("자료 정리")).toBeTruthy();
    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "다 했습니다" } });
    expect((within(report).getByRole("button", { name: "보고 보내기" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.submitTaskCompletion).not.toHaveBeenCalled();
  });

  it("the status pill takes the same fork — picking 완료 on requested work asks for the report", async () => {
    vi.mocked(api.submitTaskCompletion).mockResolvedValue({ ...requestedTask, version: 5 } as never);
    renderPage([requestedTask]);
    fireEvent.click(await screen.findByRole("button", { name: "요청받은 업무 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));

    expect(api.transitionDirectTask).not.toHaveBeenCalled();
    const report = await screen.findByRole("dialog", { name: "완료 보고" });
    fireEvent.change(within(report).getByLabelText("결과 요약"), { target: { value: "상태 칸에서 보낸 보고" } });
    fireEvent.click(within(report).getByRole("button", { name: "보고 보내기" }));
    await waitFor(() =>
      expect(api.submitTaskCompletion).toHaveBeenCalledWith("task-req", 4, { summary: "상태 칸에서 보낸 보고", output_material_ids: [] }),
    );
  });

  it("the status pill still transitions plain work in place", async () => {
    vi.mocked(api.transitionDirectTask).mockResolvedValue(undefined as never);
    renderPage([plainTask]);
    fireEvent.click(await screen.findByRole("button", { name: "내가 잡은 업무 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "완료" }));
    await waitFor(() => expect(api.transitionDirectTask).toHaveBeenCalledWith("task-plain", "complete", 2, undefined));
    expect(screen.queryByRole("dialog", { name: "완료 보고" })).toBeNull();
  });
});
