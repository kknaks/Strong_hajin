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

function renderPage(overrides: Record<string, unknown> = {}, mocks: { actions?: unknown[]; judgements?: unknown[]; inbox?: WorkRequest[]; work?: unknown[]; requests?: unknown[] | ((includeRemoved?: boolean) => Promise<unknown>); assigneeCandidates?: unknown[] } = {}) {
  vi.mocked(api.getMyWork).mockResolvedValue([]);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTaskHistory).mockResolvedValue([] as never);
  vi.mocked(api.getTask).mockImplementation(async (taskId) => (mocks.work ?? []).find((row) => (row as { task_id: string }).task_id === taskId) as never);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue(requests);
  vi.mocked(api.getWorkRequestInbox).mockResolvedValue(mocks.inbox ?? [requests[0]]);
  vi.mocked(api.getWorkRequest).mockImplementation(async (requestId) => requests.find((row) => row.request_id === requestId) as never);
  vi.mocked(api.getSentTaskAssignments).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([]);
  if (mocks.actions) vi.mocked(api.getActions).mockResolvedValue(mocks.actions as never);
  if (mocks.judgements) vi.mocked(api.getActionItems).mockResolvedValue(mocks.judgements as never);
  if (mocks.work) vi.mocked(api.getMyWork).mockResolvedValue(mocks.work as never);
  /* 목록 읽기를 «갈래로» 흉내 낼 수 있어야 한다 — `include_removed` 만 거절하는 서버가 W-3 의 자리다. */
  if (typeof mocks.requests === "function") vi.mocked(api.getWorkRequests).mockImplementation(mocks.requests as never);
  else if (mocks.requests) vi.mocked(api.getWorkRequests).mockResolvedValue(mocks.requests as never);
  if (mocks.assigneeCandidates) vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue(mocks.assigneeCandidates as never);
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
  // 머리 액션(「업무 만들기」)도 셸의 슬롯으로 올라간다 — 레일과 같은 규약이라 같은 집이 받는다.
  const [headerActions, setHeaderActions] = useState<React.ReactNode>(null);
  return (
    <>
      {headerActions}
      {rails.left}
      <MyWorkPage {...props} onRegisterHeaderActions={setHeaderActions} onRegisterRails={setRails} />
      {rails.right}
    </>
  );
}

/**
 * **WORK-002 v2 로 정보구조가 바뀐 자리다.**
 *
 * W1 은 「요청·배정」 한 탭 안에 받은·보낸·지정·참조 네 구획을 두었다. v2 의 탭은 **소유·종결 축**
 * 셋이고(SPEC-003 §2.1), 「받은 요청」은 탭이 아니라 **「내 업무」의 필터 칩**이다 — 수락 전 요청도
 * 응답할 자리는 내 목록에 있어야 하기 때문이다(V-9·V-10). 「참조된 업무」·「조직 업무」는 계약에
 * 그대로 있으므로 **지우지 않고** 「보낸 업무」 탭의 구획으로 남는다(M-20 미정).
 */
async function openSentTab() {
  const tab = await screen.findByRole("tab", { name: "보낸 업무" });
  fireEvent.click(tab);
  return tab;
}

async function openCcTab() {
  const tab = await screen.findByRole("tab", { name: "참조 업무" });
  fireEvent.click(tab);
  return tab;
}

describe("work relation information architecture", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("names the three tabs after ownership and closure, not after the three creating acts", async () => {
    renderPage();
    expect(await screen.findByRole("tab", { name: "내 업무" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "보낸 업무" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "완료 업무" })).toBeTruthy();
    // 「받은 요청」은 네 번째 탭이 아니다 — 「내 업무」의 칩이다.
    expect(screen.queryByRole("tab", { name: "받은 요청" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "요청·배정" })).toBeNull();
  });

  it("keeps pending requests in the inbox, outside the held task list and counts", async () => {
    renderPage();
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    expect(await within(rail).findByText("내게 온 검토 요청")).toBeTruthy();
    expect(within(screen.getByLabelText("내 업무")).queryByText("내게 온 검토 요청")).toBeNull();
    expect(screen.getByRole("button", { name: /^받은 요청/ }).textContent).toContain("0");
    expect(api.getWorkRequestInbox).toHaveBeenCalled();
  });

  it("opens the authorized request detail and sends the refusal reason", async () => {
    vi.mocked(api.decideWorkRequest).mockResolvedValue(requests[0]);
    renderPage();
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    fireEvent.click(await within(rail).findByText("내게 온 검토 요청"));
    const drawer = await screen.findByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "거절" }));
    fireEvent.change(within(drawer).getByLabelText("거절 사유"), { target: { value: "이번 주는 어렵습니다" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "거절 확정" }));
    await waitFor(() => expect(api.decideWorkRequest).toHaveBeenCalledWith("to-me", "reject", 1, "이번 주는 어렵습니다"));
    expect(api.getWorkRequest).toHaveBeenCalledWith("to-me");
  });

  it("keeps a pending inbox task out of My Work even when the general request list omits it", async () => {
    const incoming = request({ ...requests[0], task_id: "pending-task" });
    renderPage({}, { inbox: [incoming], requests: [], work: [{ task_id: "pending-task", title: incoming.title, state: "open", version: 1, block_reason: null }] });
    await within(await screen.findByRole("region", { name: "업무 요청 수신함" })).findByText(incoming.title);
    expect(within(screen.getByLabelText("내 업무")).queryByText(incoming.title)).toBeNull();
  });

  it("accepts directly from the work inbox without opening detail", async () => {
    renderPage();
    vi.mocked(api.decideWorkRequest).mockResolvedValue({ ...requests[0], state: "accepted" });
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    fireEvent.click(await within(rail).findByRole("button", { name: "수락" }));
    await waitFor(() => expect(api.decideWorkRequest).toHaveBeenCalledWith("to-me", "accept", 1, undefined));
    expect(api.getWorkRequest).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "업무 요청 상세" })).toBeNull();
  });

  it("direct inbox rejection keeps the reason on failure and retries the existing command", async () => {
    const { props } = renderPage();
    vi.mocked(api.decideWorkRequest).mockRejectedValueOnce(new Error("잠시 후 다시 시도"));
    vi.mocked(api.decideWorkRequest).mockResolvedValue(requests[0]);
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    fireEvent.click(await within(rail).findByRole("button", { name: "거절" }));
    const prompt = await screen.findByRole("dialog", { name: "거절 사유" });
    fireEvent.change(within(prompt).getByLabelText("거절 사유"), { target: { value: "일정 조율 필요" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "거절" }));
    await waitFor(() => expect(props.onError).toHaveBeenCalledWith("잠시 후 다시 시도"));
    expect((within(prompt).getByLabelText("거절 사유") as HTMLInputElement).value).toBe("일정 조율 필요");
    fireEvent.click(within(prompt).getByRole("button", { name: "거절" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "거절 사유" })).toBeNull());
    expect(api.decideWorkRequest).toHaveBeenLastCalledWith("to-me", "reject", 1, "일정 조율 필요");
  });

  it("after acceptance removes the inbox request and shows the same task in My Work", async () => {
    const incoming = request({ ...requests[0], task_id: "same-task" });
    renderPage({}, { inbox: [incoming], requests: [incoming] });
    vi.mocked(api.getWorkRequest).mockResolvedValue(incoming);
    fireEvent.click(await screen.findByText("내게 온 검토 요청"));
    const drawer = await screen.findByRole("dialog");
    expect(drawer.classList.contains("scax-modal")).toBe(true);
    const accepted = { ...incoming, state: "accepted" as const, version: 2 };
    vi.mocked(api.decideWorkRequest).mockResolvedValue(accepted);
    vi.mocked(api.getWorkRequestInbox).mockResolvedValue([]);
    vi.mocked(api.getWorkRequests).mockResolvedValue([accepted]);
    vi.mocked(api.getMyWork).mockResolvedValue([{ task_id: "same-task", title: incoming.title, state: "open", version: 2, block_reason: null, assignee: { member_id: "mina", display_name: "민아" } }]);
    fireEvent.click(within(drawer).getByRole("button", { name: "수락" }));
    await waitFor(() => expect(api.decideWorkRequest).toHaveBeenCalledWith("to-me", "accept", 1));
    expect((await within(screen.getByLabelText("내 업무")).findByText(incoming.title)).closest("[data-task-row]")?.getAttribute("data-task-row")).toBe(incoming.task_id);
    await waitFor(() => expect(within(screen.getByRole("region", { name: "업무 요청 수신함" })).queryByText(incoming.title)).toBeNull());
  });

  it("keeps requests and assignments in one 보낸 업무 table while telling them apart on the row", async () => {
    renderPage({ canAssignTasks: true }, {});
    vi.mocked(api.getSentTaskAssignments).mockResolvedValue([
      {
        assignment_id: "as-1",
        assignment_kind: "direct",
        status: "active",
        assignee_id: "sora",
        assigned_by: "mina",
        decline_reason: null,
        created_at: "2026-09-01T00:00:00Z",
        accepted_at: "2026-09-01T00:00:00Z",
        declined_at: null,
        task: { task_id: "task-5", title: "관리자가 배정한 업무", state: "in_progress", version: 1, block_reason: null },
      },
    ] as never);
    cleanup();
    renderPage({ canAssignTasks: true });
    await openSentTab();

    const table = within(screen.getByLabelText("보낸 업무"));
    expect(table.getByText("내가 보낸 요청")).toBeTruthy();
    // 요청 상태 여섯이 배지 둘로 뭉개지지 않는다 — 협의 중은 협의 중으로 읽힌다 (U-4).
    expect(table.getByText("협의 중")).toBeTruthy();
    // 받은 요청은 여기 서지 않는다 — 내가 보낸 것만 담는 탭이다.
    expect(table.queryByText("내게 온 검토 요청")).toBeNull();
  });

  it("keeps the 참조 section standing when nothing has been referenced yet", async () => {
    // 참조는 이 제품이 가진 관계 하나이지, 하나라도 있어야 생기는 자리가 아니다. M-20 이 미정이라
    // 「화면에서 지우는 것」은 제품 결정이고, 이 판은 보낸 업무 탭의 구획으로 남긴다.
    renderPage({}, { requests: [] });
    await openCcTab();

    const cc = within(screen.getByLabelText("참조 업무"));
    expect(cc.getByText("참조된 업무가 없습니다")).toBeTruthy();
  });

  it("shows the organization section only with the capability", async () => {
    renderPage();
    await openSentTab();
    expect(screen.queryByLabelText("조직 업무")).toBeNull();
    cleanup();

    renderPage({ canReadOrganizationWork: true });
    await openSentTab();
    expect(screen.getByLabelText("조직 업무")).toBeTruthy();
  });

  it("keeps AX Actions out of the work tables and out of WorkRequest labelling", async () => {
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
    // AX 판단 항목이 있어도 업무 요청 수신함에는 들어오지 않는다.
    const judgements = [
      { action_item_id: "ai-1", kind: "work_request.acceptance", status: "awaiting_review", subject: "내게 온 검토 요청", operation_label: "업무 요청", current_question: "이 업무 요청을 수락할지 결정하세요", preview: [], allowed_commands: [], submission_version: 1, waiting_on: { member_id: "mina", display_name: "민아 (구성원)" }, resource: { type: "work_request", id: "to-me" }, expected_version: 1 },
      { action_item_id: "ai-2", kind: "ax.task.create_self", status: "awaiting_review", subject: "AX가 제안한 업무", operation_label: "업무 생성", current_question: "AX가 준비한 변경을 승인할지 결정하세요", preview: [], allowed_commands: [], submission_version: 1, waiting_on: { member_id: "mina", display_name: "민아 (구성원)" }, resource: { type: "action", id: "action-1" }, expected_version: 1 },
    ];
    renderPage({}, { actions: axAction, judgements });
    await screen.findByText("내게 온 검토 요청");
    const inbox = within(await screen.findByRole("region", { name: "업무 요청 수신함" }));
    // A canonical WorkRequest is labelled as a request, never as an AX proposal.
    const requestCard = inbox.getByText("내게 온 검토 요청").closest(".scax-inbox-card") as HTMLElement;
    expect(within(requestCard).getByText("업무 요청")).toBeTruthy();
    expect(inbox.queryByText("AX가 제안한 업무")).toBeNull();
    expect(api.getActionItems).not.toHaveBeenCalled();

    // 업무 표와 수신함 모두 AX 제안을 섞지 않는다.
    await openSentTab();
    expect(within(screen.getByLabelText("보낸 업무")).queryByText("AX가 제안한 업무")).toBeNull();
    await openCcTab();
    expect(within(screen.getByLabelText("참조 업무")).queryByText("AX가 제안한 업무")).toBeNull();
    expect(within(screen.getByRole("region", { name: "업무 요청 수신함" })).queryByText("AX가 제안한 업무")).toBeNull();
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
    await openSentTab();

    // 내 업무 → 보낸 업무 → 행을 열어 상세 → 내용 고쳐 재상신
    fireEvent.click(within(screen.getByLabelText("보낸 업무")).getByText("내가 보낸 요청"));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(within(drawer).getByRole("button", { name: "내용 고쳐 재상신" })).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: "내용 고쳐 재상신" }));
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "재상신" })).toBeTruthy());
    // The existing round history is still on screen while editing.
    expect(within(drawer).getAllByText(/기한을 늦춰 주세요/).length).toBeGreaterThan(0);
  });

  it("loads the permission-safe request detail before opening a sent row", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue({
      request: requests[2],
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
      ...requests[2],
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
    await openSentTab();

    fireEvent.click(within(screen.getByLabelText("보낸 업무")).getByText("내가 보낸 요청"));

    await waitFor(() => expect(api.getWorkRequest).toHaveBeenCalledWith("by-me"));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(within(drawer).getByText("자료 확인")).toBeTruthy();
    expect(within(drawer).getByText("지난 분기 보고")).toBeTruthy();
  });

  /**
   * **목록 정리는 「숨기기」다** (F-6 · L-6 · OQ-202 의 화면 선택). 목록에서만 빠지고 이력은 남으므로
   * 「삭제」로 부르지 않는다. 그리고 그 숨김은 **서버가 기억한다** — 이 화면이 기억하는 것이라면
   * 새로고침 한 번에 사라지고, 정리한 적이 없는 것과 구별되지 않는다.
   */
  it("취소로 끝난 요청만 정리할 수 있고, 숨김은 서버 목록에서 읽어 새로고침 뒤에도 선다", async () => {
    const rows = [
      request({ request_id: "rejected", title: "거절된 요청", requester_id: "mina", assignee_id: "jiho", state: "rejected" }),
      request({ request_id: "hidden", title: "정리한 요청", requester_id: "mina", assignee_id: "jiho", state: "withdrawn", list_entry_hidden: true }),
      request({ request_id: "open", title: "아직 대기 중", requester_id: "mina", assignee_id: "jiho", state: "pending" }),
    ];
    renderPage({}, { requests: rows });
    await openSentTab();
    const table = () => within(screen.getByLabelText("보낸 업무"));

    // 서버가 숨김이라고 말한 행은 기본 목록에 없다 — 화면이 기억한 것이 아니라 응답이 그렇다.
    expect(table().queryByText("정리한 요청")).toBeNull();
    expect(table().getByText("거절된 요청")).toBeTruthy();

    // 정리는 끝난 항목에만 열린다. 아직 대기 중인 요청은 철회가 먼저다.
    const live = table().getByText("아직 대기 중").closest('[data-sent-row]') as HTMLElement;
    expect(within(live).queryByRole("button", { name: "숨기기" })).toBeNull();
    expect(within(live).getByRole("button", { name: "철회" })).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: /숨긴 항목 보기/ }));
    expect(table().getByText("정리한 요청")).toBeTruthy();
  });

  it("숨김 목록을 읽을 때 서버에 정리한 항목까지 달라고 말한다", async () => {
    renderPage();
    await waitFor(() => expect(api.getWorkRequests).toHaveBeenCalledWith(true));
  });

  /**
   * **첫 실패를 말없이 삼키지 않는다** (검수 W-3).
   *
   * 서버가 `include_removed` 를 거절하면 일반 목록으로 내려가는 것은 맞다 — 목록까지 잃을 이유가
   * 없다. 그러나 그 상태에서 「숨긴 항목 보기」는 **이 세션에만** 사는 값을 센다. 말하지 않으면
   * 사람은 정리한 것이 영속으로 남은 줄 안다.
   */
  it("숨긴 항목까지 읽지 못하면 일반 목록은 살리되 그 사실을 말한다", async () => {
    const hiddenUnsupported = async (includeRemoved?: boolean) =>
      includeRemoved ? Promise.reject(new Error("unknown query")) : requests;
    const { props } = renderPage({}, { requests: hiddenUnsupported });

    // 일반 목록은 그대로 선다 — 반쪽 실패가 목록을 빈 것으로 만들지 않는다.
    await openSentTab();
    expect(within(screen.getByLabelText("보낸 업무")).getByText("내가 보낸 요청")).toBeTruthy();
    // 그리고 경고가 나 있다.
    await waitFor(() =>
      expect(props.onError).toHaveBeenCalledWith("숨긴 항목까지 읽지 못했습니다 — 「숨긴 항목 보기」가 이 세션에만 적용됩니다."),
    );
  });

  /** 경고는 **명령 하나가 성공했다고 지워지지 않는다** — 아직 참이기 때문이다. */
  it("그 뒤 명령이 성공해도 경고가 남는다", async () => {
    const hiddenUnsupported = async (includeRemoved?: boolean) =>
      includeRemoved ? Promise.reject(new Error("unknown query")) : requests;
    vi.mocked(api.decideWorkRequest).mockResolvedValue(requests[0] as never);
    const { props } = renderPage({}, { requests: hiddenUnsupported });

    fireEvent.click(await screen.findByText("내게 온 검토 요청"));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "업무 요청 상세" })).getByRole("button", { name: "수락" }));

    await waitFor(() => expect(api.decideWorkRequest).toHaveBeenCalled());
    await waitFor(() => expect(vi.mocked(props.onError).mock.calls.at(-1)?.[0]).toContain("숨긴 항목까지 읽지 못했습니다"));
  });

  /**
   * 「다시 요청」은 **재요청**이다 — `supersedes_request_id` 를 실은 **새 요청·새 Task** (V-12).
   * 독촉(`reminders`)과 같은 단추일 수 없다. 이 줄이 그 선택을 못박는다.
   */
  it("「다시 요청」은 이전 요청을 이어 새 요청을 보낸다 — 독촉이 아니다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "new", title: "거절된 요청", state: "pending", version: 1 } as never);
    const rows = [request({ request_id: "rejected", title: "거절된 요청", requester_id: "mina", assignee_id: "jiho", state: "rejected", parent_task_id: "parent-1" })];
    renderPage({}, { requests: rows, assigneeCandidates: [{ id: "jiho", display_name: "지호 (팀장)" }] });
    await openSentTab();

    fireEvent.click(within(screen.getByLabelText("보낸 업무")).getByRole("button", { name: "다시 요청" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 요청" });
    expect(modal.textContent).toContain("이전 요청을 잇는 다시 요청입니다");
    fireEvent.click(within(modal).getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() =>
      expect(api.createWorkRequest).toHaveBeenCalledWith(
        "거절된 요청",
        "jiho",
        expect.objectContaining({ supersedes_request_id: "rejected", parent_task_id: "parent-1" }),
        expect.any(String),
      ),
    );
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

  it("renders pending and negotiating requests with the confirmed inbox categories", async () => {
    renderPage({}, { inbox: [requests[0], request({ ...requests[0], request_id: "negotiating", title: "협의할 요청", state: "negotiating" })] });
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    expect(await within(rail).findByText("협의할 요청")).toBeTruthy();
    expect(within(rail).getByText("내게 온 검토 요청")).toBeTruthy();
    expect(within(rail).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["전체", "업무", "참고"]);
  });

  it("filters CC requests into 참고 and opens a read-only request detail", async () => {
    renderPage();
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    await within(rail).findByText("참조로 받은 요청");
    fireEvent.click(within(rail).getByRole("tab", { name: "업무" }));
    expect(within(rail).getByText("내게 온 검토 요청")).toBeTruthy();
    expect(within(rail).queryByText("참조로 받은 요청")).toBeNull();
    fireEvent.click(within(rail).getByRole("tab", { name: "참고" }));
    expect(within(rail).queryByText("내게 온 검토 요청")).toBeNull();
    fireEvent.click(within(rail).getByRole("button", { name: "참조로 받은 요청" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(within(drawer).getByText("참조로 받은 요청")).toBeTruthy();
    for (const name of ["수락", "거절", "조정 요청", "남기기", "내용 수정"]) {
      expect(within(drawer).queryByRole("button", { name })).toBeNull();
    }
    expect(api.getWorkRequestTimeline).toHaveBeenCalledWith("cc-me");
  });

  it("shows empty request inbox copy", async () => {
    renderPage({}, { inbox: [], requests: [] });
    const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
    expect(await within(rail).findByText("받은 업무 요청이 없습니다")).toBeTruthy();
  });

  it("does not call the inbox endpoint without work_request.decide", async () => {
    renderPage({ canDecideWorkRequests: false }, { requests: [] });
    await screen.findByText("받은 업무 요청이 없습니다");
    expect(api.getWorkRequestInbox).not.toHaveBeenCalled();
  });

  it("retries inbox failures independently of the held task list", async () => {
    vi.mocked(api.getWorkRequestInbox).mockRejectedValueOnce(new Error("offline"));
    renderPage();
    await screen.findByText("수신함을 불러오지 못했습니다.");
    vi.mocked(api.getWorkRequestInbox).mockResolvedValue([requests[0]]);
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await screen.findByText("내게 온 검토 요청");
  });

  /* W1: 만든 것이 어디에 섰는지를 화면이 바로 보여 준다 (WORK-001 Phase 7).
     갈래는 만든 쪽이 돌려주는 사실로 고른다 — 알림 문구를 다시 읽어 알아내지 않는다. */
  it("남의 업무가 되면 「보낸 업무」로 옮겨 서고, 내 업무면 그대로 머문다", async () => {
    /* 최종 발주 3: 업무 갈래에는 담당자 칸이 없다 — 남에게 맡기는 길은 머리의 「요청」 토글이다.
       갈래는 여전히 «만든 쪽이 돌려주는 사실» 로 고른다. */
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "req-9", title: "소라에게 보낼 업무" } as never);
    renderPage({}, { assigneeCandidates: [{ id: "sora", display_name: "소라 (법무)" }] });

    fireEvent.click(await screen.findByRole("button", { name: "업무 만들기" }));
    const create = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    fireEvent.change(await screen.findByLabelText("요청할 업무"), { target: { value: "소라에게 보낼 업무" } });
    fireEvent.click(within(create).getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "소라 (법무)" }));
    fireEvent.click(within(create).getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][1]).toBe("sora");
    // 목록을 다시 읽고, 그 업무가 서는 자리로 옮겨 선다
    await waitFor(() => expect((screen.getByRole("tab", { name: "보낸 업무" }) as HTMLElement).getAttribute("aria-selected")).toBe("true"));
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

  it("offers only list and timeline views while retaining task opening", async () => {
    renderPage({}, { work: [task({ state: "in_progress" })] });
    const views = screen.getByRole("tablist", { name: "보기 방식" });
    expect(within(views).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["목록", "타임라인"]);
    fireEvent.click(within(views).getByRole("tab", { name: "타임라인" }));
    fireEvent.click(await screen.findByRole("button", { name: /계약서 검토/ }));
    expect(await screen.findByRole("dialog", { name: "업무 상세" })).toBeTruthy();
  });

  /**
   * v2: 「확인 대기」는 **상태가 아니라 `derived.approval`** 이다 — 밖으로는 `done` 이라 「완료 업무」
   * 탭에 서고, 그 안에서 **최종 완료와 다르게** 읽혀야 한다 (U-5). 상태 문자열로는 그 둘이 구별되지 않는다.
   */
  it("요청 업무의 승인 전 done 은 「완료 업무」에서 «확인 대기» 로 읽힌다 — 완료로 읽히지 않는다", async () => {
    renderPage({}, { work: [task({ state: "done", derived: { approval: "awaiting_review" } })] });
    fireEvent.click(await screen.findByRole("tab", { name: "완료 업무" }));
    const table = within(screen.getByLabelText("완료 업무"));
    expect(table.getByText("계약서 검토")).toBeTruthy();
    expect(table.getByText("확인 대기")).toBeTruthy();
    expect(table.queryByText("완료")).toBeNull();
    // 표에 상태를 옮기는 컨트롤이 없다 — 지금은 상대의 차례다.
    expect(screen.queryByRole("button", { name: "계약서 검토 상태" })).toBeNull();
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

/* ════════════════════════════════════════════════════════════════════════════
   후보 로딩 — **참조 후보는 요청 권한에 매달리지 않는다.**

   참조자(`cc_member_ids`)와 결재자(`approver_id`)는 `POST /api/tasks` 본인 갈래도 받는 값이다.
   한동안 두 목록이 한 `useEffect` 안에 있어서 `work_request.create` 가 없으면 **둘 다** 안 읽었고,
   생성 창은 후보가 비면 그 칸을 그리지 않으므로 참조자·결재자가 통째로 사라졌다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("업무만 만들 수 있는 사람의 후보 로딩", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("요청 권한이 없어도 참조 후보를 읽는다 — 담당 후보만 읽지 않는다", async () => {
    renderPage({ canManageOwnTasks: true, canCreateWorkRequests: false, canDecideWorkRequests: false });

    await waitFor(() => expect(api.getWorkRequestCcCandidates).toHaveBeenCalled());
    // 담당 후보(요청 수신자)는 정말로 요청 갈래에서만 쓰는 목록이라 그대로 닫혀 있다
    expect(api.getWorkRequestAssigneeCandidates).not.toHaveBeenCalled();
  });

  it("만들 수 있는 것이 아무것도 없으면 참조 후보도 읽지 않는다", async () => {
    renderPage({ canManageOwnTasks: false, canCreateWorkRequests: false, canDecideWorkRequests: false });

    await waitFor(() => expect(api.getMyWork).toHaveBeenCalled());
    expect(api.getWorkRequestCcCandidates).not.toHaveBeenCalled();
  });
});
