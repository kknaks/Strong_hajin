import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkRequest } from "./viewModels";

vi.mock("./api", () => ({
  getMyWork: vi.fn(),
  getTasks: vi.fn(),
  getActionInbox: vi.fn(),
  getActionItems: vi.fn(),
  getActionItem: vi.fn(),
  runActionCommand: vi.fn(),
  getActions: vi.fn(),
  getWorkRequests: vi.fn(),
  getTaskAssignmentInbox: vi.fn(),
  getSentTaskAssignments: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  addWorkRequestComment: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  resubmitWorkRequest: vi.fn(),
  updateTask: vi.fn(),
  transitionDirectTask: vi.fn(),
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  acceptTaskAssignment: vi.fn(),
  declineTaskAssignment: vi.fn(),
  decideAction: vi.fn(),
  getTaskMaterials: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  taskMaterialContentUrl: () => "",
}));

import * as api from "./api";
import { MyWorkPage } from "./MyWorkPage";

const request = (overrides: Partial<WorkRequest> & { request_id: string; title: string }): WorkRequest => ({
  state: "pending",
  version: 1,
  task_id: null,
  assignment_state: null,
  conditions: null,
  ...overrides,
});

// One server-authorized list; every projection below is a filter over it.
const requests: WorkRequest[] = [
  request({ request_id: "to-me", title: "내게 온 검토 요청", requester_id: "jiho", assignee_id: "mina" }),
  request({ request_id: "to-me-done", title: "이미 판단한 요청", requester_id: "jiho", assignee_id: "mina", state: "accepted" }),
  request({ request_id: "by-me", title: "내가 보낸 요청", requester_id: "mina", assignee_id: "jiho", state: "negotiating", conditions: { note: "기한을 늦춰 주세요" } }),
  request({ request_id: "cc-me", title: "참조로 받은 요청", requester_id: "jiho", assignee_id: "sora", cc_member_ids: ["mina"] }),
];

function renderPage(overrides: Record<string, unknown> = {}, mocks: { inbox?: WorkRequest[]; actions?: unknown[]; judgements?: unknown[] } = {}) {
  vi.mocked(api.getMyWork).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getActionInbox).mockResolvedValue([]);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue(requests);
  vi.mocked(api.getTaskAssignmentInbox).mockResolvedValue([]);
  vi.mocked(api.getSentTaskAssignments).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([]);
  if (mocks.inbox) vi.mocked(api.getActionInbox).mockResolvedValue(mocks.inbox as never);
  if (mocks.actions) vi.mocked(api.getActions).mockResolvedValue(mocks.actions as never);
  if (mocks.judgements) vi.mocked(api.getActionItems).mockResolvedValue(mocks.judgements as never);
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

async function openRelationTab() {
  const tab = await screen.findByRole("tab", { name: "요청·배정" });
  fireEvent.click(tab);
  return tab;
}

describe("work relation information architecture", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("names the tab after the relationships it holds, not after sending", async () => {
    renderPage();
    expect(await screen.findByRole("tab", { name: "요청·배정" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "보낸 업무" })).toBeNull();
  });

  it("splits the one authorized list into the four canonical relationships", async () => {
    renderPage();
    await openRelationTab();

    const toMe = within(screen.getByLabelText("내게 요청된 업무"));
    expect(toMe.getByText("내게 온 검토 요청")).toBeTruthy();
    expect(toMe.getByText("이미 판단한 요청")).toBeTruthy(); // resolved history stays visible
    expect(toMe.queryByText("내가 보낸 요청")).toBeNull();

    const byMe = within(screen.getByLabelText("내가 요청한 업무"));
    expect(byMe.getByText("내가 보낸 요청")).toBeTruthy();
    expect(byMe.queryByText("내게 온 검토 요청")).toBeNull();

    const cc = within(screen.getByLabelText("참조된 업무"));
    expect(cc.getByText("참조로 받은 요청")).toBeTruthy();
    expect(cc.queryByText("내가 보낸 요청")).toBeNull();

    // Roles are read from each row, and the viewer is named as 나 on their own side.
    const row = byMe.getByText("내가 보낸 요청").closest("tr") as HTMLElement;
    expect(within(row).getByText("지호")).toBeTruthy(); // display names are shortened by personName
    expect(within(row).getByText("나")).toBeTruthy();
  });

  it("shows the assignment section only with the capability and never mixes it with requests", async () => {
    renderPage();
    await openRelationTab();
    expect(screen.queryByLabelText("내가 배정한 업무")).toBeNull();
    cleanup();

    renderPage({ canAssignTasks: true });
    await openRelationTab();
    const assignments = within(screen.getByLabelText("내가 배정한 업무"));
    expect(assignments.getByText("내가 배정한 업무가 없습니다")).toBeTruthy();
    expect(assignments.queryByText("내가 보낸 요청")).toBeNull();
  });

  it("keeps AX Actions out of the persistent relationship tab and out of WorkRequest labelling", async () => {
    const axAction = [
      {
        action_id: "action-1",
        conversation_id: "c1",
        turn_id: "t1",
        action_type: "task.create_self",
        title: "업무 생성 확인",
        subject: "AX가 제안한 업무",
        operation_label: "업무 생성",
        preview: [],
        state: "pending",
        version: 1,
        payload_summary: "업무 생성 확인",
        result: null,
        audit_ref: null,
        commands: [{ id: "approve", label: "승인", tone: "primary" }],
      },
    ];
    // Both origins reach the one judgement ledger, each labelled by the server.
    const judgements = [
      { action_item_id: "ai-1", kind: "work_request.acceptance", status: "awaiting_review", subject: "내게 온 검토 요청", operation_label: "업무 요청", current_question: "이 업무 요청을 수락할지 결정하세요", preview: [], allowed_commands: [], submission_version: 1, waiting_on: { member_id: "mina", display_name: "민아 (구성원)" }, resource: { type: "work_request", id: "to-me" }, expected_version: 1 },
      { action_item_id: "ai-2", kind: "ax.task.create_self", status: "awaiting_review", subject: "AX가 제안한 업무", operation_label: "업무 생성", current_question: "AX가 준비한 변경을 승인할지 결정하세요", preview: [], allowed_commands: [], submission_version: 1, waiting_on: { member_id: "mina", display_name: "민아 (구성원)" }, resource: { type: "action", id: "action-1" }, expected_version: 1 },
    ];
    renderPage({}, { inbox: [requests[0]], actions: axAction, judgements });
    await screen.findByText("AX가 제안한 업무");
    const inbox = within(document.querySelector(".decision-panel") as HTMLElement);
    // A canonical WorkRequest is labelled as a request, never as an AX proposal.
    const requestCard = inbox.getByText("내게 온 검토 요청").closest(".task-card") as HTMLElement;
    expect(requestCard.querySelector(".task-card-kicker")?.textContent).toBe("업무 요청");
    expect(requestCard.querySelector(".badge.ai")).toBeNull();
    expect((inbox.getByText("AX가 제안한 업무").closest(".task-card") as HTMLElement).querySelector(".task-card-kicker")?.textContent).toBe("업무 생성");

    // The persistent relationship tab holds canonical rows only; the AX proposal stays in the decision panel.
    await openRelationTab();
    for (const label of ["내게 요청된 업무", "내가 요청한 업무", "참조된 업무"]) {
      expect(within(screen.getByLabelText(label)).queryByText("AX가 제안한 업무")).toBeNull();
    }
    expect(within(document.querySelector(".decision-panel") as HTMLElement).getByText("AX가 제안한 업무")).toBeTruthy();
  });

  it("lets the requester reach the resubmit path for a negotiating request they own", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue({
      request: requests[2],
      request_thread_id: "thread-1",
      comments: [],
      evidence: [],
      decision_item: null,
      submissions: [{ submission_id: "s1", submission_version: 1, revises_id: null, submitted_by: "mina", submitted_at: "2026-09-01T00:00:00Z", snapshot: {}, subject_version: null, diff: null }],
      review_assignments: [],
      review_decisions: [{ review_decision_id: "d1", submission_id: "s1", actor_member_id: "jiho", decision: "negotiate", reason: "기한을 늦춰 주세요", conditions: null, decided_at: "2026-09-02T00:00:00Z" }],
      activity: [],
    } as never);
    renderPage();
    await openRelationTab();

    // 내 업무 → 요청·배정 → 내가 요청한 업무 → 상세 → 내용 고쳐 재상신
    const row = within(screen.getByLabelText("내가 요청한 업무")).getByText("내가 보낸 요청").closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "상세보기" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(within(drawer).getByRole("button", { name: "내용 고쳐 재상신" })).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: "내용 고쳐 재상신" }));
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "재상신" })).toBeTruthy());
    // The existing round history is still on screen while editing.
    expect(within(drawer).getAllByText(/기한을 늦춰 주세요/).length).toBeGreaterThan(0);
  });
});
