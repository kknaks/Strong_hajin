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
import { addPersonOnce } from "./PeoplePicker";
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
  vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
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
    // 장소는 글자 칸이 아니라 회의실 셀렉트다(SPEC-010 §2.2 · OQ-1005) — 예약 없던 회의는 「회의실 예약 없음」 에서 연다
    expect(modal.querySelector("#meeting-head-place")).toBeNull();
    expect((within(modal).getByLabelText(meetingScreen.noRoom) as HTMLInputElement).checked).toBe(true);
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

    // 바뀐 값만 보낸다(SPEC-010 §4.3 · OQ-1010) — 제목만 고쳤으니 시각·참석자·방은 싣지 않는다. 새 예약이 없으니 멱등 키도 없다
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledWith("p2", { title: "정산 점검 2" }, undefined));
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

/*
 * WORK-012 WP1-FE — SH-IMP-002·004(SPEC-010 §2.1 · AC-01 · AC-02).
 *
 * jsdom 은 배치를 계산하지 않는다 — 그래서 «넘치지 않음» 은 두 가지로 잠근다:
 * ① 일정 한 줄의 DOM 구조(날짜 → 시각 한 쌍이 왼쪽 열 안에 있다) ② 폭을 정하는 CSS 규칙을 **소스 글자 그대로** 읽어
 *   날짜 칸이 고정 200px 이 아니고(줄어든다) 수정 모달의 시각 한 쌍이 줄어드는지. CSS 는 `?raw` 로 읽으면 vitest 가 빈 글자로
 *   스텁하므로 `node:fs` 로 읽는다(`ChatDrawer.test.tsx` 선례).
 */
async function meetingsCss(): Promise<string> {
  // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다(`ChatDrawer.test.tsx` 와 같은 처리).
  const { readFileSync } = await import("node:fs");
  return (readFileSync("src/styles/meetings.css", "utf8") as string).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, "");
}

describe("WP1-FE — 수정 모달 일정 칸 · 참석자 중복 (SH-IMP-002·004)", () => {
  it("날짜·시작·종료가 왼쪽 열의 한 줄(.meeting-when)에 선다 — 오른쪽 열에는 검색 입력뿐이다", async () => {
    renderList();
    const modal = await openEdit();
    const grid = modal.querySelector(".meeting-meta-edit") as HTMLElement;
    const [left, right] = [...grid.children] as HTMLElement[];
    const row = left.querySelector(".meeting-when") as HTMLElement;
    expect(row).toBeTruthy();
    // 한 줄 안에 날짜 칸 하나 · 시각 한 쌍 하나
    expect(row.querySelectorAll(":scope > .date-field")).toHaveLength(1);
    expect(row.querySelectorAll(":scope > .time-range")).toHaveLength(1);
    expect(within(row).getByRole("button", { name: /종료 시각/ })).toBeTruthy();
    // 종료 시각은 왼쪽 열 안이다 — 오른쪽 열(참석자)에는 시각 단추가 없다
    expect(within(right).queryByRole("button", { name: /시각/ })).toBeNull();
    expect(within(right).getByRole("combobox", { name: meetingScreen.nameSearchPlaceholder })).toBeTruthy();
  });

  it("날짜 칸은 고정 200px 이 아니라 줄어들고, 수정 모달의 시각 한 쌍은 좁게 선다 (CSS)", async () => {
    const css = await meetingsCss();
    // 고정 폭이던 옛 규칙이 없다 — 넘치던 원인
    expect(css).not.toContain(".meeting-when.date-field{flex:none;width:200px;}");
    // 날짜는 남는 폭까지만(최대 200) · 줄어들 수 있다
    expect(css).toContain(".meeting-when.date-field{flex:01200px;min-width:0;}");
    // 수정 모달(두 열)에서만 시각 한 쌍을 줄인다 — 생성 모달(한 열)은 그대로 넓다
    expect(css).toContain(".meeting-meta-edit.meeting-when.time-range.select-trigger{min-width:0;padding:07px;}");
    expect(css).toContain(".meeting-meta-edit.meeting-when.time-range.select-triggersvg{display:none;}");
  });

  it("생성 모달도 같은 줄 모양(.meeting-when)을 쓴다 — 두 열 규칙(.meeting-meta-edit)은 걸리지 않는다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
    renderList();
    fireEvent.click(await screen.findByRole("button", { name: meetingScreen.book }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    const row = modal.querySelector(".meeting-when") as HTMLElement;
    expect(row.querySelector(".date-field")).toBeTruthy();
    expect(row.closest(".meeting-meta-edit")).toBeNull();
  });

  it("검색으로 고른 사람은 한 번만 담기고 결과에서 빠진다 (AC-02)", async () => {
    vi.mocked(api.getOrganizationTree).mockResolvedValue([
      { id: "u1", name: "플랫폼실", parent_id: null, unit_type: null, lifecycle: "active", display_order: 1, member_count: 1, direct_member_count: 1, leaders: [] },
    ]);
    vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([
      { member_id: "41", display_name: "한서린", memberships: [], positions: [], grade: "주임", jobs: [] },
    ]);
    renderList();
    const modal = await openEdit();
    const search = within(modal).getByRole("combobox", { name: meetingScreen.nameSearchPlaceholder });

    fireEvent.change(search, { target: { value: "한서" } });
    fireEvent.click(await within(modal).findByRole("option", { name: /한서린/ }));
    // 다시 찾아도 이미 담긴 사람은 결과에 없다
    fireEvent.change(search, { target: { value: "한서" } });
    expect(within(modal).queryByRole("option", { name: /한서린/ })).toBeNull();

    vi.mocked(api.updateMeetingInfo).mockResolvedValue(record());
    fireEvent.click(within(modal).getByRole("button", { name: meetingScreen.save }));
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalled());
    expect(vi.mocked(api.updateMeetingInfo).mock.calls[0][1].attendee_ids).toEqual(["1", "2", "41"]);
  });

  it("담는 함수는 같은 사람을 두 번 넣지 않는다 — 결과가 다시 그려지기 전의 두 번 누르기도 하나다", () => {
    const one = { member_id: "41", name: "한서린" };
    const once = addPersonOnce([], one);
    expect(addPersonOnce(once, { ...one })).toBe(once);
    expect(addPersonOnce(once, { member_id: "42", name: "전지우" }).map((p) => p.member_id)).toEqual(["41", "42"]);
  });
});

/*
 * WORK-012 WP3-FE — 수정 모달의 회의실(SPEC-010 §2.2 · §4.3 · WP3 계약 고정 2·3 · H-1 · H-2 · OQ-1010).
 * 장소 글자 칸 → 회의실 셀렉트 · 바뀐 값만 보내기 · 기존 방 못 쓰면 [저장] 옆 이유 · 409 거절 · 조회 실패 중 저장 문구.
 */
describe("WP3-FE — 수정 모달 회의실 셀렉트 · 바뀐 값만", () => {
  const booked = () => record({ room_reservation: { status: "booked", room_name: "회의실 3", reason: null } });
  const rooms = (over: Partial<import("../../lib/viewModels").MeetingRoom> = {}) => [
    { room_id: 3, name: "회의실 3", capacity: 6, available: true, current: true, unavailable_reason: null, ...over },
    { room_id: 1, name: "회의실 1 (6인)", capacity: 6, available: true, current: false, unavailable_reason: null },
  ];
  const saveButton = (modal: HTMLElement) => within(modal).getByRole("button", { name: meetingScreen.save }) as HTMLButtonElement;

  async function openBooked(roomList = rooms()) {
    vi.mocked(api.readMeeting).mockResolvedValue(booked());
    vi.mocked(api.readMeetingRooms).mockResolvedValue(roomList);
    renderList();
    const modal = await openEdit();
    await within(modal).findByText(meetingScreen.roomKeep("회의실 3"));
    return modal;
  }

  it("방이 잡힌 회의는 맨 위 「기존 — 회의실 3 (변경 안 함)」 에서 열리고, 조회에 meeting_id · 인원을 싣는다", async () => {
    const modal = await openBooked();
    expect((within(modal).getByLabelText(meetingScreen.roomKeep("회의실 3")) as HTMLInputElement).checked).toBe(true);
    await waitFor(() => expect(api.readMeetingRooms).toHaveBeenCalledWith(expect.objectContaining({ meeting_id: "p1", people: 2 })));
    // 아무것도 안 바꾸면 저장할 수 없다
    expect(saveButton(modal).disabled).toBe(true);
  });

  it("다른 방을 고르면 `room: {room_id}` 만 보내고(새 예약일 수 있어) 멱등 키를 싣는다", async () => {
    const modal = await openBooked();
    fireEvent.click(await within(modal).findByLabelText("회의실 1 (6인)"));
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(booked());
    fireEvent.click(saveButton(modal));
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalled());
    const [id, patch, key] = vi.mocked(api.updateMeetingInfo).mock.calls[0];
    expect(id).toBe("p1");
    expect(patch).toEqual({ room: { room_id: 1 } });
    expect(typeof key).toBe("string");
  });

  it("「회의실 예약 없음」 을 고르면 `room: {room_id: null}`(예약 취소) — 멱등 키 없음", async () => {
    const modal = await openBooked();
    fireEvent.click(within(modal).getByLabelText(meetingScreen.noRoom));
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(record());
    fireEvent.click(saveButton(modal));
    await waitFor(() => expect(api.updateMeetingInfo).toHaveBeenCalledWith("p1", { room: { room_id: null } }, undefined));
  });

  it("시각만 바꾸면 시작·종료만 보낸다 — 방은 그대로(`room` 없음)", async () => {
    const modal = await openBooked();
    fireEvent.click(within(modal).getByRole("button", { name: /종료 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "16:30" }));
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(booked());
    fireEvent.click(saveButton(modal));
    await waitFor(() =>
      expect(api.updateMeetingInfo).toHaveBeenCalledWith("p1", { starts_at: "2026-09-10T15:00:00+09:00", ends_at: "2026-09-10T16:30:00+09:00" }, undefined),
    );
  });

  it("기존 방을 새 조건에 못 쓰면 [저장]이 막히고 그 옆에 이유 한 줄 (H-2)", async () => {
    const modal = await openBooked(rooms({ available: false, unavailable_reason: "time_conflict" }));
    // 비활성 기존 줄은 이유(「새 시간에 예약 불가」)가 라벨 안에 함께 선다 — 첫 라디오로 짚는다
    const keep = () => modal.querySelector<HTMLInputElement>('input[name="meeting-edit-room"]') as HTMLInputElement;
    await waitFor(() => expect(keep().disabled).toBe(true));
    expect(keep().checked).toBe(true);
    expect(within(modal).getByText("새 시간에 예약 불가")).toBeTruthy();
    // 제목을 고쳐도 저장이 막혀 있다 — 조용히 예약이 풀리지 않는다
    fireEvent.change(within(modal).getByLabelText(meetingScreen.titleField), { target: { value: "옮긴 회의" } });
    expect(saveButton(modal).disabled).toBe(true);
    const foot = modal.querySelector(".modal-foot") as HTMLElement;
    expect(within(foot).getByText(meetingScreen.roomPickAgain)).toBeTruthy();
    fireEvent.click(within(modal).getByLabelText("회의실 1 (6인)"));
    await waitFor(() => expect(saveButton(modal).disabled).toBe(false));
  });

  it("저장 직전 그새 방이 차면(409 ROOM_BOOKING_REFUSED) 셀렉트가 가능한 방만으로 다시 서고 다시 고를 때까지 막힌다 (AC-08)", async () => {
    const { onNotice } = { onNotice: vi.fn() };
    vi.mocked(api.readMeeting).mockResolvedValue(booked());
    vi.mocked(api.readMeetingRooms).mockResolvedValue(rooms());
    vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [row({ meeting_id: "p1" })], past: { items: [], next_cursor: null } });
    render(<ListHost onError={vi.fn()} onMeetingUpdated={vi.fn()} onNotice={onNotice} onOpenMeeting={vi.fn()} selected={null} />);
    const modal = await openEdit();
    fireEvent.click(await within(modal).findByLabelText("회의실 1 (6인)"));
    vi.mocked(api.updateMeetingInfo).mockRejectedValue(
      new api.ApiError(409, "Conflict", { code: "ROOM_BOOKING_REFUSED", message: "taken", available_rooms: [{ room_id: 5, name: "회의실 5 (8인)", capacity: 8 }] }),
    );
    fireEvent.click(saveButton(modal));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(meetingScreen.roomRejected.ROOM_BOOKING_REFUSED));
    expect(await within(modal).findByText("회의실 5 (8인)")).toBeTruthy();
    expect(within(modal).queryByText("회의실 1 (6인)")).toBeNull();
    expect(saveButton(modal).disabled).toBe(true);
    fireEvent.click(within(modal).getByLabelText("회의실 5 (8인)"));
    await waitFor(() => expect(saveButton(modal).disabled).toBe(false));
  });

  it("조회 실패 중 「기존 (확인 못 함)」 그대로 시각을 바꿔 저장하면 결과 문구가 그 사실을 말한다 (H-1)", async () => {
    const onNotice = vi.fn();
    vi.mocked(api.readMeeting).mockResolvedValue(booked());
    vi.mocked(api.readMeetingRooms).mockRejectedValue(new api.ApiError(503, "Service Unavailable", { code: "ROOM_SERVICE_UNAVAILABLE" }));
    vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [row({ meeting_id: "p1" })], past: { items: [], next_cursor: null } });
    render(<ListHost onError={vi.fn()} onMeetingUpdated={vi.fn()} onNotice={onNotice} onOpenMeeting={vi.fn()} selected={null} />);
    const modal = await openEdit();
    await within(modal).findByText(meetingScreen.roomKeepUnchecked("회의실 3"));
    expect(within(modal).getByText(meetingScreen.roomsFailed)).toBeTruthy();
    fireEvent.click(within(modal).getByRole("button", { name: /종료 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "16:30" }));
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(booked());
    fireEvent.click(saveButton(modal));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(meetingScreen.roomSyncUnchecked));
  });

  it("저장 때 Connect 에 닿지 못했다는 응답이면 「회의실 예약 시스템에 닿지 못했습니다」 (WP3 계약 고정 3)", async () => {
    const onNotice = vi.fn();
    vi.mocked(api.readMeeting).mockResolvedValue(booked());
    vi.mocked(api.readMeetingRooms).mockResolvedValue(rooms());
    vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [row({ meeting_id: "p1" })], past: { items: [], next_cursor: null } });
    render(<ListHost onError={vi.fn()} onMeetingUpdated={vi.fn()} onNotice={onNotice} onOpenMeeting={vi.fn()} selected={null} />);
    const modal = await openEdit();
    fireEvent.click(await within(modal).findByLabelText("회의실 1 (6인)"));
    vi.mocked(api.updateMeetingInfo).mockResolvedValue(record({ room_reservation: { status: "failed", room_name: null, reason: "reservation_unavailable" } }));
    fireEvent.click(saveButton(modal));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(meetingScreen.roomSyncFailed));
  });
});

/* WP3 수정 1 — 검수 W-6: 앞 동기화가 실패했어도 방 이름이 남아 있으면 기존 줄로 본다(조회 503 인 날에도 「기존 (확인 못 함)」) */
describe("WP3 수정 1 — 실패한 예약의 기존 방 (W-6)", () => {
  it("`room_reservation {status: failed, room_name}` + 조회 503 이면 「기존 — 회의실 3 (확인 못 함)」 에서 연다", async () => {
    vi.mocked(api.readMeeting).mockResolvedValue(record({ room_reservation: { status: "failed", room_name: "회의실 3", reason: "reservation_unavailable" } }));
    vi.mocked(api.readMeetingRooms).mockRejectedValue(new api.ApiError(503, "Service Unavailable", { code: "ROOM_SERVICE_UNAVAILABLE" }));
    renderList();
    const modal = await openEdit();
    const keep = (await within(modal).findByLabelText(meetingScreen.roomKeepUnchecked("회의실 3"))) as HTMLInputElement;
    expect(keep.checked).toBe(true);
    expect(keep.disabled).toBe(false);
    // 아무것도 안 바꾸면 저장할 것이 없다 — 조용히 「예약 없음」 으로 열려 예약을 지우지 않는다
    expect((within(modal).getByRole("button", { name: meetingScreen.save }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("방 이름이 없는 실패(`failed` + `room_name: null`)는 기존 줄이 없다", async () => {
    vi.mocked(api.readMeeting).mockResolvedValue(record({ room_reservation: { status: "failed", room_name: null, reason: "room_unavailable" } }));
    renderList();
    const modal = await openEdit();
    expect((await within(modal).findByLabelText(meetingScreen.noRoom) as HTMLInputElement).checked).toBe(true);
    expect(within(modal).queryByText(/기존 —/)).toBeNull();
  });
});

/* WP3 수정 2 — 재검수 W-r2-4 · W-r2-1(계약 고정 §7) */
describe("WP3 수정 2 — 확인 중인 예약의 기존 방 · 확인 중 409", () => {
  it("`needs_verification` + 방 이름도 기존 방이다 — 「기존 — 회의실 3」 에서 연다 (W-r2-4)", async () => {
    vi.mocked(api.readMeeting).mockResolvedValue(record({ room_reservation: { status: "needs_verification", room_name: "회의실 3", reason: "reservation_needs_verification" } }));
    vi.mocked(api.readMeetingRooms).mockRejectedValue(new api.ApiError(503, "Service Unavailable", { code: "ROOM_SERVICE_UNAVAILABLE" }));
    renderList();
    const modal = await openEdit();
    const keep = (await within(modal).findByLabelText(meetingScreen.roomKeepUnchecked("회의실 3"))) as HTMLInputElement;
    expect(keep.checked).toBe(true);
  });

  it("저장이 409 ROOM_RESERVATION_UNCONFIRMED 면 코드별 문구를 낸다 — 모달은 열린 채 (W-r2-1)", async () => {
    const onError = vi.fn();
    vi.mocked(api.readMeeting).mockResolvedValue(record({ room_reservation: { status: "booked", room_name: "회의실 3", reason: null } }));
    vi.mocked(api.readMeetingRooms).mockResolvedValue([
      { room_id: 3, name: "회의실 3", capacity: 6, available: true, current: true, unavailable_reason: null },
      { room_id: 1, name: "회의실 1 (6인)", capacity: 6, available: true, current: false, unavailable_reason: null },
    ]);
    vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [row({ meeting_id: "p1" })], past: { items: [], next_cursor: null } });
    render(<ListHost onError={onError} onMeetingUpdated={vi.fn()} onNotice={vi.fn()} onOpenMeeting={vi.fn()} selected={null} />);
    const modal = await openEdit();
    fireEvent.click(await within(modal).findByLabelText("회의실 1 (6인)"));
    vi.mocked(api.updateMeetingInfo).mockRejectedValue(new api.ApiError(409, "Conflict", { code: "ROOM_RESERVATION_UNCONFIRMED", message: "reservation is being confirmed" }));
    fireEvent.click(within(modal).getByRole("button", { name: meetingScreen.save }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(meetingScreen.saveErrors.ROOM_RESERVATION_UNCONFIRMED));
    expect(screen.getByRole("dialog", { name: meetingScreen.editInfo })).toBeTruthy();
  });
});
