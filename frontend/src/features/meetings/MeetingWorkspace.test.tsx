import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import type React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  listMeetings: vi.fn(),
  readMeeting: vi.fn(),
  quickStartMeeting: vi.fn(),
  removeMeeting: vi.fn(),
  bookMeeting: vi.fn(),
  readMeetingTranscript: vi.fn(),
  readMeetingRooms: vi.fn(),
  readMeetingMaterials: vi.fn(),
  readMeetingMaterialText: vi.fn(),
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
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  createWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  assignTask: vi.fn(),
  getTasks: vi.fn(),
}));

import * as api from "../../lib/api";
import { meetingScreen } from "../../lib/labels";
import type { MeetingInfo, MeetingRecord, MeetingRow } from "../../lib/viewModels";
import { MeetingWorkspace } from "./MeetingWorkspace";

function info(over: Partial<MeetingInfo> = {}): MeetingInfo {
  return {
    meeting_id: "m1",
    title: "DB ax 전략",
    purpose: "AX 전환 범위를 정한다.",
    starts_at: "2026-09-08T06:30:00Z",
    ends_at: "2026-09-08T07:00:00Z",
    location: "대회의실",
    status: "done",
    created_by: "이건학",
    attendees: [{ member_id: "1", display_name: "이건학" }],
    external_attendees: [],
    viewer_relation: "attendee",
    can_edit_info: true,
    can_edit_note: true,
    can_edit_agendas: true,
    can_write_memo: false,
    started_at: "2026-09-08T06:30:00Z",
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    last_saved_at: "2026-09-08T07:08:00Z",
    carried_from_meeting_id: null,
    ...over,
  };
}

const rows: MeetingRow[] = [
  { meeting_id: "m1", title: "DB ax 전략", starts_at: "2026-09-08T06:30:00Z", ends_at: "2026-09-08T07:00:00Z", location: "대회의실", status: "done", attendee_count: 1, viewer_relation: "attendee" } as MeetingRow,
  { meeting_id: "m2", title: "정산 마감 점검", starts_at: "2026-09-09T06:30:00Z", ends_at: "2026-09-09T07:00:00Z", location: "소회의실", status: "done", attendee_count: 1, viewer_relation: "attendee" } as MeetingRow,
];

/** 셸이 내주는 세 자리(머리 액션 · 좌 레일 · 우 레일)만 흉내 내는 얇은 집. */
function ShellHost() {
  const [actions, setActions] = useState<React.ReactNode>(null);
  const [rails, setRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
  return (
    <>
      {actions}
      {rails.left}
      <MeetingWorkspace
        canCreateWorkRequests
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRegisterHeaderActions={setActions}
        onRegisterRails={setRails}
        onSessionLost={vi.fn()}
        ownerName="이건학"
      />
      {rails.right}
    </>
  );
}

beforeEach(() => {
  vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [], past: { items: rows, next_cursor: null } });
  vi.mocked(api.readMeeting).mockImplementation(async (id: string) =>
    ({ meeting: info({ meeting_id: id, title: id === "m1" ? "DB ax 전략" : "정산 마감 점검" }), agendas: [] }) as MeetingRecord,
  );
  vi.mocked(api.readMeetingTranscript).mockResolvedValue({ items: [] } as never);
  vi.mocked(api.readMeetingMaterials).mockResolvedValue([] as never);
  vi.mocked(api.readMeetingShares).mockResolvedValue([] as never);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("회의 한 화면 4칸 (바퀴 6a)", () => {
  /*
   * M-2 잠금. 예전에는 breadcrumb 의 「회의 목록」을 누를 때 물었고, 화면이 하나로 합쳐지면서
   * **다른 회의를 고르는 순간**으로 자리가 옮겨 갔다. 이 검사가 없으면 「묻지 않고 내용이 날아가는」
   * 회귀가 조용히 지나간다 — 화면은 멀쩡해 보이고 클래스 검사도 전부 통과하기 때문이다.
   */
  it("고치던 것이 있으면 다른 회의를 고를 때 한 번 묻는다 — 답하기 전에는 자리가 바뀌지 않는다", async () => {
    render(<ShellHost />);

    // 목록 칸에서 하나를 고르면 상세 칸이 그 회의로 바뀐다 (M-1 — 화면 전환이 아니다)
    fireEvent.click((await screen.findAllByText("DB ax 전략"))[0]);
    expect(await screen.findByRole("button", { name: meetingScreen.editInfo })).toBeTruthy();

    // 회의 정보를 고치기 시작한다 → 저장하지 않은 것이 생긴다
    fireEvent.click(screen.getByRole("button", { name: meetingScreen.editInfo }));
    await screen.findByLabelText(meetingScreen.place);

    // 다른 회의를 고른다 — 바로 넘어가지 않고 먼저 묻는다
    fireEvent.click((await screen.findAllByText("정산 마감 점검"))[0]);
    const ask = await screen.findByRole("alertdialog", { name: meetingScreen.leaveTitle });
    // 아직 자리는 그대로다: 고치던 칸이 살아 있다
    expect(screen.getByLabelText(meetingScreen.place)).toBeTruthy();

    // 「나간다」를 고르면 그때 옮겨 간다
    fireEvent.click(within(ask).getByRole("button", { name: meetingScreen.leave }));
    await waitFor(() => expect(api.readMeeting).toHaveBeenCalledWith("m2"));
  });

  it("고치던 것이 없으면 묻지 않고 바로 옮겨 간다", async () => {
    render(<ShellHost />);
    fireEvent.click((await screen.findAllByText("DB ax 전략"))[0]);
    await screen.findByRole("button", { name: meetingScreen.editInfo });

    fireEvent.click((await screen.findAllByText("정산 마감 점검"))[0]);
    await waitFor(() => expect(api.readMeeting).toHaveBeenCalledWith("m2"));
    expect(screen.queryByRole("alertdialog", { name: meetingScreen.leaveTitle })).toBeNull();
  });
});
