import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkRequest } from "../../lib/viewModels";

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

const request = (overrides: Partial<WorkRequest> & { request_id: string; title: string }): WorkRequest => ({
  state: "pending",
  version: 1,
  task_id: null,
  assignment_state: null,
  conditions: null,
  ...overrides,
});

const ccRequest = request({
  request_id: "cc-1",
  title: "참조로 받은 계약 검토",
  requester_id: "jiho",
  assignee_id: "sora",
  cc_member_ids: ["mina"],
  due_date: "2026-09-30",
});
const sentRequest = request({ request_id: "sent-1", title: "내가 보낸 요청", requester_id: "mina", assignee_id: "jiho" });
const myTask = { task_id: "task-1", title: "내가 든 업무", state: "in_progress", version: 1, block_reason: null };
const doneTask = { task_id: "task-2", title: "끝난 업무", state: "done", version: 3, block_reason: null, completed_at: "2026-09-10T00:00:00Z" };

function renderPage(overrides: Record<string, unknown> = {}, mocks: { work?: unknown[]; closed?: unknown[]; detail?: unknown } = {}) {
  vi.mocked(api.getMyWork).mockResolvedValue((mocks.work ?? [myTask]) as never);
  vi.mocked(api.getTasks).mockResolvedValue((mocks.closed ?? [myTask, doneTask]) as never);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTaskHistory).mockResolvedValue([] as never);
  vi.mocked(api.getTask).mockResolvedValue((mocks.detail ?? doneTask) as never);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue([ccRequest, sentRequest]);
  vi.mocked(api.getWorkRequestInbox).mockResolvedValue([]);
  vi.mocked(api.getWorkRequest).mockImplementation(async (id) => [ccRequest, sentRequest].find((row) => row.request_id === id) as never);
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
      { id: "sora", display_name: "소라 (법무)" },
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
    ...overrides,
  };
  return { ...render(<MyWorkPage {...(props as unknown as Parameters<typeof MyWorkPage>[0])} />), props };
}

const openTab = async (name: string) => {
  fireEvent.click(await screen.findByRole("tab", { name }));
};

/**
 * 4차 발주 1·2 — **네 탭과 한 벌의 표.**
 *
 * 탭이 넷이 된 것(참조가 「보낸 업무」 안의 구획에서 자기 자리로 나온 것)과, 그 넷이 «같은 격자» 를
 * 쓴다는 것을 여기서 잡는다. 둘은 한 이야기다: 구획이던 시절의 참조 목록은 다른 골격(`<table>`)이라
 * 제목 시작 위치부터 달랐다.
 */
describe("work tabs and the one table they share", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("stands four tabs — 참조 업무 is one of them, not a section inside 보낸 업무", async () => {
    renderPage();
    for (const name of ["내 업무", "보낸 업무", "완료 업무", "참조 업무"]) {
      expect(await screen.findByRole("tab", { name })).toBeTruthy();
    }
    await openTab("보낸 업무");
    // 구획은 이동했다 — 복제가 아니라서 이 탭에는 남지 않는다.
    expect(screen.queryByLabelText("참조된 업무")).toBeNull();
    expect(within(screen.getByLabelText("보낸 업무")).queryByText(ccRequest.title)).toBeNull();
  });

  it("lists CC work in its own tab and offers nothing to accept or refuse", async () => {
    renderPage();
    await openTab("참조 업무");
    const table = within(await screen.findByLabelText("참조 업무"));
    expect(table.getByText(ccRequest.title)).toBeTruthy();
    // 참조자는 읽고 논의할 뿐이다 — 판단 명령은 담당자의 자리다.
    for (const name of ["수락", "거절", "조정 요청"]) {
      expect(table.queryByRole("button", { name })).toBeNull();
    }
    expect(table.getByText("지호 → 소라")).toBeTruthy();
  });

  /**
   * WORK-003 정정 — **상태·요청자를 «보여 주는» 자리는 그대로다.**
   *
   * 걷어 낸 것은 요청 **생성 모달**의 카드다(고칠 수 없는 값을 고를 수 있는 것처럼 세우던 자리).
   * 상세는 서버가 이미 정한 값을 읽어 내는 자리라 말이 되고, 여기서 함께 사라지면 안 된다.
   */
  it("요청 상세에는 상태와 요청자가 그대로 선다 — 걷어 낸 것은 생성 모달의 카드다", async () => {
    renderPage();
    await openTab("참조 업무");
    fireEvent.click(await screen.findByText(ccRequest.title));
    const detail = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    await waitFor(() => expect(api.getWorkRequest).toHaveBeenCalledWith("cc-1"));

    expect(within(detail).getByText("판단 대기")).toBeTruthy();
    expect(within(detail).getByText("요청자")).toBeTruthy();
    // `personName` 이 괄호 직함을 떼고 이름만 세운다.
    expect(within(detail).getByText("지호")).toBeTruthy();
    expect(detail.querySelector(".meta-grid")).toBeTruthy();
  });

  it("opens a CC row read-only — the detail carries no decision either", async () => {
    renderPage();
    await openTab("참조 업무");
    fireEvent.click(await screen.findByText(ccRequest.title));
    const detail = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    await waitFor(() => expect(api.getWorkRequest).toHaveBeenCalledWith("cc-1"));
    for (const name of ["수락", "거절", "조정 요청", "내용 수정"]) {
      expect(within(detail).queryByRole("button", { name })).toBeNull();
    }
  });

  it("keeps one centre modal when a task detail follows its source, and steps back into it", async () => {
    const sourced = {
      task_id: "task-3",
      title: "요청에서 생긴 업무",
      state: "in_progress",
      version: 1,
      block_reason: null,
      origin: { kind: "work_request", actor_role: "요청자", source: { type: "work_request", id: "sent-1", title: "출처 요청 보기" } },
    };
    renderPage({}, { work: [sourced], closed: [sourced], detail: { ...sourced, checklist: [], references: [], children: [] } });

    fireEvent.click(await screen.findByText("요청에서 생긴 업무"));
    const taskDetail = await screen.findByRole("dialog", { name: "업무 상세" });
    // 오른쪽 드로어는 이 화면에서 서지 않는다.
    expect(taskDetail.classList.contains("scax-modal")).toBe(true);
    expect(document.querySelectorAll(".scax-drawer-overlay").length).toBe(0);

    fireEvent.click(within(taskDetail).getByRole("button", { name: "출처 요청 보기" }));
    await screen.findByRole("dialog", { name: "업무 요청 상세" });
    // 겹은 그대로 하나다 — 출처를 따라가도 상자가 두 번 덮이지 않는다.
    expect(document.querySelectorAll(".scax-modal-overlay, .scax-drawer-overlay, .modal-backdrop").length).toBe(1);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "이전 상세로 돌아가기" }));
    await screen.findByRole("dialog", { name: "업무 상세" });
    expect(screen.queryByRole("dialog", { name: "업무 요청 상세" })).toBeNull();
  });

  it("draws every tab with the same grid, so titles start on the same line", async () => {
    renderPage();
    const labels = ["내 업무", "보낸 업무", "완료 업무", "참조 업무"] as const;
    for (const label of labels) {
      await openTab(label);
      const table = await screen.findByLabelText(label);
      // 같은 골격 · 같은 격자 · 같은 머리 다섯 칸.
      expect(table.classList.contains("scax-task-table")).toBe(true);
      expect(table.classList.contains("scax-task-table--nostar")).toBe(true);
      expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
      // 탭별 격자 변형은 사라졌다 — 첫 트랙을 되살리는 modifier 가 붙지 않는다.
      expect(table.className).not.toContain("scax-task-table--sent");
      expect(table.className).not.toContain("scax-task-table--done");
    }
  });

  it("stops indenting the done rows — the grouped row starts where every other title does", async () => {
    renderPage();
    await openTab("완료 업무");
    const table = await screen.findByLabelText("완료 업무");
    const row = await waitFor(() => {
      const found = table.querySelector("[data-done-row]");
      if (!found) throw new Error("완료 행이 아직 없습니다");
      return found as HTMLElement;
    });
    const title = row.querySelector(".scax-task-table__cell--title") as HTMLElement;
    expect(title).toBeTruthy();
    expect(title.className).not.toContain("scax-task-table__cell--indent");
    expect(within(row).getAllByRole("cell")).toHaveLength(5);
  });
});
