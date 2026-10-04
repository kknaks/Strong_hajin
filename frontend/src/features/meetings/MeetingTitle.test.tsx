import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/* 회의 상세 머리 — 제목 인라인 수정 · 제목 후보 [적용] · 「내보내기」 크기 (WORK-010 Phase 1 · 1-2 · 1-3).
   api 목은 MeetingAfter.test.tsx 와 같은 벌이다 — 같은 화면을 같은 조건으로 띄운다. */
vi.mock("../../lib/api", async (actual) => ({
  getTaskAssignments: vi.fn(),
  getTaskProposals: vi.fn(),
  createTaskProposal: vi.fn(),
  respondTaskProposal: vi.fn(),
  withdrawTaskProposal: vi.fn(),
  reopenTask: vi.fn(),
  getTaskChildren: vi.fn(),
  withdrawWorkRequest: vi.fn(),
  hideWorkRequestListEntry: vi.fn(),
  // ApiError 는 진짜를 쓴다 — 화면이 409 를 `instanceof` 로 가른다
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  readMeeting: vi.fn(),
  readMeetingTranscript: vi.fn(),
  readMeetingRooms: vi.fn(),
  readMeetingMaterials: vi.fn(),
  attachMeetingMaterials: vi.fn(),
  detachMeetingMaterial: vi.fn(),
  meetingMaterialContentUrl: (m: string, id: string) => `/api/meetings/${m}/materials/${id}/content`,
  readMeetingShares: vi.fn(),
  shareMeetingWith: vi.fn(),
  revokeMeetingShare: vi.fn(),
  promoteMeetingTodo: vi.fn(),
  removeMeetingTodo: vi.fn(),
  retryMeetingFinalize: vi.fn(),
  meetingExportUrl: (id: string) => `/api/meetings/${id}/export?format=html`,
  startMeeting: vi.fn(),
  endMeeting: vi.fn(),
  updateMeetingInfo: vi.fn(),
  updateMeetingAgenda: vi.fn(),
  addMeetingAgenda: vi.fn(),
  removeMeetingAgenda: vi.fn(),
  addMeetingMemoLine: vi.fn(),
  bookMeeting: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getMeetingPromotionCandidates: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  createWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  assignTask: vi.fn(),
  getTasks: vi.fn(),
}));

import { ApiError } from "../../lib/api";
import * as api from "../../lib/api";
import { meetingScreen } from "../../lib/labels";
import type { MeetingAgenda, MeetingInfo, MeetingRecord } from "../../lib/viewModels";
import { useState } from "react";

import { MeetingDetailPage } from "./MeetingDetailPage";
import { resetRoster } from "./roster";

function DetailHost(props: Parameters<typeof MeetingDetailPage>[0]) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <>
      <MeetingDetailPage {...props} sideRailHost={host} />
      <div ref={setHost} />
    </>
  );
}

function meeting(over: Partial<MeetingInfo> = {}): MeetingInfo {
  return {
    meeting_id: "m1",
    title: "DB ax 전략",
    purpose: null,
    starts_at: "2026-09-08T06:30:00Z",
    ends_at: "2026-09-08T07:00:00Z",
    location: "대회의실",
    status: "done",
    created_by: "1",
    attendees: [
      { member_id: "1", display_name: "이건학" },
      { member_id: "2", display_name: "정우성" },
    ],
    external_attendees: [],
    viewer_relation: "attendee",
    can_edit_info: true,
    can_edit_note: false,
    can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false },
    can_write_memo: false,
    last_saved_at: "2026-09-08T07:08:00Z",
    started_at: "2026-09-08T06:30:00Z",
    carried_from_meeting_id: null,
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    ...over,
  };
}

const agendas: MeetingAgenda[] = [];

function renderDetail(over: Partial<MeetingInfo> = {}) {
  const value: MeetingRecord = { meeting: meeting(over), agendas };
  vi.mocked(api.readMeeting).mockResolvedValue(value);
  const onError = vi.fn();
  const onMeetingChanged = vi.fn();
  render(
    <DetailHost
      canCreateWorkRequests
      meetingId="m1"
      onBack={vi.fn()}
      onError={onError}
      onMeetingChanged={onMeetingChanged}
      onNotice={vi.fn()}
      onOpenMeeting={vi.fn()}
      onSessionLost={vi.fn()}
      ownerName="이건학"
    />,
  );
  return { onError, onMeetingChanged };
}

/** 제목 칸 — 닫혀 있으면 `button`, 열려 있으면 `textbox` 인 같은 노드다. */
const slot = () => screen.getByLabelText(meetingScreen.titleEdit);
/** contenteditable 에는 value 가 없다 — 글자가 곧 내용이다. */
function type(element: HTMLElement, text: string) {
  element.textContent = text;
  fireEvent.input(element);
}
function saved(title: string | null, over: Partial<MeetingInfo> = {}): MeetingRecord {
  return { meeting: meeting({ title, ...over }), agendas };
}

beforeEach(() => {
  resetRoster();
  vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
  vi.mocked(api.readMeetingMaterials).mockResolvedValue([]);
  vi.mocked(api.readMeetingShares).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getMeetingPromotionCandidates).mockResolvedValue([]);
  vi.mocked(api.readMeetingTranscript).mockResolvedValue({ items: [], memos: [] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("회의 제목 인라인 수정 (WORK-010 1-3)", () => {
  it("고칠 수 있으면 제목을 눌러 그 자리에서 열린다 — 입력칸이 새로 생기지 않는다", async () => {
    renderDetail();
    await screen.findByText("DB ax 전략");
    const element = slot();
    expect(element.closest("h2.scax-detail__title")).toBeTruthy();
    fireEvent.click(element);
    expect(element.getAttribute("role")).toBe("textbox");
    expect(element.getAttribute("contenteditable")).toBe("true");
    expect(element.textContent).toBe("DB ax 전략");
    expect(document.querySelector(".scax-detail__head input")).toBeNull();
  });

  it("Enter 로 저장한다 — `{title}` 하나만 보내고, 응답을 세우고, 목록에 다시 읽으라고 알린다", async () => {
    const { onMeetingChanged } = renderDetail();
    await screen.findByText("DB ax 전략");
    /* 서버가 정규화한 값을 돌려준다 — 화면이 «보낸 값» 이 아니라 «응답» 을 세웠는지 가르려고 둘을 다르게 둔다 */
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(saved("DB AX 전환 범위 (서버)"));
    const element = slot();
    fireEvent.click(element);
    type(element, "DB AX 전환 범위");
    fireEvent.keyDown(element, { key: "Enter" });
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledWith("m1", { title: "DB AX 전환 범위" }));
    expect(await screen.findByText("DB AX 전환 범위 (서버)")).toBeTruthy();
    expect(screen.queryByText("DB AX 전환 범위")).toBeNull();
    /* 다시 읽지 않는다 — 응답이 곧 상세다 */
    expect(api.readMeeting).toHaveBeenCalledTimes(1);
    expect(onMeetingChanged).toHaveBeenCalledTimes(1);
  });

  it("저장이 도는 동안 두 번째 저장은 요청을 내지 않는다 — 같은 자리를 두 번 잡지 않는다", async () => {
    renderDetail({ title: null, title_candidate: "후보 제목" });
    await screen.findByText(/제목 후보: 후보 제목/);
    let finish: (value: MeetingRecord) => void = () => undefined;
    vi.mocked(api.updateMeetingInfo).mockReturnValue(new Promise<MeetingRecord>((resolve) => { finish = resolve; }));
    /* 첫 저장 — 인라인 칸 */
    const element = slot();
    fireEvent.click(element);
    type(element, "첫 제목");
    fireEvent.keyDown(element, { key: "Enter" });
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledTimes(1));
    /* 도는 동안 [적용] — 비활성이고, 눌러도 두 번째 요청이 나가지 않는다 */
    const apply = screen.getByRole("button", { name: meetingScreen.titleCandidateApply }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    fireEvent.click(apply);
    /* 같은 칸을 다시 열어 Enter 해도 나가지 않는다 */
    fireEvent.click(element);
    type(element, "두 번째 제목");
    fireEvent.keyDown(element, { key: "Enter" });
    expect(api.updateMeetingInfo).toHaveBeenCalledTimes(1);
    finish(saved("첫 제목"));
    expect(await screen.findByText("첫 제목")).toBeTruthy();
    expect(api.updateMeetingInfo).toHaveBeenCalledTimes(1);
  });

  it("blur 로도 저장한다", async () => {
    renderDetail();
    await screen.findByText("DB ax 전략");
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(saved("바뀐 제목"));
    const element = slot();
    fireEvent.click(element);
    type(element, "바뀐 제목");
    fireEvent.blur(element);
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledWith("m1", { title: "바뀐 제목" }));
  });

  it("한글 조합 중의 Enter 는 저장하지 않는다 — 조합이 끝난 뒤의 Enter 가 저장한다", async () => {
    renderDetail();
    await screen.findByText("DB ax 전략");
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(saved("회의 제목"));
    const element = slot();
    fireEvent.click(element);
    type(element, "회의 제목");
    fireEvent.keyDown(element, { key: "Enter", isComposing: true });
    expect(api.updateMeetingInfo).not.toHaveBeenCalled();
    expect(element.getAttribute("role")).toBe("textbox");
    fireEvent.keyDown(element, { key: "Enter" });
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledTimes(1));
  });

  it("Esc 는 취소다 — 보내지 않고 원래 제목으로 돌아온다", async () => {
    renderDetail();
    await screen.findByText("DB ax 전략");
    const element = slot();
    fireEvent.click(element);
    type(element, "쓰다 만 제목");
    fireEvent.keyDown(element, { key: "Escape" });
    fireEvent.blur(element);
    expect(api.updateMeetingInfo).not.toHaveBeenCalled();
    expect(element.textContent).toBe("DB ax 전략");
  });

  it("빈 값과 안 바뀐 값은 보내지 않는다", async () => {
    renderDetail();
    await screen.findByText("DB ax 전략");
    const element = slot();
    fireEvent.click(element);
    type(element, "   ");
    fireEvent.keyDown(element, { key: "Enter" });
    fireEvent.click(element);
    type(element, "DB ax 전략");
    fireEvent.keyDown(element, { key: "Enter" });
    expect(api.updateMeetingInfo).not.toHaveBeenCalled();
    expect(element.textContent).toBe("DB ax 전략");
  });

  it("서버가 거절하면 원래 제목으로 돌아오고 서버 문장을 오류로 낸다", async () => {
    const { onError, onMeetingChanged } = renderDetail();
    await screen.findByText("DB ax 전략");
    vi.mocked(api.updateMeetingInfo).mockRejectedValue(new ApiError(409, "진행 중인 회의는 정보를 고칠 수 없습니다"));
    const element = slot();
    fireEvent.click(element);
    type(element, "거절될 제목");
    fireEvent.keyDown(element, { key: "Enter" });
    await waitFor(() => expect(onError).toHaveBeenCalledWith("진행 중인 회의는 정보를 고칠 수 없습니다"));
    await waitFor(() => expect(element.textContent).toBe("DB ax 전략"));
    expect(onMeetingChanged).not.toHaveBeenCalled();
  });

  it("고칠 수 없으면(`can_edit_info=false`) 글자만 선다 — 눌러도 열리지 않는다", async () => {
    renderDetail({ can_edit_info: false, viewer_relation: "shared" });
    const title = await screen.findByText("DB ax 전략");
    expect(title.tagName).toBe("H2");
    expect(screen.queryByLabelText(meetingScreen.titleEdit)).toBeNull();
    fireEvent.click(title);
    expect(document.querySelector("[contenteditable='true']")).toBeNull();
  });

  it("제목이 없으면 「제목 없는 회의」를 보이고, 열면 빈 칸에서 시작한다", async () => {
    renderDetail({ title: null });
    expect(await screen.findByText(meetingScreen.noTitle)).toBeTruthy();
    const element = slot();
    fireEvent.click(element);
    expect(element.textContent).toBe("");
  });
});

describe("제목 후보 [적용] (WORK-010 1-3)", () => {
  it("제목이 없고 후보가 있으면 「제목 후보: …」 와 [적용]이 서고, [적용]은 후보를 제목으로 저장한다", async () => {
    const { onMeetingChanged } = renderDetail({ title: null, title_candidate: "DB AX 전환 범위 논의" });
    expect(await screen.findByText(/제목 후보: DB AX 전환 범위 논의/)).toBeTruthy();
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(saved("DB AX 전환 범위 논의", { title_candidate: "DB AX 전환 범위 논의" }));
    fireEvent.click(screen.getByRole("button", { name: meetingScreen.titleCandidateApply }));
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledWith("m1", { title: "DB AX 전환 범위 논의" }));
    /* 서버가 후보를 지우지 않아도 제목이 생기면 후보 줄은 서지 않는다 */
    await waitFor(() => expect(screen.queryByText(/제목 후보:/)).toBeNull());
    expect(screen.queryByRole("button", { name: meetingScreen.titleCandidateApply })).toBeNull();
    expect(onMeetingChanged).toHaveBeenCalledTimes(1);
  });

  it("고칠 수 없으면 후보는 글자로만 서고 [적용]이 없다", async () => {
    renderDetail({ title: null, title_candidate: "DB AX 전환 범위 논의", can_edit_info: false, viewer_relation: "shared" });
    expect(await screen.findByText(/제목 후보: DB AX 전환 범위 논의/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: meetingScreen.titleCandidateApply })).toBeNull();
  });

  it("제목이 있으면 후보 줄은 서지 않는다", async () => {
    renderDetail({ title_candidate: "다른 후보" });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByText(/제목 후보/)).toBeNull();
    expect(screen.queryByRole("button", { name: meetingScreen.titleCandidateApply })).toBeNull();
  });

  it("[적용]이 거절되면 후보 줄이 그대로 남고 서버 문장을 오류로 낸다", async () => {
    const { onError } = renderDetail({ title: null, title_candidate: "DB AX 전환 범위 논의" });
    await screen.findByText(/제목 후보:/);
    vi.mocked(api.updateMeetingInfo).mockRejectedValue(new ApiError(404, "회의를 찾을 수 없습니다"));
    fireEvent.click(screen.getByRole("button", { name: meetingScreen.titleCandidateApply }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("회의를 찾을 수 없습니다"));
    expect(screen.getByText(/제목 후보: DB AX 전환 범위 논의/)).toBeTruthy();
    expect(screen.getByText(meetingScreen.noTitle)).toBeTruthy();
  });
});

describe("「내보내기」 크기 (WORK-010 1-2)", () => {
  it("옆 단추와 같은 sm · outlined-neutral 이고, 동작(같은 탭 링크)은 그대로다", async () => {
    renderDetail();
    await screen.findByText("DB ax 전략");
    const link = screen.getByRole("link", { name: "내보내기" });
    expect(link.className.split(" ")).toEqual(expect.arrayContaining(["scax-button", "scax-button--outlined-neutral", "scax-button--sm"]));
    expect(link.getAttribute("href")).toBe("/api/meetings/m1/export?format=html");
    expect(link.getAttribute("target")).toBeNull();
    const share = screen.getByRole("button", { name: "공유" });
    expect(share.className.split(" ")).toEqual(expect.arrayContaining(["scax-button--outlined-neutral", "scax-button--sm"]));
  });
});
