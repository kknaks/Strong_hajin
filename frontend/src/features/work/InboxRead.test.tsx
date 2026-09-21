import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import type React from "react";
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
  markWorkRequestRead: vi.fn(),
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
  listProjects: vi.fn(),
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

/** 내가 참조자인 참고 항목. 수신함에 서고 `참조 업무` 탭에도 선다. */
const ccOne = request({ request_id: "cc-1", title: "참조로 받은 계약 검토", requester_id: "jiho", assignee_id: "sora", cc_member_ids: ["mina"], category: "reference" } as never);
const ccTwo = request({ request_id: "cc-2", title: "참조로 받은 예산안", requester_id: "jiho", assignee_id: "sora", cc_member_ids: ["mina"], category: "reference" } as never);
/** 내가 답해야 하는 업무 요청. **읽음이 서지 않는다** — 수락·거절로 접힌다. */
const toMe = request({ request_id: "to-me", title: "내게 온 검토 요청", requester_id: "jiho", assignee_id: "mina", category: "work" } as never);

function renderPage() {
  vi.mocked(api.getMyWork).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTaskHistory).mockResolvedValue([] as never);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue([ccOne, ccTwo, toMe]);
  vi.mocked(api.getWorkRequestInbox).mockResolvedValue([ccOne, ccTwo, toMe]);
  vi.mocked(api.getWorkRequest).mockImplementation(async (id) => [ccOne, ccTwo, toMe].find((row) => row.request_id === id) as never);
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
  };
  return { ...render(<RailHost {...(props as unknown as Parameters<typeof MyWorkPage>[0])} />), props };
}

/** 좌·우 레일은 화면이 «셸의 슬롯» 에 등록해 그린다. 셸이 하는 일만 흉내 내는 얇은 집이다. */
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

const rail = async () => within(await screen.findByRole("region", { name: "업무 요청 수신함" }));
const cardOf = (title: string) => screen.getByText(title).closest(".scax-inbox-card") as HTMLElement;

/**
 * WORK-003 Phase 5 (SPEC-001 U-12) — **읽으면 수신함에서만 접힌다.**
 *
 * 읽음은 그 사람에게만 있는 사실이다. 그래서 수신함에서만 사라지고 `참조 업무` 탭·CC 관계·업무
 * 상세는 그대로다 — 관계가 남아야 그 사람이 왜 이 업무를 아는지가 남는다.
 */
describe("참고 항목 읽음", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("참고 카드에만 [읽음] 이 선다 — 업무 요청 카드에는 세우지 않는다", async () => {
    renderPage();
    await (await rail()).findByText(ccOne.title);

    expect(within(cardOf(ccOne.title)).getByRole("button", { name: "읽음" })).toBeTruthy();
    // 업무 요청 갈래는 [수락]·[거절] 로 접힌다. 읽음이 수락을 대신하지 않는다.
    expect(within(cardOf(toMe.title)).queryByRole("button", { name: "읽음" })).toBeNull();
    expect(within(cardOf(toMe.title)).getByRole("button", { name: "수락" })).toBeTruthy();
  });

  it("읽으면 그 카드만 수신함에서 빠지고 뱃지가 준다", async () => {
    vi.mocked(api.markWorkRequestRead).mockResolvedValue({ request_id: "cc-1", read: true, read_at: "2026-09-19T00:00:00Z" });
    renderPage();
    const region = await screen.findByRole("region", { name: "업무 요청 수신함" });
    await within(region).findByText(ccOne.title);
    // 뱃지는 «필터가 걸린 수신함 건수» 그대로다.
    expect(within(region).getByText("3")).toBeTruthy();

    fireEvent.click(within(cardOf(ccOne.title)).getByRole("button", { name: "읽음" }));

    await waitFor(() => expect(api.markWorkRequestRead).toHaveBeenCalledWith("cc-1"));
    await waitFor(() => expect(within(region).queryByText(ccOne.title)).toBeNull());
    // 남의 카드는 그대로다 — 내 읽음이 다른 항목을 접지 않는다.
    expect(within(region).getByText(ccTwo.title)).toBeTruthy();
    expect(within(region).getByText("2")).toBeTruthy();
  });

  it("읽은 항목도 「참조 업무」 탭에는 남는다 — 요청 목록에 읽음 필터가 없다", async () => {
    vi.mocked(api.markWorkRequestRead).mockResolvedValue({ request_id: "cc-1", read: true, read_at: "2026-09-19T00:00:00Z" });
    renderPage();
    await (await rail()).findByText(ccOne.title);
    fireEvent.click(within(cardOf(ccOne.title)).getByRole("button", { name: "읽음" }));
    await waitFor(() => expect(api.markWorkRequestRead).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "참조 업무" }));
    const table = within(await screen.findByLabelText("참조 업무"));
    expect(table.getByText(ccOne.title)).toBeTruthy();
    // 그 탭에는 [읽음] 을 세우지 않는다 (OQ-J — 표시를 더하지 않는다).
    expect(table.queryByRole("button", { name: "읽음" })).toBeNull();
  });

  it("연타해도 한 번만 나간다", async () => {
    let settle: (value: unknown) => void = () => {};
    vi.mocked(api.markWorkRequestRead).mockImplementation(() => new Promise((resolve) => { settle = resolve; }) as never);
    renderPage();
    await (await rail()).findByText(ccOne.title);

    const button = within(cardOf(ccOne.title)).getByRole("button", { name: "읽음" });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(api.markWorkRequestRead).toHaveBeenCalledTimes(1));
    settle({ request_id: "cc-1", read: true, read_at: "2026-09-19T00:00:00Z" });
  });

  it("카드를 여는 것도 같은 읽음 명령을 부른다", async () => {
    vi.mocked(api.markWorkRequestRead).mockResolvedValue({ request_id: "cc-1", read: true, read_at: "2026-09-19T00:00:00Z" });
    renderPage();
    await (await rail()).findByText(ccOne.title);

    fireEvent.click(within(cardOf(ccOne.title)).getByRole("button", { name: ccOne.title }));

    // 여는 일은 그대로 일어나고, 읽음도 함께 간다 — CTA 둘이 같은 한 명령이다.
    await waitFor(() => expect(api.markWorkRequestRead).toHaveBeenCalledWith("cc-1"));
    expect(api.getWorkRequest).toHaveBeenCalledWith("cc-1");
  });

  it("읽지 못하면 목록에서 접지 않는다 — 읽은 척하지 않는다", async () => {
    vi.mocked(api.markWorkRequestRead).mockRejectedValue(new Error("이 항목을 읽음 처리할 권한이 없습니다."));
    const { props } = renderPage();
    const region = await screen.findByRole("region", { name: "업무 요청 수신함" });
    await within(region).findByText(ccOne.title);

    fireEvent.click(within(cardOf(ccOne.title)).getByRole("button", { name: "읽음" }));

    await waitFor(() => expect(props.onError).toHaveBeenCalledWith("이 항목을 읽음 처리할 권한이 없습니다."));
    expect(within(region).getByText(ccOne.title)).toBeTruthy();
  });
});
