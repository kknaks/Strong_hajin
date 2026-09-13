import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkRequest } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  getMyWork: vi.fn(),
  getTask: vi.fn(),
  getTaskHistory: vi.fn(),
  getTasks: vi.fn(),
  getActionItems: vi.fn(),
  getActionItem: vi.fn(),
  runActionCommand: vi.fn(),
  getActions: vi.fn(),
  getWorkRequests: vi.fn(),
  getWorkRequest: vi.fn(),
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
  generateDailyReportDraft: vi.fn(),
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

// One server-authorized list; every projection below is a filter over it.
const requests: WorkRequest[] = [
  request({ request_id: "to-me", title: "내게 온 검토 요청", requester_id: "jiho", assignee_id: "mina" }),
  request({ request_id: "to-me-done", title: "이미 판단한 요청", requester_id: "jiho", assignee_id: "mina", state: "accepted" }),
  request({ request_id: "by-me", title: "내가 보낸 요청", requester_id: "mina", assignee_id: "jiho", state: "negotiating", conditions: { note: "기한을 늦춰 주세요" } }),
  request({ request_id: "cc-me", title: "참조로 받은 요청", requester_id: "jiho", assignee_id: "sora", cc_member_ids: ["mina"] }),
];

function renderPage(overrides: Record<string, unknown> = {}, mocks: { actions?: unknown[]; judgements?: unknown[]; work?: unknown[]; requests?: unknown[] } = {}) {
  vi.mocked(api.getMyWork).mockResolvedValue([]);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTaskHistory).mockResolvedValue([] as never);
  vi.mocked(api.getTask).mockImplementation(async (taskId) => (mocks.work ?? []).find((row) => (row as { task_id: string }).task_id === taskId) as never);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue(requests);
  vi.mocked(api.getWorkRequest).mockImplementation(async (requestId) => requests.find((row) => row.request_id === requestId) as never);
  vi.mocked(api.getSentTaskAssignments).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([]);
  if (mocks.actions) vi.mocked(api.getActions).mockResolvedValue(mocks.actions as never);
  if (mocks.judgements) vi.mocked(api.getActionItems).mockResolvedValue(mocks.judgements as never);
  if (mocks.work) vi.mocked(api.getMyWork).mockResolvedValue(mocks.work as never);
  if (mocks.requests) vi.mocked(api.getWorkRequests).mockResolvedValue(mocks.requests as never);
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
  return { ...render(<RailHost {...(props as unknown as Parameters<typeof MyWorkPage>[0])} />), props };
}

/**
 * 바퀴 5b: 좌·우 레일은 화면이 «셸의 AppBody 슬롯» 에 등록해 그린다(K-6).
 * 페이지만 떼어 렌더하면 그 슬롯이 없어 레일이 안 보이므로, 셸이 하는 일만 흉내 내는 얇은 집을 둔다.
 */
function RailHost(props: Parameters<typeof MyWorkPage>[0]) {
  const [rails, setRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
  return (
    <>
      {rails.left}
      <MyWorkPage {...props} onRegisterRails={setRails} />
      {rails.right}
    </>
  );
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

  it("keeps the 참조 section standing when nothing has been referenced yet", async () => {
    // 참조는 이 제품이 가진 관계 하나이지, 하나라도 있어야 생기는 자리가 아니다. 옆의 두 덩어리처럼 비어 있어도
    // 자리를 지켜야 아직 참조로 받은 요청이 없는 사람도 그런 자리가 있다는 것을 안다.
    renderPage({}, { requests: [] });
    await openRelationTab();

    const cc = within(screen.getByLabelText("참조된 업무"));
    expect(cc.getByText("참조된 업무가 없습니다")).toBeTruthy();
    expect(screen.getByLabelText("받은 업무")).toBeTruthy();
    expect(screen.getByLabelText("보낸 업무")).toBeTruthy();
  });

  it("splits the one authorized list into the four canonical relationships", async () => {
    renderPage();
    await openRelationTab();

    const toMe = within(screen.getByLabelText("받은 업무"));
    expect(toMe.getByText("내게 온 검토 요청")).toBeTruthy();
    expect(toMe.getByText("이미 판단한 요청")).toBeTruthy(); // resolved history stays visible
    expect(toMe.queryByText("내가 보낸 요청")).toBeNull();

    const byMe = within(screen.getByLabelText("보낸 업무"));
    expect(byMe.getByText("내가 보낸 요청")).toBeTruthy();
    expect(byMe.queryByText("내게 온 검토 요청")).toBeNull();

    const cc = within(screen.getByLabelText("참조된 업무"));
    expect(cc.getByText("참조로 받은 요청")).toBeTruthy();
    expect(cc.queryByText("내가 보낸 요청")).toBeNull();

    // Each list names the other party, from the side the reader is on — never a fixed role column.
    const row = byMe.getByText("내가 보낸 요청").closest("tr") as HTMLElement;
    expect(within(row).getByText("지호")).toBeTruthy(); // display names are shortened by personName
    expect(within(row).queryByText("나")).toBeNull(); // the reader is not their own counterpart
    expect(within(screen.getByLabelText("보낸 업무")).getByText("담당자")).toBeTruthy();
    expect(within(screen.getByLabelText("받은 업무")).getByText("보낸 사람")).toBeTruthy();
  });

  it("shows the assignment section only with the capability and never mixes it with requests", async () => {
    renderPage();
    await openRelationTab();
    expect(screen.queryByLabelText("내가 지정한 업무")).toBeNull();
    cleanup();

    renderPage({ canAssignTasks: true });
    await openRelationTab();
    const assignments = within(screen.getByLabelText("내가 지정한 업무"));
    expect(assignments.getByText("내가 담당자를 지정한 업무가 없습니다")).toBeTruthy();
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
    renderPage({}, { actions: axAction, judgements });
    await screen.findByText("AX가 제안한 업무");
    const inbox = within(await screen.findByRole("region", { name: "판단이 필요한 업무" }));
    // A canonical WorkRequest is labelled as a request, never as an AX proposal.
    const requestCard = inbox.getByText("내게 온 검토 요청").closest(".scax-inbox-card") as HTMLElement;
    expect(within(requestCard).getByText("업무 요청")).toBeTruthy();
    expect((inbox.getByText("AX가 제안한 업무").closest(".scax-inbox-card") as HTMLElement).textContent).toContain("업무 생성");

    // The persistent relationship tab holds canonical rows only; the AX proposal stays in the decision panel.
    await openRelationTab();
    for (const label of ["받은 업무", "보낸 업무", "참조된 업무"]) {
      expect(within(screen.getByLabelText(label)).queryByText("AX가 제안한 업무")).toBeNull();
    }
    expect(within(screen.getByRole("region", { name: "판단이 필요한 업무" })).getByText("AX가 제안한 업무")).toBeTruthy();
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

    // 내 업무 → 요청·배정 → 보낸 업무 → 상세 → 내용 고쳐 재상신
    const row = within(screen.getByLabelText("보낸 업무")).getByText("내가 보낸 요청").closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "상세보기" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(within(drawer).getByRole("button", { name: "내용 고쳐 재상신" })).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: "내용 고쳐 재상신" }));
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "재상신" })).toBeTruthy());
    // The existing round history is still on screen while editing.
    expect(within(drawer).getAllByText(/기한을 늦춰 주세요/).length).toBeGreaterThan(0);
  });

  it("loads the permission-safe request detail before opening a relationship row", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue({
      request: requests[0],
      request_thread_id: "thread-1",
      comments: [],
      evidence: [],
      decision_item: null,
      submissions: [],
      review_assignments: [],
      review_decisions: [],
      activity: [],
    } as never);
    const detail = {
      ...requests[0],
      checklist: ["자료 확인"],
      references: [
        {
          reference_id: "reference-1",
          created_by: "jiho",
          task: { task_id: "task-1", title: "지난 분기 보고", state: "done" },
        },
      ],
    } as WorkRequest;
    renderPage();
    vi.mocked(api.getWorkRequest).mockResolvedValue(detail);
    await openRelationTab();

    const row = within(screen.getByLabelText("받은 업무")).getByText("내게 온 검토 요청").closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "상세보기" }));

    await waitFor(() => expect(api.getWorkRequest).toHaveBeenCalledWith("to-me"));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(within(drawer).getByText("자료 확인")).toBeTruthy();
    expect(within(drawer).getByText("지난 분기 보고")).toBeTruthy();
  });

  it("opens a handed-off work request id through the permission-safe detail read", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue({
      request: requests[0], request_thread_id: "thread-1", comments: [], evidence: [], decision_item: null,
      submissions: [], review_assignments: [], review_decisions: [], activity: [],
    } as never);
    const onRequestFocusHandled = vi.fn();
    renderPage({ focusWorkRequestId: "to-me", onRequestFocusHandled });

    await waitFor(() => expect(api.getWorkRequest).toHaveBeenCalledWith("to-me"));
    expect(await screen.findByRole("dialog", { name: "업무 요청 상세" })).toBeTruthy();
    expect(onRequestFocusHandled).toHaveBeenCalledTimes(1);
  });
});

describe("what the list says about dates", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the start date a task actually has, and says nothing when it has none", async () => {
    const started = {
      task_id: "t1",
      title: "시작일이 있는 업무",
      state: "in_progress",
      version: 1,
      block_reason: null,
      start_date: "2026-09-01",
      due_date: null,
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };
    const undated = { ...started, task_id: "t2", title: "시작일이 없는 업무", start_date: null };
    renderPage({}, { work: [started, undated] });

    /* 바퀴 5a J-6: 목록이 시안대로 5열이 되면서 «시작일 열» 이 빠졌다. 값은 사라지지 않았고
       상세로 자리를 옮겼을 뿐이라(거기서는 고칠 수도 있다), 같은 사실을 그 자리에서 검사한다. */
    fireEvent.click(await screen.findByText("시작일이 있는 업무"));
    expect((await screen.findByLabelText("시작일")).textContent).toContain("2026-09-01");
    fireEvent.click(screen.getByRole("button", { name: "상세 닫기" }));

    // A task nobody scheduled has no start date. The day it was created is not one.
    fireEvent.click(await screen.findByText("시작일이 없는 업무"));
    // 아무도 잡아 주지 않은 업무에는 시작일이 없다. 만든 날은 시작일이 아니다.
    const without = await screen.findByLabelText("시작일");
    expect(without.textContent).not.toContain("2026-08-20");
    expect(without.textContent).toContain("YYYY-MM-DD");
  });

  it("판단할 것은 좌 레일 수신함에 서고, 분류로 좁혀진다 (바퀴 5b)", async () => {
    const judgements = [
      { action_item_id: "ai-1", kind: "work_request.acceptance", status: "awaiting_review", subject: "판단할 요청", operation_label: "업무 요청", current_question: "결정하세요", preview: [], allowed_commands: [], submission_version: 1, waiting_on: { member_id: "mina", display_name: "민아" }, resource: { type: "work_request", id: "r1" }, expected_version: 1 },
    ];
    renderPage({}, { judgements });
    const rail = await screen.findByRole("region", { name: "판단이 필요한 업무" });
    expect(await within(rail).findByText("판단할 요청")).toBeTruthy();
    // 분류를 좁히면 그 분류에 없는 것은 빠진다 — 목록은 레일 안에서만 움직인다.
    fireEvent.click(within(rail).getByRole("tab", { name: "조정 필요" }));
    expect(within(rail).queryByText("판단할 요청")).toBeNull();
    fireEvent.click(within(rail).getByRole("tab", { name: "전체" }));
    expect(within(rail).getByText("판단할 요청")).toBeTruthy();
  });

  it("판단할 것이 없으면 수신함이 빈 상태를 낸다", async () => {
    renderPage({}, { judgements: [] });
    const rail = await screen.findByRole("region", { name: "판단이 필요한 업무" });
    expect(await within(rail).findByText("판단할 항목이 없습니다")).toBeTruthy();
  });

});

/**
 * 바퀴 5c — 표의 «상태» 칸이 읽기 글자에서 컨트롤로 바뀐 자리다.
 *
 * 5a 가 여기에 「envelope 이 없으니 안 그린다」고 적어 두었던 것을 되돌린 것이라, 되돌린 값이 무엇인지를
 * 검사로 못 박는다: 무엇을 고를 수 있나(우리 전이 규칙) · 고르면 무엇이 불리나(기존 커맨드) ·
 * 못 고치는 사람에게는 무엇이 보이나(예전 그대로 글자).
 */
describe("표의 상태 칸 (바퀴 5c)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const task = (overrides: Record<string, unknown> = {}) => ({
    task_id: "t1",
    title: "계약서 검토",
    state: "open",
    version: 3,
    block_reason: null,
    start_date: null,
    due_date: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  });

  const pill = () => screen.getByRole("button", { name: "계약서 검토 상태" });

  it("갈 수 있는 곳만 낸다 — 시작 전 업무에는 「진행 중」 하나다", async () => {
    renderPage({}, { work: [task()] });
    fireEvent.click(await screen.findByRole("button", { name: "계약서 검토 상태" }));
    const list = await screen.findByRole("listbox", { name: "계약서 검토 상태" });
    expect(within(list).getByRole("option", { name: "시작 전" })).toBeTruthy();
    expect(within(list).getByRole("option", { name: "진행 중" })).toBeTruthy();
    // 시작 전에서 바로 완료·막힘으로 건너뛰지 않는다 — 칸반의 드래그 규칙과 같은 표를 본다
    expect(within(list).queryByRole("option", { name: "완료" })).toBeNull();
    expect(within(list).queryByRole("option", { name: "막힘" })).toBeNull();
  });

  it("고르면 기존 커맨드가 그대로 불린다 — 새 저장 경로를 만들지 않았다", async () => {
    vi.mocked(api.transitionDirectTask).mockResolvedValue(undefined as never);
    renderPage({}, { work: [task()] });
    fireEvent.click(await screen.findByRole("button", { name: "계약서 검토 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "진행 중" }));
    await waitFor(() => expect(api.transitionDirectTask).toHaveBeenCalledWith("t1", "start", 3, undefined));
  });

  it("「막힘」은 고른 즉시 보내지 않는다 — 사유를 먼저 받는다", async () => {
    vi.mocked(api.transitionDirectTask).mockResolvedValue(undefined as never);
    renderPage({}, { work: [task({ state: "in_progress" })] });
    fireEvent.click(await screen.findByRole("button", { name: "계약서 검토 상태" }));
    fireEvent.click(await screen.findByRole("option", { name: "막힘" }));
    expect(api.transitionDirectTask).not.toHaveBeenCalled();

    // 칸반이 쓰던 사유 칸 그대로다 — 상태 열이 좁아 한 줄 입력칸을 그 자리에 둘 수 없다
    const prompt = await screen.findByRole("dialog", { name: "막힘 사유" });
    fireEvent.change(within(prompt).getByLabelText("막힘 사유"), { target: { value: "법무 회신 대기" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "막힘 처리" }));
    await waitFor(() => expect(api.transitionDirectTask).toHaveBeenCalledWith("t1", "block", 3, "법무 회신 대기"));
  });

  it("칸반의 막힘 칸도 같은 사유 칸을 연다 — 표와 한 벌이다", async () => {
    renderPage({}, { work: [task({ state: "in_progress" })] });
    fireEvent.click(await screen.findByRole("tab", { name: "칸반" }));
    const card = await screen.findByText("계약서 검토");
    const column = card.closest(".kanban-column");
    const blocked = [...document.querySelectorAll(".kanban-column")].find((node) => node.textContent?.startsWith("막힘"));
    expect(column).toBeTruthy();
    fireEvent.dragStart(card.closest("[draggable]") as HTMLElement);
    fireEvent.drop(blocked as HTMLElement);
    expect(await screen.findByRole("dialog", { name: "막힘 사유" })).toBeTruthy();
    expect(api.transitionDirectTask).not.toHaveBeenCalled();
  });

  it("갈 데가 없는 상태는 컨트롤이 아니라 글자다", async () => {
    // 「완료 확인 대기」는 상대가 판단할 차례라 내가 옮길 곳이 없다 — 전이 표에 그 줄이 없다
    renderPage({}, { work: [task({ state: "completion_submitted" })] });
    await screen.findByText("계약서 검토");
    expect(screen.queryByRole("button", { name: "계약서 검토 상태" })).toBeNull();
    expect(screen.getAllByText("완료 확인 대기").length).toBeGreaterThan(0);
  });

  it("고칠 수 없는 사람에게는 예전 그대로 글자다", async () => {
    renderPage({ canManageOwnTasks: false }, { work: [task()] });
    await screen.findByText("계약서 검토");
    expect(screen.queryByRole("button", { name: "계약서 검토 상태" })).toBeNull();
    expect(screen.getAllByText("시작 전").length).toBeGreaterThan(0);
    expect(pill).toThrow();
  });
});

/**
 * 바퀴 5c — 시안의 머리 액션 「일일보고 생성」.
 *
 * 5a 는 「여기서는 못 부른다」며 이동 단추로 줄였다. 실제로는 부를 수 있어서 되돌린 자리라,
 * «정말 만들고 나서 넘어가는지» 와 «못 만들면 안 넘어가는지» 둘을 못 박는다.
 */
function HeaderHost(props: Parameters<typeof MyWorkPage>[0]) {
  const [actions, setActions] = useState<React.ReactNode>(null);
  return (
    <>
      <div>{actions}</div>
      <MyWorkPage {...props} onRegisterHeaderActions={setActions} />
    </>
  );
}

describe("머리의 일일보고 (바퀴 5c)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderHeader(overrides: Record<string, unknown> = {}) {
    vi.mocked(api.getMyWork).mockResolvedValue([]);
    vi.mocked(api.getTasks).mockResolvedValue([]);
    vi.mocked(api.getActionItems).mockResolvedValue([]);
    vi.mocked(api.getActions).mockResolvedValue([]);
    vi.mocked(api.getWorkRequests).mockResolvedValue([]);
    vi.mocked(api.getSentTaskAssignments).mockResolvedValue([]);
    vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([]);
    vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
    vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([]);
    const props = {
      personaId: "mina",
      personaName: "민아 (구성원)",
      personas: [{ id: "mina", display_name: "민아 (구성원)" }],
      canManageOwnTasks: true,
      canAssignTasks: false,
      canCreateWorkRequests: true,
      canDecideWorkRequests: true,
      canReadActions: true,
      canGenerateDailyReport: true,
      onNavigate: vi.fn(),
      onAskAboutTask: vi.fn(),
      onNotice: vi.fn(),
      onError: vi.fn(),
      onDecided: vi.fn().mockResolvedValue(true),
      ...overrides,
    };
    return { ...render(<HeaderHost {...(props as unknown as Parameters<typeof MyWorkPage>[0])} />), props };
  }

  it("정말 초안을 만들고 나서 보고 화면으로 넘긴다", async () => {
    vi.mocked(api.generateDailyReportDraft).mockResolvedValue({ draft_version: 2 } as never);
    const { props } = renderHeader();
    fireEvent.click(await screen.findByRole("button", { name: "일일보고 생성" }));
    // 날짜를 들고 부른다 — 이 화면이 이미 아는 「오늘」이다
    await waitFor(() => expect(api.generateDailyReportDraft).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.generateDailyReportDraft).mock.calls[0][0]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    await waitFor(() => expect(props.onNavigate).toHaveBeenCalledWith("report"));
  });

  it("못 만들면 넘기지 않는다 — 무엇이 안 됐는지 먼저 말한다", async () => {
    vi.mocked(api.generateDailyReportDraft).mockRejectedValue(new Error("오늘 보고는 이미 제출되었습니다."));
    const { props } = renderHeader();
    fireEvent.click(await screen.findByRole("button", { name: "일일보고 생성" }));
    await waitFor(() => expect(props.onError).toHaveBeenCalledWith("오늘 보고는 이미 제출되었습니다."));
    expect(props.onNavigate).not.toHaveBeenCalled();
  });

  it("만들 권한이 없으면 그 단추 자체가 없다", async () => {
    renderHeader({ canGenerateDailyReport: false });
    await screen.findByRole("button", { name: "업무 만들기" });
    expect(screen.queryByRole("button", { name: "일일보고 생성" })).toBeNull();
  });
});
