import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask, WorkRequest } from "../../lib/viewModels";

/**
 * 3차 프론트 발주의 회귀 검사 — **모달 한 겹, 남지 않는 스크림, 계약과 같은 문구.**
 *
 * 세 가지가 한 화면의 같은 증상으로 보고됐다.
 * 1. 「파생 업무 보기」가 새 드로어를 열어 뒤 화면이 두 번 덮였다 (발주 3).
 * 2. 닫거나 탭을 옮긴 뒤에도 겹이 상태에 남아 스크림이 화면을 계속 덮었다 (발주 4).
 * 3. 「수락하면 생성됩니다」가 서버 계약과 달랐다 — Task 는 요청을 보낼 때 이미 만들어진다 (발주 2).
 *
 * 스크림 자체는 `position:fixed;inset:0` 인 상자라 jsdom 이 «흐린지» 를 재지 못한다. 그래서 재는 것은
 * **그 상자가 문서에 몇 개 서 있는가** 다 — 0이면 가릴 것이 없고, 1이면 겹이 하나다.
 */

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

/** 요청을 «보낸 순간» 이미 서 있는 Task — 담당은 아직 비어 있다(수락이 그 자리를 켠다). */
const derivedTask = {
  task_id: "task-derived",
  title: "계약서 검토 초안",
  state: "open",
  version: 1,
  block_reason: null,
  derived: { assignment: "awaiting_acceptance", approval: null },
} as unknown as DirectTask;

const heldTask = {
  task_id: "task-held",
  title: "내가 들고 있는 업무",
  state: "in_progress",
  version: 1,
  block_reason: null,
  assignee: { member_id: "mina", display_name: "민아 (구성원)" },
} as unknown as DirectTask;

const pending: WorkRequest = {
  request_id: "to-me",
  title: "계약서 검토 초안",
  requester_id: "jiho",
  assignee_id: "mina",
  state: "pending",
  version: 1,
  task_id: "task-derived",
  assignment_state: null,
  conditions: null,
} as WorkRequest;

/** 셸의 AppBody 슬롯만 흉내 낸다 — 레일은 화면이 등록해 그린다(바퀴 5b K-6). */
function RailHost(props: Parameters<typeof MyWorkPage>[0]) {
  const [rails, setRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
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

function pageProps(personaId = "mina") {
  return {
    personaId,
    personaName: personaId === "mina" ? "민아 (구성원)" : "지호 (팀장)",
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
}

function renderPage(work: DirectTask[] = [], request: WorkRequest = pending) {
  vi.mocked(api.getMyWork).mockResolvedValue(work);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTaskHistory).mockResolvedValue([] as never);
  vi.mocked(api.getTask).mockResolvedValue(derivedTask as never);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getActionItems).mockResolvedValue([]);
  vi.mocked(api.getActions).mockResolvedValue([]);
  vi.mocked(api.getWorkRequests).mockResolvedValue([request]);
  vi.mocked(api.getWorkRequestInbox).mockResolvedValue([request]);
  vi.mocked(api.getWorkRequest).mockResolvedValue(request as never);
  vi.mocked(api.getSentTaskAssignments).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([]);
  return render(<RailHost {...(pageProps() as unknown as Parameters<typeof MyWorkPage>[0])} />);
}

/** 화면을 덮는 상자 — 모달·드로어·구 백드롭 셋 다 `position:fixed;inset:0` 인 같은 종류다. */
const scrims = () => document.querySelectorAll(".scax-modal-overlay, .scax-drawer-overlay, .modal-backdrop");

async function openRequestDetail() {
  const rail = await screen.findByRole("region", { name: "업무 요청 수신함" });
  fireEvent.click(await within(rail).findByText("계약서 검토 초안"));
  return screen.findByRole("dialog", { name: "업무 요청 상세" });
}

describe("업무 요청 상세 — 겹 하나로 파생 업무까지 간다 (3차 발주 3)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("「파생 업무 보기」는 새 겹을 열지 않고 지금 모달의 내용을 업무 상세로 바꾼다", async () => {
    renderPage();
    const detail = await openRequestDetail();
    expect(scrims().length).toBe(1);

    fireEvent.click(within(detail).getByRole("button", { name: "파생 업무 보기" }));
    await screen.findByRole("dialog", { name: "업무 상세" });

    // 겹은 그대로 하나다 — 드로어도, 중첩 모달도 서지 않는다.
    expect(scrims().length).toBe(1);
    expect(document.querySelectorAll(".scax-drawer-overlay").length).toBe(0);
    expect(screen.getAllByRole("dialog").length).toBe(1);
    // 요청 상세는 «닫힌» 것이 아니라 «가려진» 것이다 — 뒤로가 그것을 되돌린다.
    expect(screen.queryByRole("dialog", { name: "업무 요청 상세" })).toBeNull();
  });

  it("뒤로가기가 같은 겹에서 요청 상세로 되돌린다", async () => {
    renderPage();
    const detail = await openRequestDetail();
    fireEvent.click(within(detail).getByRole("button", { name: "파생 업무 보기" }));
    const taskView = await screen.findByRole("dialog", { name: "업무 상세" });

    fireEvent.click(within(taskView).getByRole("button", { name: "업무 요청 상세로 돌아가기" }));

    await screen.findByRole("dialog", { name: "업무 요청 상세" });
    expect(screen.queryByRole("dialog", { name: "업무 상세" })).toBeNull();
    expect(scrims().length).toBe(1);
    // 요청을 다시 읽지 않는다 — 같은 겹에 그대로 남아 있던 것을 되돌렸을 뿐이다.
    expect(api.getWorkRequest).toHaveBeenCalledTimes(1);
  });

  it("파생 업무에서 닫으면 흐름 전체가 닫히고 덮는 상자가 남지 않는다", async () => {
    renderPage();
    const detail = await openRequestDetail();
    fireEvent.click(within(detail).getByRole("button", { name: "파생 업무 보기" }));
    const taskView = await screen.findByRole("dialog", { name: "업무 상세" });

    fireEvent.click(within(taskView).getByRole("button", { name: "상세 닫기" }));

    await waitFor(() => expect(screen.queryAllByRole("dialog").length).toBe(0));
    expect(scrims().length).toBe(0);
  });
});

describe("오버레이 정리 — 겹이 없으면 화면을 덮는 것도 없다 (3차 발주 4)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("요청 상세를 닫으면 스크림이 문서에서 사라진다", async () => {
    renderPage();
    const detail = await openRequestDetail();
    fireEvent.click(within(detail).getByRole("button", { name: "상세 닫기" }));
    await waitFor(() => expect(screen.queryAllByRole("dialog").length).toBe(0));
    expect(scrims().length).toBe(0);
  });

  it("탭을 옮기면 열려 있던 겹이 함께 접힌다 — 다음 목록 위에 스크림이 남지 않는다", async () => {
    renderPage([heldTask]);
    fireEvent.click(await screen.findByText("내가 들고 있는 업무"));
    await screen.findByRole("dialog", { name: "업무 상세" });
    expect(scrims().length).toBe(1);

    fireEvent.click(screen.getByRole("tab", { name: "보낸 업무" }));

    await waitFor(() => expect(screen.queryAllByRole("dialog").length).toBe(0));
    expect(scrims().length).toBe(0);
  });

  it("요청 상세를 연 채 탭을 옮겨도 마찬가지다", async () => {
    renderPage();
    await openRequestDetail();
    fireEvent.click(screen.getByRole("tab", { name: "완료 업무" }));
    await waitFor(() => expect(screen.queryAllByRole("dialog").length).toBe(0));
    expect(scrims().length).toBe(0);
  });

  it("사람을 바꾸면 앞사람의 상세가 뒷사람 목록 위에 남지 않는다", async () => {
    vi.mocked(api.getMyWork).mockResolvedValue([heldTask]);
    const { rerender } = renderPage([heldTask]);
    fireEvent.click(await screen.findByText("내가 들고 있는 업무"));
    await screen.findByRole("dialog", { name: "업무 상세" });
    expect(scrims().length).toBe(1);

    // 이 화면은 페르소나가 바뀌어도 다시 마운트되지 않는다 — 같은 겹이 그대로 남을 수 있는 자리다.
    rerender(<RailHost {...(pageProps("jiho") as unknown as Parameters<typeof MyWorkPage>[0])} />);

    await waitFor(() => expect(screen.queryAllByRole("dialog").length).toBe(0));
    expect(scrims().length).toBe(0);
  });

  it("빈 스크림은 화면을 가리지 않는다 — CSS 안전망이 살아 있다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
    const { readFileSync } = await import("node:fs");
    const css: string = (readFileSync("src/styles/components.css", "utf8") as string).replace(/\s+/g, "");
    expect(css).toContain(".scax-modal-overlay:empty,.scax-drawer-overlay:empty,.modal-backdrop:empty{opacity:0;pointer-events:none}");
  });
});

describe("요청 상세 문구 — 수락은 «생성» 이 아니라 «담당 활성화» 다 (3차 발주 2)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("판단 전 요청은 수락·거절이 실제로 무엇을 하는지 그대로 적는다", async () => {
    renderPage();
    const detail = await openRequestDetail();
    expect(detail.textContent).toContain(
      "수락하면 이 업무가 내 업무에 표시되고, 내가 담당자로 지정됩니다. 거절하면 업무는 취소됩니다.",
    );
    // 계약이 하지 않는 말은 남지 않는다 — Task 는 요청을 보낼 때 이미 만들어졌다.
    expect(detail.textContent).not.toContain("거절하면 업무는 만들어지지 않습니다");
    expect(detail.textContent).not.toContain("수락 후 생성됨");
  });

  it("아직 판단 전이어도 파생 Task 는 «이미 생성됨» 으로 읽힌다", async () => {
    renderPage();
    const detail = await openRequestDetail();
    const row = within(detail).getByText("생성된 업무").closest("div");
    expect(row?.textContent).toContain("파생 업무 보기");
  });

  it("거절된 요청은 업무가 «취소» 되었다고 말한다", async () => {
    renderPage([], { ...pending, state: "rejected" });
    const detail = await openRequestDetail();
    expect(detail.textContent).toContain("요청이 거절되어 업무는 취소되었습니다.");
    expect(detail.textContent).not.toContain("업무가 생성되지 않았습니다");
  });
});
