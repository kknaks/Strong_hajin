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
  getMeetingPromotionCandidates: vi.fn(),
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
    can_edit_agendas: { memo: true, ai: false, final: true }, can_add_agenda: { memo: true, ai: false, final: true },
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
    expect(await screen.findByRole("button", { name: meetingScreen.edit })).toBeTruthy();

    /* 회의록을 고치기 시작한다 → 저장하지 않은 것이 생긴다.
       회의 정보 편집은 이제 목록 카드의 모달이 들고 자기 닫기 경로에서 스스로 묻는다
       (MeetingEditModal). 이 화면이 들고 있는 「저장 안 한 것」은 회의록 편집 하나이고,
       가드가 지켜야 하는 것도 바로 그것이다 — 잠그는 자리는 그대로다. */
    fireEvent.click(screen.getByRole("button", { name: meetingScreen.edit }));
    await screen.findByRole("button", { name: meetingScreen.save });

    // 다른 회의를 고른다 — 바로 넘어가지 않고 먼저 묻는다
    fireEvent.click((await screen.findAllByText("정산 마감 점검"))[0]);
    const ask = await screen.findByRole("alertdialog", { name: meetingScreen.leaveTitle });
    // 아직 자리는 그대로다: 고치던 칸이 살아 있다
    expect(screen.getByRole("button", { name: meetingScreen.save })).toBeTruthy();
    expect(api.readMeeting).not.toHaveBeenCalledWith("m2");

    // 「나간다」를 고르면 그때 옮겨 간다
    fireEvent.click(within(ask).getByRole("button", { name: meetingScreen.leave }));
    await waitFor(() => expect(api.readMeeting).toHaveBeenCalledWith("m2"));
  });

  /* ──────────────────────────────────────────────────────────────────────────
     **상세에서 바뀐 상태가 목록으로 돌아온다** (2026-09-15 버그).
     배선이 한 방향뿐이라(목록 → 상세) 회의를 시작·종료해도 왼쪽 카드가 옛 배지를 달고 있었다.
     낙관 렌더로 카드를 고치지 않는다 — **다시 읽으라는 신호만** 가고 그리는 값은 서버가 낸 것이다.
     ────────────────────────────────────────────────────────────────────────── */
  describe("상세 ↔ 목록 동기화", () => {
    /** 「예정」 하나만 있는 목록 — 시작하면 그 카드가 어떻게 되는지 보려면 하나여야 한다. */
    const scheduled: MeetingRow = {
      meeting_id: "m1", title: "DB ax 전략", starts_at: "2026-09-08T06:30:00Z", ends_at: "2026-09-08T07:00:00Z",
      location: "대회의실", status: "scheduled", attendee_count: 1, viewer_relation: "attendee",
    } as MeetingRow;

    function listReturns(...pages: MeetingRow[][]) {
      const mock = vi.mocked(api.listMeetings);
      mock.mockReset();
      for (const page of pages) mock.mockResolvedValueOnce({ upcoming: page, past: { items: [], next_cursor: null } });
      // 그 뒤로는 마지막 것을 계속 낸다
      mock.mockResolvedValue({ upcoming: pages[pages.length - 1], past: { items: [], next_cursor: null } });
    }

    /** 왼쪽 목록 칸 — 상세 칸에도 같은 제목·같은 상태 낱말이 서므로 «이 안에서만» 본다. */
    const railEl = () => document.querySelector(".scax-meeting-list") as HTMLElement;
    const listRail = () => within(railEl());
    /** 상세 머리의 단추 — 셸 머리의 [회의 시작]과 이름이 같아서 자리로 가른다. */
    const titleRow = () => within(document.querySelector(".scax-detail__title-row") as HTMLElement);

    it("회의를 시작하면 **카드 배지와 구획이 함께** 바뀐다", async () => {
      const running = { ...scheduled, status: "in_progress" } as MeetingRow;
      listReturns([scheduled], [running]);
      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: info({ status: "scheduled" }), agendas: [] } as MeetingRecord);
      render(<ShellHost />);

      fireEvent.click((await screen.findAllByText("DB ax 전략"))[0]);
      // 시작 전 — 「예정」 구획에 「예정」 배지다
      await waitFor(() => expect(listRail().getByRole("heading", { name: /예정/ })).toBeTruthy());

      vi.mocked(api.startMeeting).mockResolvedValue(undefined as never);
      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: info({ status: "in_progress" }), agendas: [] } as MeetingRecord);
      await waitFor(() => expect(titleRow().getByRole("button", { name: meetingScreen.start })).toBeTruthy());
      fireEvent.click(titleRow().getByRole("button", { name: meetingScreen.start }));

      // 목록을 **다시 읽는다** — 상세만 바뀌고 마는 자리였다
      await waitFor(() => expect(vi.mocked(api.listMeetings).mock.calls.length).toBeGreaterThan(1));
      // 배지와 구획이 함께 「진행 중」이 된다
      await waitFor(() => expect(listRail().getByRole("heading", { name: /진행 중/ })).toBeTruthy());
      expect(listRail().queryByRole("heading", { name: /예정/ })).toBeNull();
    });

    it("회의를 종료해도 그렇다 — 카드가 「지난」으로 넘어간다", async () => {
      const running = { ...scheduled, status: "in_progress" } as MeetingRow;
      const mock = vi.mocked(api.listMeetings);
      mock.mockReset();
      mock.mockResolvedValueOnce({ upcoming: [running], past: { items: [], next_cursor: null } });
      mock.mockResolvedValue({ upcoming: [], past: { items: [{ ...scheduled, status: "done" } as MeetingRow], next_cursor: null } });
      // 회의를 닫는 것은 «이끄는 창» 의 일이다 — 그 자리를 서게 하려면 올리는 쪽이어야 한다
      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: info({ status: "in_progress", can_write_memo: true }), agendas: [] } as MeetingRecord);
      render(<ShellHost />);

      fireEvent.click((await screen.findAllByText("DB ax 전략"))[0]);
      await waitFor(() => expect(listRail().getByRole("heading", { name: /진행 중/ })).toBeTruthy());

      vi.mocked(api.endMeeting).mockResolvedValue(undefined as never);
      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: info({ status: "done" }), agendas: [] } as MeetingRecord);
      await waitFor(() => expect(titleRow().getByRole("button", { name: meetingScreen.end })).toBeTruthy());
      fireEvent.click(titleRow().getByRole("button", { name: meetingScreen.end }));

      await waitFor(() => expect(listRail().getByRole("heading", { name: /지난/ })).toBeTruthy());
      expect(listRail().queryByRole("heading", { name: /진행 중/ })).toBeNull();
    });

    it("[바로 시작] 직후 목록에 선다 — 새로고침을 요구하지 않는다", async () => {
      const started = { ...scheduled, meeting_id: "q1", title: "빠르게 시작한 회의", status: "in_progress" } as MeetingRow;
      listReturns([], [started]);
      vi.mocked(api.quickStartMeeting).mockResolvedValue({ meeting: info({ meeting_id: "q1", status: "in_progress" }), agendas: [] } as MeetingRecord);
      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: info({ meeting_id: "q1", status: "in_progress" }), agendas: [] } as MeetingRecord);
      render(<ShellHost />);
      await screen.findByRole("button", { name: /회의 시작/ });

      fireEvent.click(screen.getByRole("button", { name: /회의 시작/ }));

      await waitFor(() => expect(listRail().getByText("빠르게 시작한 회의")).toBeTruthy());
    });

    it("배지와 구획이 **같은 값**에서 나온다 — 「진행 중」 카드가 「예정」 아래 서지 않는다", async () => {
      /* 서버의 `upcoming` 은 「예정」이 아니라 **「아직 안 지난 회의」**다 (`policy.py:277` —
         과거로 치는 것은 종료·실패·취소와 시간이 지난 것뿐). 그래서 진행 중·정리 중이 이 칸에
         함께 온다. 전에는 그 칸 전체에 「예정」이라는 제목을 달아서, 「진행 중」 배지를 단 카드가
         「예정」 아래 서 있었다 — 카드가 낡아서가 아니라 **제목이 그 칸을 잘못 불렀다.** */
      listReturns([
        { ...scheduled, meeting_id: "m9", title: "아직 안 연 회의", status: "scheduled" } as MeetingRow,
        { ...scheduled, meeting_id: "m8", title: "지금 도는 회의", status: "in_progress" } as MeetingRow,
        { ...scheduled, meeting_id: "m7", title: "정리하는 회의", status: "summarizing" } as MeetingRow,
      ]);
      render(<ShellHost />);

      const runningHead = await screen.findByRole("heading", { name: /진행 중/ });
      const upcomingHead = screen.getByRole("heading", { name: /예정/ });
      const items = (head: HTMLElement) =>
        [...(railEl().querySelector(`ul[aria-labelledby="${head.id}"]`) as HTMLElement).querySelectorAll("li")]
          .map((node) => node.textContent ?? "");

      // 도는 것 둘은 「진행 중」 아래, 안 연 것 하나만 「예정」 아래
      expect(items(runningHead).join(" ")).toContain("지금 도는 회의");
      expect(items(runningHead).join(" ")).toContain("정리하는 회의");
      expect(items(upcomingHead).join(" ")).toContain("아직 안 연 회의");
      expect(items(upcomingHead).join(" ")).not.toContain("지금 도는 회의");
    });
  });

  it("고치던 것이 없으면 묻지 않고 바로 옮겨 간다", async () => {
    render(<ShellHost />);
    fireEvent.click((await screen.findAllByText("DB ax 전략"))[0]);
    await screen.findByRole("button", { name: meetingScreen.edit });

    fireEvent.click((await screen.findAllByText("정산 마감 점검"))[0]);
    await waitFor(() => expect(api.readMeeting).toHaveBeenCalledWith("m2"));
    expect(screen.queryByRole("alertdialog", { name: meetingScreen.leaveTitle })).toBeNull();
  });
});
