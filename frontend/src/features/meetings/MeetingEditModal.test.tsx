import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  listMeetings: vi.fn(),
  readMeeting: vi.fn(),
  removeMeeting: vi.fn(),
  quickStartMeeting: vi.fn(),
  bookMeeting: vi.fn(),
  updateMeetingInfo: vi.fn(),
  readMeetingRooms: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
}));

import * as api from "../../lib/api";
import { meetingScreen } from "../../lib/labels";
import type { MeetingRecord, MeetingRow } from "../../lib/viewModels";
import { useState } from "react";
import type React from "react";

import { MeetingListPage } from "./MeetingListPage";
import { resetRoster } from "./roster";

/**
 * 회의 정보 수정 — **목록 카드의 [수정]이 여는 모달** 잠금.
 *
 * 이 자리는 예전에 상세 머리의 연필이었다 (그 잠금은 `MeetingDetail.test.tsx` 가 들고 있었다).
 * 시안 02·10 에 따라 자리를 옮기면서 **잠금도 함께 옮겼다** — 아래 넷은 옮기기 전에 걸려 있던
 * 것과 같은 것을 본다: 칸이 값으로 차 있는가 · 바뀐 것이 없으면 저장이 막히는가 ·
 * `updateMeetingInfo` 로 나가는 patch 가 계약 모양인가 · 서버가 닫은 회의는 칸이 안 열리는가.
 * 다섯째(고치던 채로 닫으면 묻는다)는 자리가 모달로 오며 새로 생긴 이탈 경로다.
 */

const row = (over: Partial<MeetingRow> & { meeting_id: string }): MeetingRow => ({
  title: "주간 회의",
  starts_at: "2026-09-10T06:00:00Z",
  ends_at: "2026-09-10T07:00:00Z",
  location: "회의실 A",
  status: "scheduled",
  viewer_relation: "attendee",
  created_by: "이건학",
  attendee_count: 2,
  ...over,
});

const record = (over: Partial<MeetingRecord["meeting"]> = {}): MeetingRecord => ({
  meeting: {
    meeting_id: "p1",
    title: "주간 회의",
    purpose: null,
    starts_at: "2026-09-10T06:00:00Z",
    ends_at: "2026-09-10T07:00:00Z",
    location: "회의실 A",
    status: "scheduled",
    created_by: "이건학",
    attendees: [
      { member_id: "1", display_name: "이건학" },
      { member_id: "2", display_name: "정우성" },
    ],
    external_attendees: [],
    viewer_relation: "attendee",
    can_edit_info: true,
    can_edit_note: false,
    can_edit_agendas: { memo: true, ai: false, final: true }, can_add_agenda: { memo: true, ai: false, final: true },
    can_write_memo: false,
    started_at: null,
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    last_saved_at: null,
    carried_from_meeting_id: null,
    ...over,
  },
  agendas: [],
});

function ListHost(props: Parameters<typeof MeetingListPage>[0]) {
  const [actions, setActions] = useState<React.ReactNode>(null);
  return (
    <>
      {actions}
      <MeetingListPage {...props} onRegisterHeaderActions={setActions} />
    </>
  );
}

function renderList(rows: MeetingRow[] = [row({ meeting_id: "p1" })]) {
  vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: rows, past: { items: [], next_cursor: null } });
  const onOpenMeeting = vi.fn();
  const onNotice = vi.fn();
  const onMeetingUpdated = vi.fn();
  render(
    <ListHost
      onError={vi.fn()}
      onMeetingUpdated={onMeetingUpdated}
      onNotice={onNotice}
      onOpenMeeting={onOpenMeeting}
      selected={null}
    />,
  );
  return { onMeetingUpdated, onNotice, onOpenMeeting };
}

/** 카드의 [수정]을 눌러 모달을 연다. */
async function openEdit() {
  fireEvent.click(await screen.findByRole("button", { name: meetingScreen.edit }));
  return screen.findByRole("dialog", { name: meetingScreen.editInfo });
}

beforeEach(() => {
  resetRoster();
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
  vi.mocked(api.readMeeting).mockResolvedValue(record());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("회의 정보 수정 — 목록 카드의 [수정]이 여는 모달", () => {
  it("칸은 지금 값으로 차 있고, 바뀐 것이 없으면 저장할 수 없다", async () => {
    renderList();
    const modal = await openEdit();

    expect((within(modal).getByLabelText(meetingScreen.titleField) as HTMLInputElement).value).toBe("주간 회의");
    expect((within(modal).getByLabelText(meetingScreen.place) as HTMLInputElement).value).toBe("회의실 A");
    // 일시는 30분 눈금의 공용 부품이다 — 브라우저 기본 달력·드롭다운이 아니다
    expect(within(modal).getByRole("button", { name: /시작 시각/ }).textContent).toContain("15:00");
    expect(within(modal).getByRole("button", { name: /종료 시각/ }).textContent).toContain("16:00");
    expect(within(modal).getByRole("button", { name: meetingScreen.save }).hasAttribute("disabled")).toBe(true);
  });

  it("저장하면 계약대로 보내고, 고른 회의와 고친 회의가 어긋나지 않는다", async () => {
    const { onMeetingUpdated, onNotice, onOpenMeeting } = renderList([
      row({ meeting_id: "p1" }),
      row({ meeting_id: "p2", title: "정산 점검" }),
    ]);
    await screen.findByText("정산 점검");

    // 두 번째 카드의 [수정] — 그 줄의 회의를 읽어야 한다
    vi.mocked(api.readMeeting).mockResolvedValue(record({ meeting_id: "p2", title: "정산 점검" }));
    fireEvent.click(screen.getAllByRole("button", { name: meetingScreen.edit })[1]);
    const modal = await screen.findByRole("dialog", { name: meetingScreen.editInfo });
    await waitFor(() => expect(api.readMeeting).toHaveBeenCalledWith("p2"));
    // [수정]은 «고르기» 가 아니다 — 고른 회의를 건드리지 않는다
    expect(onOpenMeeting).not.toHaveBeenCalled();

    fireEvent.change(within(modal).getByLabelText(meetingScreen.titleField), { target: { value: "정산 점검 2" } });
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(record({ meeting_id: "p2" }));
    fireEvent.click(within(modal).getByRole("button", { name: meetingScreen.save }));

    await waitFor(() =>
      expect(api.updateMeetingInfo).toHaveBeenCalledWith("p2", {
        title: "정산 점검 2",
        starts_at: "2026-09-10T15:00:00+09:00",
        ends_at: "2026-09-10T16:00:00+09:00",
        location: "회의실 A",
        attendee_ids: ["1", "2"],
      }),
    );
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(meetingScreen.saved));
    // 고친 회의를 고르고 있었다면 상세도 다시 읽어야 한다 — 판단은 워크스페이스가 한다
    await waitFor(() => expect(onMeetingUpdated).toHaveBeenCalledWith("p2"));
  });

  /**
   * 증보 K22 — **겹침 문구의 주인이 서버다.**
   *
   * 「민아 님의」처럼 **이름이 들어 있어** 화면이 지을 수 없는 문장이다. 남의 시간은 busy/free 만
   * 읽으므로 화면은 그 이름을 알 자리가 없다 — `ApiError.message` 를 **그대로** 낸다.
   *
   * ⚠ **`detail` 은 문자열이다** — 방예약 `409` 들만 `{code, message}` 객체를 낸다.
   * `detail.code` 를 읽는 길로 새면 `undefined` 라 이 문장이 조용히 사라진다.
   */
  it("겹침 409 는 서버 문장을 그대로 낸다 — 화면이 문장을 짓지 않는다 (K22)", async () => {
    const onError = vi.fn();
    vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [row({ meeting_id: "p1" })], past: { items: [], next_cursor: null } });
    render(<ListHost onError={onError} onMeetingUpdated={vi.fn()} onNotice={vi.fn()} onOpenMeeting={vi.fn()} selected={null} />);
    const modal = await openEdit();
    fireEvent.change(within(modal).getByLabelText(meetingScreen.titleField), { target: { value: "옮긴 회의" } });

    const detail = "민아 님의 일정과 겹칩니다";
    vi.mocked(api.updateMeetingInfo).mockRejectedValue(new api.ApiError(409, detail, detail));
    fireEvent.click(within(modal).getByRole("button", { name: meetingScreen.save }));

    await waitFor(() => expect(onError).toHaveBeenCalledWith(detail));
    // 마침표도 붙이지 않는다 — 서버 문자열이 정본이다.
    expect(onError.mock.calls.at(-1)![0]).toBe("민아 님의 일정과 겹칩니다");
  });

  /*
   * 이탈 가드의 «모달 쪽 절반». 워크스페이스가 「다른 회의를 고를 때」를 막는 것과 짝이다 —
   * 고치던 것이 있는데 닫기가 조용히 통과하면 쓴 것이 그대로 사라진다.
   */
  it("고치던 채로 닫으면 한 번 묻는다 — 고친 것이 없으면 묻지 않는다", async () => {
    renderList();
    let modal = await openEdit();

    // 아무것도 안 고쳤다 → 바로 닫힌다
    fireEvent.click(within(modal).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: meetingScreen.editInfo })).toBeNull());

    modal = await openEdit();
    fireEvent.change(within(modal).getByLabelText(meetingScreen.titleField), { target: { value: "주간 회의 2" } });
    fireEvent.click(within(modal).getByRole("button", { name: "닫기" }));

    const ask = await screen.findByRole("alertdialog", { name: meetingScreen.leaveTitle });
    // 아직 안 닫혔다 — 고치던 칸이 살아 있다
    expect((screen.getByLabelText(meetingScreen.titleField) as HTMLInputElement).value).toBe("주간 회의 2");
    fireEvent.click(within(ask).getByRole("button", { name: meetingScreen.leave }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: meetingScreen.editInfo })).toBeNull());
    // 나간 것이지 저장한 것이 아니다
    expect(api.updateMeetingInfo).not.toHaveBeenCalled();
  });

  it("서버가 못 고친다고 하면 칸을 열지 않는다 — 행의 상태로 넘겨짚지 않는다", async () => {
    vi.mocked(api.readMeeting).mockResolvedValue(record({ can_edit_info: false }));
    renderList();
    const modal = await openEdit();

    expect(await within(modal).findByText(meetingScreen.cannotEditInfo)).toBeTruthy();
    expect(within(modal).queryByLabelText(meetingScreen.titleField)).toBeNull();
    expect(within(modal).getByRole("button", { name: meetingScreen.save }).hasAttribute("disabled")).toBe(true);
  });
});
