import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  // ApiError 는 진짜를 쓴다 — 화면이 409 를 `instanceof` 로 가른다
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
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
  listMeetings: vi.fn(),
  readMeeting: vi.fn(),
  removeMeeting: vi.fn(),
  quickStartMeeting: vi.fn(),
  bookMeeting: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
}));

import { ApiError } from "../../lib/api";
import * as api from "../../lib/api";
import type { MeetingAgenda, MeetingRecord, MeetingRow } from "../../lib/viewModels";
import { roomReservationNotice } from "./BookingModal";
import { StrictMode, useState } from "react";
import type React from "react";

import { MeetingListPage } from "./MeetingListPage";
import { resetRoster } from "./roster";

const row = (over: Partial<MeetingRow> & { meeting_id: string }): MeetingRow => ({
  title: "주간 회의",
  starts_at: "2026-09-10T06:00:00Z",
  ends_at: "2026-09-10T07:00:00Z",
  location: "회의실 A",
  status: "done",
  viewer_relation: "attendee",
  created_by: "이건학",
  attendee_count: 4,
  ...over,
});

const agenda = (over: Partial<MeetingAgenda> & { agenda_id: string }): MeetingAgenda => ({
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "8월 정산 마감 현황",
  source: "manual",
  concluded: true,
  lines: [],
  todos: [],
  ...over,
});

const record = (over: Partial<MeetingRecord["meeting"]> = {}, agendas: MeetingAgenda[] = []): MeetingRecord => ({
  meeting: {
    meeting_id: "p1",
    title: "주간 회의",
    purpose: null,
    starts_at: "2026-09-10T06:00:00Z",
    ends_at: "2026-09-10T07:00:00Z",
    location: "회의실 A",
    status: "done",
    created_by: "이건학",
    attendees: [{ member_id: "1", display_name: "이건학" }],
    external_attendees: [],
    viewer_relation: "attendee",
    can_edit_info: true,
    can_edit_note: true,
    can_edit_agendas: true,
    can_write_memo: false,
    started_at: null,
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    last_saved_at: "2026-09-10T07:08:00Z",
    carried_from_meeting_id: null,
    ...over,
  },
  agendas,
});

/* 바퀴 6a: 이 칸의 머리 액션(회의 생성·빠른 시작)은 셸의 AppHeader 에 등록해 그린다(M-6).
   칸만 떼어 렌더하면 그 자리가 없으므로, 셸이 하는 일만 흉내 내는 얇은 집을 둔다. */
function ListHost(props: Parameters<typeof MeetingListPage>[0]) {
  const [actions, setActions] = useState<React.ReactNode>(null);
  return (
    <>
      {actions}
      <MeetingListPage {...props} onRegisterHeaderActions={setActions} />
    </>
  );
}

function renderList(upcoming: MeetingRow[], past: MeetingRow[], cursor: string | null = null) {
  vi.mocked(api.listMeetings).mockResolvedValue({ upcoming, past: { items: past, next_cursor: cursor } });
  const onOpenMeeting = vi.fn();
  const onNotice = vi.fn();
  render(<ListHost onError={vi.fn()} onNotice={onNotice} onOpenMeeting={onOpenMeeting} selected={null} />);
  return { onOpenMeeting, onNotice };
}

it("응답이 끊긴 회의실 변경은 자동 재시도 대신 확인 필요로 알린다", () => {
  expect(
    roomReservationNotice(
      record({
        room_reservation: {
          status: "needs_verification",
          room_name: "회의실 3 (6인)",
          reason: "reservation_needs_verification",
        },
      }),
    ),
  ).toBe("회의실 예약 결과를 확인해야 합니다 — 자동으로 다시 요청하지 않았습니다.");
});

it("예약 보상 중단은 실패로 숨기지 않고 취소 결과 확인이 필요하다고 알린다", () => {
  expect(
    roomReservationNotice(
      record({
        location: "회의실 3 (6인)",
        room_reservation: {
          status: "needs_verification",
          room_name: "회의실 3 (6인)",
          reason: "reservation_compensation_pending",
        },
      }),
    ),
  ).toBe("회의실 예약 취소 결과를 확인해야 합니다 — 자동으로 다시 예약하지 않았습니다.");
});

/** 명부가 비어 있어도 참석자 한 명은 세울 수 있다 — 이름을 치고 사외로 단다. */
function addGuest(modal: HTMLElement, name: string) {
  fireEvent.change(within(modal).getByRole("combobox"), { target: { value: name } });
  fireEvent.click(within(modal).getByRole("button", { name: `${name} 사외 참석자로 추가` }));
}

beforeEach(() => {
  resetRoster();
  vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
  vi.mocked(api.readMeetingMaterials).mockResolvedValue([]);
  vi.mocked(api.readMeetingShares).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SCR-105 회의 목록", () => {
  it("상태 여섯을 행마다 낸다 — 한 상태도 감추지 않는다", async () => {
    renderList(
      [row({ meeting_id: "u1", status: "scheduled" })],
      [
        row({ meeting_id: "p1", status: "in_progress" }),
        row({ meeting_id: "p2", status: "summarizing" }),
        row({ meeting_id: "p3", status: "done" }),
        row({ meeting_id: "p4", status: "failed" }),
        row({ meeting_id: "p5", status: "cancelled" }),
      ],
    );
    await screen.findByText("진행 중");
    for (const label of ["예정", "진행 중", "정리 중", "종료", "실패", "취소"]) {
      // 「예정」·「지난」 구획 제목과 겹치므로 상태 표기만 골라 센다
      // 바퀴 6bc: 카드의 상태는 여섯 다 배지다 — 구 `.status`/`.danger-text` 글자에서 `.scax-badge` 로 갔다
      expect(screen.getAllByText(label).some((node) => node.className.includes("scax-badge"))).toBe(true);
    }
  });

  it("공유받은 회의에는 「열람」이 상태와 나란히 선다", async () => {
    renderList([], [row({ meeting_id: "p1", viewer_relation: "shared" }), row({ meeting_id: "p2" })]);
    await screen.findAllByText("종료");
    expect(screen.getAllByText("열람")).toHaveLength(1);
  });

  it("주제를 안 채운 회의는 「제목 없는 회의」로 선다", async () => {
    renderList([], [row({ meeting_id: "p1", title: null })]);
    expect(await screen.findByText("제목 없는 회의")).toBeTruthy();
  });

  it("[삭제]는 「예정」 행에만 붙는다", async () => {
    renderList([row({ meeting_id: "u1", status: "scheduled" })], [row({ meeting_id: "p1", status: "done" })]);
    await screen.findAllByText("종료");
    expect(screen.getAllByRole("button", { name: "삭제" })).toHaveLength(1);
    // 바퀴 6bc: 시안의 카드는 예정에만 [수정]·[삭제] 둘을 나란히 둔다 — 지난 회의 카드에는 조작 자리가 없다
    expect(screen.getAllByRole("button", { name: "수정" })).toHaveLength(1);
  });

  it("삭제 확인은 갈래를 두지 않는다 — [회의 취소] 하나다", async () => {
    const { onNotice } = renderList([row({ meeting_id: "u1", status: "scheduled" })], []);
    vi.mocked(api.removeMeeting).mockResolvedValue(undefined);
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));
    expect(screen.getByText("회의를 취소할까요?")).toBeTruthy();
    const foot = document.querySelector(".scax-modal__foot") as HTMLElement;
    expect(within(foot).getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "회의록만 삭제" })).toBeNull();
    // 갈래가 없어도 닫을 길은 남는다 — 머리의 × 와 Esc (바퀴 6bc R-3)
    expect(screen.getByRole("button", { name: "닫기" })).toBeTruthy();
    fireEvent.click(within(foot).getByRole("button", { name: "회의 취소" }));
    await waitFor(() => expect(api.removeMeeting).toHaveBeenCalledWith("u1", "meeting"));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("회의를 취소했습니다."));
  });

  it("[더 보기]는 커서를 그대로 들고 다음 장을 부른다", async () => {
    renderList([], [row({ meeting_id: "p1" })], "cursor-2");
    await screen.findByRole("button", { name: "더 보기" });
    vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [], past: { items: [row({ meeting_id: "p2" })], next_cursor: null } });
    fireEvent.click(screen.getByRole("button", { name: "더 보기" }));
    await waitFor(() => expect(api.listMeetings).toHaveBeenCalledWith("cursor-2"));
  });
  /* 바퀴 6a M-4: 목록의 오른쪽 «회의록 미리보기 패널» 을 지웠다(시안에 없다).
     그 패널이 읽기 전용이라는 것을 지키던 검사라, 이제 «패널이 없다» 는 것을 지킨다 —
     회의록도 그 조작(공유·내보내기·업무 생성)도 목록 칸에는 서지 않는다. 고르면 3칸이 연다. */
  it("목록 칸에는 회의록도 그 조작도 없다 — 고르는 일만 한다", async () => {
    const { onOpenMeeting } = renderList([], [row({ meeting_id: "p1" })]);

    fireEvent.click((await screen.findAllByText("주간 회의"))[0]);
    // 고른 것은 «선택» 이다 — 목록 칸은 그 사실만 위로 올린다
    expect(onOpenMeeting).toHaveBeenCalledWith("p1");

    // 회의록 본문도, 그 위에서 하던 조작도 이 칸에는 없다
    expect(screen.queryByText("안건 1. 8월 정산 마감 현황")).toBeNull();
    expect(screen.queryByRole("button", { name: "공유" })).toBeNull();
    expect(screen.queryByRole("link", { name: "내보내기" })).toBeNull();
    expect(screen.queryByRole("button", { name: "업무 생성" })).toBeNull();
  });

  it("예약 모달에는 반복 칸이 없고, 회의실에 「가능」 판정이 없다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    expect(within(modal).queryByText(/반복/)).toBeNull();
    expect(within(modal).queryByText("가능")).toBeNull();
    // 칸 순서 — 주제 · 일시 · 목적 · 안건 · 참석자 · 장소
    // 바퀴 9: 구 `.field` 가 새 DS 의 `.scax-field` 로 갈렸다 — 짚는 것은 그대로 «칸 순서» 다
    const labels = [...modal.querySelectorAll(".form-stack > .scax-field > label")].map((node) =>
      (node.textContent ?? "").replace(/\s*\*.*$/, "").trim(),
    );
    expect(labels).toEqual(["회의명", "일시", "목적", "안건", "참석자", "장소"]);
  });

  it("[회의 시작]은 값을 묻지 않고 바로 연다", async () => {
    const { onOpenMeeting } = renderList([], []);
    vi.mocked(api.quickStartMeeting).mockResolvedValue(record({ meeting_id: "q1", status: "in_progress" }));
    fireEvent.click(await screen.findByRole("button", { name: "회의 시작" }));
    await waitFor(() => expect(onOpenMeeting).toHaveBeenCalledWith("q1"));
  });

  it("예약 모달의 날짜·시각은 공용 부품이다 — 브라우저 기본 칸을 쓰지 않는다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    expect(modal.querySelector('input[type="date"]')).toBeNull();
    expect(modal.querySelector("select")).toBeNull();
    // 날짜는 우리 달력을, 시각은 30분 눈금 목록을 연다
    expect(within(modal).getByRole("button", { name: /일시 달력 열기/ })).toBeTruthy();
    expect(within(modal).getByRole("button", { name: /일시 시작 시각/ })).toBeTruthy();
    expect(within(modal).getByRole("button", { name: /일시 종료 시각/ })).toBeTruthy();
  });

  it("회의실은 서버가 주는 목록이다 — 빈 목록이면 「회의실 선택 안 함」만 선다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    const rooms = [...modal.querySelectorAll<HTMLInputElement>('input[name="meeting-room"]')];
    // 잡을 수 없는 방을 지어내지 않는다
    expect(rooms).toHaveLength(1);
    expect(rooms[0].checked).toBe(true);
    expect(within(modal).getByText("회의실 선택 안 함")).toBeTruthy();
  });

  it("고른 회의실은 room_id 로, 「선택 안 함」은 null 로 나간다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([
      { room_id: 7, name: "5F 대회의실 (20인)", capacity: 20 },
      { room_id: 9, name: "6F 소회의실 (4인)", capacity: 4 },
    ]);
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    await within(modal).findByText("5F 대회의실 (20인)");

    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    addGuest(modal, "한서린");

    vi.mocked(api.bookMeeting).mockResolvedValue(record());
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));
    // 「선택 안 함」이 기본이라 그대로 보내면 null 이다 — 예약 시스템을 부르지 않는다
    await waitFor(() => expect(vi.mocked(api.bookMeeting).mock.calls[0][0].room_id).toBeNull());
    expect(vi.mocked(api.bookMeeting).mock.calls[0][0]).not.toHaveProperty("location");

    cleanup();
    vi.mocked(api.bookMeeting).mockClear();
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const again = await screen.findByRole("dialog", { name: "회의 예약" });
    await within(again).findByText("6F 소회의실 (4인)");
    fireEvent.change(within(again).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    addGuest(again, "한서린");
    fireEvent.click(within(again).getByLabelText("6F 소회의실 (4인)"));
    vi.mocked(api.bookMeeting).mockResolvedValue(record());
    fireEvent.click(within(again).getByRole("button", { name: "회의 생성" }));
    await waitFor(() => expect(vi.mocked(api.bookMeeting).mock.calls[0][0].room_id).toBe(9));
  });

  it("회의실을 못 잡아도 회의는 서고, 왜 비었는지 한 줄로 말한다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([{ room_id: 7, name: "5F 대회의실 (20인)", capacity: 20 }]);
    const { onNotice } = renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    await within(modal).findByText("5F 대회의실 (20인)");
    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    addGuest(modal, "한서린");
    fireEvent.click(within(modal).getByLabelText("5F 대회의실 (20인)"));

    let settle: (value: MeetingRecord) => void = () => {};
    vi.mocked(api.bookMeeting).mockReturnValue(new Promise<MeetingRecord>((resolve) => (settle = resolve)));
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));
    // 예약 시스템이 붙잡는 동안 단추가 무엇을 기다리는지 말한다 — 다시 걸지 않는다
    expect(await within(modal).findByRole("button", { name: "예약 중" })).toHaveProperty("disabled", true);

    const failed = record({ room_reservation: { status: "failed", room_name: "5F 대회의실 (20인)", reason: "room_unavailable" } });
    await act(async () => {
      settle(failed);
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith("그 시간엔 이미 예약된 회의실입니다 — 회의는 만들었고 장소는 비어 있습니다."),
    );
    // 회의는 이미 섰다 — 목록을 다시 읽는다
    await waitFor(() => expect(vi.mocked(api.listMeetings).mock.calls.length).toBeGreaterThan(1));
  });

  it("고른 방이 안 돼 다른 방으로 잡히면 어디로 잡혔는지 말한다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([{ room_id: 7, name: "5F 대회의실 (20인)", capacity: 20 }]);
    const { onNotice } = renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    await within(modal).findByText("5F 대회의실 (20인)");
    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    addGuest(modal, "한서린");
    fireEvent.click(within(modal).getByLabelText("5F 대회의실 (20인)"));

    vi.mocked(api.bookMeeting).mockResolvedValue(
      record({
        room_reservation: {
          status: "booked",
          room_name: "6F 소회의실 (4인)",
          reason: null,
          replaced: true,
          requested_room_name: "5F 대회의실 (20인)",
        },
      }),
    );
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("6F 소회의실 (4인)(으)로 예약됐습니다"));
  });

  it("회의실이 거절되면 회의가 서지 않는다 — 쓴 것은 그대로 두고 회의실만 다시 그린다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([
      { room_id: 7, name: "5F 대회의실 (20인)", capacity: 20 },
      { room_id: 9, name: "6F 소회의실 (4인)", capacity: 4 },
    ]);
    const { onNotice } = renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    await within(modal).findByText("5F 대회의실 (20인)");
    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    fireEvent.change(within(modal).getByPlaceholderText("이 회의를 왜 하는지 한 줄로"), { target: { value: "범위를 정한다" } });
    addGuest(modal, "한서린");
    fireEvent.click(within(modal).getByLabelText("5F 대회의실 (20인)"));

    vi.mocked(api.bookMeeting).mockRejectedValue(
      new ApiError(409, "Conflict", {
        code: "room_unavailable",
        message: "room is taken",
        available_rooms: [{ room_id: 11, name: "3F 회의실 (8인)", capacity: 8 }],
      }),
    );
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));

    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("회의실이 이미 예약되어 있습니다"));
    // 모달은 그대로 열려 있고 쓴 것이 남아 있다
    expect(screen.getByRole("dialog", { name: "회의 예약" })).toBeTruthy();
    expect((within(modal).getByPlaceholderText("회의명을 적으세요") as HTMLInputElement).value).toBe("주간 회의");
    expect((within(modal).getByPlaceholderText("이 회의를 왜 하는지 한 줄로") as HTMLInputElement).value).toBe("범위를 정한다");
    expect(within(modal).getByText("한서린")).toBeTruthy();
    // 회의실만 다시 그려진다 — 거절당한 방은 사라지고 고른 것은 풀린다
    expect(within(modal).getByText("3F 회의실 (8인)")).toBeTruthy();
    expect(within(modal).queryByText("5F 대회의실 (20인)")).toBeNull();
    const rooms = [...modal.querySelectorAll<HTMLInputElement>('input[name="meeting-room"]')];
    expect(rooms).toHaveLength(2);
    expect(rooms[0].checked).toBe(true);
  });

  it("응답을 받지 못한 생성 재시도는 같은 idempotency key를 다시 쓴다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([{ room_id: 7, name: "5F 대회의실 (20인)", capacity: 20 }]);
    renderList([], []);
    /* 바퀴 13: 머리의 여는 단추는 바퀴 6a 에서 「회의 생성」이 됐다(모달 제목만 「회의 예약」이다).
       main(#10)이 이 검사를 쓸 때의 이름이 남아 있어 시각과 무관하게 늘 깨지고 있었다 —
       짚는 것은 그대로 두고 이름만 지금 것으로 맞춘다. */
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    await within(modal).findByText("5F 대회의실 (20인)");
    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "응답 유실 회의" } });
    addGuest(modal, "한서린");
    fireEvent.click(within(modal).getByLabelText("5F 대회의실 (20인)"));
    vi.mocked(api.bookMeeting).mockRejectedValueOnce(new Error("응답을 받지 못했습니다"));

    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));
    await waitFor(() => expect(api.bookMeeting).toHaveBeenCalledTimes(1));
    const firstKey = vi.mocked(api.bookMeeting).mock.calls[0][1];

    vi.mocked(api.bookMeeting).mockResolvedValueOnce(record());
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));
    await waitFor(() => expect(api.bookMeeting).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.bookMeeting).mock.calls[1][1]).toBe(firstKey);
  });

  it("일시를 바꾸면 그 시간에 쓸 수 있는 방만 다시 받는다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    // 처음에는 파라미터 없이 전부 받는다
    await waitFor(() => expect(api.readMeetingRooms).toHaveBeenCalledWith(undefined));

    fireEvent.click(within(modal).getByRole("button", { name: /일시 시작 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "09:30" }));
    // 눈금을 하나씩 옮길 때마다 부르지 않는다 — 잠깐 기다렸다 한 번만 부른다
    expect(api.readMeetingRooms).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(api.readMeetingRooms).toHaveBeenCalledTimes(2));
    const range = vi.mocked(api.readMeetingRooms).mock.calls.at(-1)?.[0];
    expect(range?.starts_at).toContain("T09:30:00");
    // 시작을 옮기면 종료가 잡고 있던 간격만큼 따라간다
    expect(range?.ends_at).toContain("T10:30:00");
  });

  /* ★ 바퀴 13 — 자정을 넘는 회의. 예전에는 끝이 시작보다 «앞» 으로 읽혀 제출 단추가 굳었고,
     23:00~23:30 에 모달을 열면 기본값이 바로 그 꼴이라 그 시간대에는 아무 회의도 못 만들었다.
     이 검사는 시각을 **명시적으로 23:30 → 00:30 으로 만들어** 낮에 돌려도 그 회귀를 잡는다. */
  it("자정을 넘겨도 예약된다 — 끝 시각의 날짜가 하루 넘어간다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });

    fireEvent.click(within(modal).getByRole("button", { name: /일시 시작 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "21:00" }));
    fireEvent.click(within(modal).getByRole("button", { name: /일시 시작 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "23:30" }));
    fireEvent.click(within(modal).getByRole("button", { name: /일시 종료 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "00:30" }));

    // 끝이 다음 날이라는 것을 화면이 말한다
    expect(within(modal).getByText("다음 날")).toBeTruthy();

    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "야간 점검" } });
    addGuest(modal, "한서린");
    vi.mocked(api.bookMeeting).mockResolvedValue(record());
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));

    await waitFor(() => expect(api.bookMeeting).toHaveBeenCalled());
    const sent = vi.mocked(api.bookMeeting).mock.calls[0][0];
    const startDay = sent.starts_at.slice(0, 10);
    const endDay = sent.ends_at.slice(0, 10);
    expect(sent.starts_at).toContain("T23:30:00");
    expect(sent.ends_at).toContain("T00:30:00");
    // 날짜가 하루 넘어간다 — 끝이 시작보다 뒤다
    expect(new Date(endDay).getTime() - new Date(startDay).getTime()).toBe(24 * 60 * 60 * 1000);
    expect(new Date(sent.ends_at).getTime()).toBeGreaterThan(new Date(sent.starts_at).getTime());
  });

  /* 회의실 조회도 같은 계산을 써야 «그 시간대에 실제로 빈 방» 을 받는다 — 거꾸로 된 구간을 물으면 안 된다. */
  it("자정을 넘을 때 회의실 조회도 다음 날로 묻는다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    await waitFor(() => expect(api.readMeetingRooms).toHaveBeenCalledWith(undefined));

    /* 기본값이 우연히 23:30 인 시간대(23:00~23:30)에도 «값이 바뀌어» 다시 묻도록, 먼저 다른 시각으로
       옮겼다가 23:30 으로 돌아온다. 안 그러면 그 시간대에서는 아무것도 안 바뀌어 검사가 헛돈다. */
    fireEvent.click(within(modal).getByRole("button", { name: /일시 시작 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "21:00" }));
    fireEvent.click(within(modal).getByRole("button", { name: /일시 시작 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "23:30" }));
    fireEvent.click(within(modal).getByRole("button", { name: /일시 종료 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "00:30" }));

    await waitFor(() => {
      const range = vi.mocked(api.readMeetingRooms).mock.calls.at(-1)?.[0];
      expect(range?.starts_at).toContain("T23:30:00");
      expect(range?.ends_at).toContain("T00:30:00");
      expect(new Date(range!.ends_at).getTime()).toBeGreaterThan(new Date(range!.starts_at).getTime());
    });
  });

  it("예약 푸터는 [회의 생성] 하나다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    const foot = modal.querySelector(".modal-foot") as HTMLElement;
    expect(within(foot).getAllByRole("button")).toHaveLength(1);
    expect(within(foot).getByRole("button", { name: "회의 생성" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "회의실까지 예약" })).toBeNull();
  });

  it("명부에 없는 이름은 「직원 정보 없음」 아래에 사외로 단다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    fireEvent.change(within(modal).getByRole("combobox"), { target: { value: "이건학" } });

    // 줄 둘이다 — 명부에 없다는 말이 먼저고, 사외로 다는 것은 그 아래 고르는 줄이다
    const block = modal.querySelector(".popover-guest") as HTMLElement;
    expect(within(block).getByText("직원 정보 없음")).toBeTruthy();
    const add = within(block).getByRole("button");
    // 한 줄 텍스트로 이어 쓴다 — 아바타도 좌우 분리도 없다
    expect(add.textContent).toBe("이건학 사외 참석자로 추가");
    expect(add.querySelector(".avatar")).toBeNull();
    // 「직원 정보 없음」은 고르는 자리가 아니다
    expect(within(block).getAllByRole("button")).toHaveLength(1);
  });

  it("쓴 것이 있는 채로 닫으면 나가는지 한 번 묻는다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    fireEvent.click(within(modal).getByRole("button", { name: "닫기" }));
    expect(await screen.findByText("입력 정보는 저장되지 않습니다.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "나가기" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "버리기" })).toBeNull();
  });

  /* jsdom 은 스타일시트를 적용하지 않아 「포커스했을 때 무엇이 보이나」를 화면에서 잴 수 없다.
     규칙 자체를 검사한다 — 이 결정이 조용히 되돌아가는 것을 막는 자리다. */
  /* 「포커스는 칸 모양을 바꾸지 않는다」는 사용자가 구 DS 시절에 내린 결정이고, 이 검사가 그것을
     지켜 왔다 — 구 `styles.css` 안의 모든 `:focus` 규칙이 outline·box-shadow 를 안 켜는지 훑었다.

     ★ 바퀴 9-B 에서 그 검사의 전제가 무너졌다. 구 파일이 사라지면서 전역 규칙은 `shell.css` 로
     옮겨 갔고(아래에서 그대로 짚는다), **새 DS 는 그 결정을 정면으로 뒤집는다** —
     `:focus-visible` 에 `--scax-focus-ring` 을 일부러 그리는 규칙이 DS 원본 복사본 안에만 18곳이다
     (`components.css` 17 · `meetings.css` 의 `.scax-meeting-card` 1). 「아무 규칙도 안 켠다」를
     그대로 두면 DS 를 실은 순간 빨개지고, 파일을 골라 훑으면 그때부터 «빈 검사» 다.
     그래서 **지금도 참인 절반만** 남긴다: 브라우저 기본 링을 끄는 전역 규칙이 살아 있는가.
     나머지 절반(링을 다시 그릴 것인가)은 제품 판단이라 코디에게 올렸다. */
  it("브라우저 기본 포커스 링을 끄는 전역 규칙이 살아 있다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다. 파일을 읽는 것은 이 검사 하나뿐이다.
    const { readFileSync } = await import("node:fs");
    const shell: string = readFileSync("src/styles/shell.css", "utf8");
    expect(shell.replace(/\s+/g, "")).toContain(":focus,:focus-visible{outline:none}");
  });

  /* 높이를 재는 것은 jsdom 이 못 한다 — 기둥의 «규칙» 을 검사한다.
     한 칸이라도 빠지면 페이지가 늘어나고 목록이 패널을 밀어낸다. */
  it("목록은 패널 안에서만 스크롤한다 — 높이 기둥이 위에서 아래로 이어진다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
    const { readFileSync } = await import("node:fs");
    /* 바퀴 9-B: 기둥의 «위쪽» 이 구 `.canvas` 에서 새 셸로 옮겨 갔다(바퀴 2). 그래서 구 파일만
       읽으면 이 검사는 이미 죽은 규칙을 짚는 빈 검사가 된다 — 파일 여럿을 한꺼번에 훑는다. */
    const css: string = ["src/styles/shell.css", "src/styles/meetings.css", "src/styles/workspace.css"]
      .map((path: string) => readFileSync(path, "utf8") as string)
      .join("\n");
    // 공백을 걷고 견준다 — 규칙이 어떻게 띄어져 있든 같은 줄로 읽힌다
    const lines = css.split("\n").map((line: string) => line.replace(/\s+/g, ""));
    const rule = (selector: string) => lines.find((line: string) => line.startsWith(`${selector}{`)) ?? "";

    // 정해진 높이가 있어야 아래에서 나눌 「남는 높이」가 생긴다 — 이제 셸이 그 높이를 쥔다
    expect(rule(".scax-app-shell")).toContain("height:100vh");
    expect(rule(".scax-page-body")).toContain("min-height:0");
    expect(rule(".scax-page-body__content")).toContain("min-height:0");
    expect(rule(".meeting-surface")).toContain("min-height:0");
    // 행이 auto 면 그리드가 가장 큰 패널의 내용만큼 자란다
    expect(rule(".meeting-columns")).toContain("grid-template-rows:minmax(0,1fr)");
    expect(rule(".meeting-columns")).toContain("min-height:0");
    /* 4칸의 «아래쪽» 기둥. 예전에는 열을 감싸던 카드(.meeting-panel)가 그 자리였는데, 시안대로
       카드를 벗으면서 그 규칙이 죽었다 — 죽은 셀렉터를 짚으면 그때부터 빈 검사다.
       지금 기둥을 잇는 것은 레일 자신과 그 본문이다: 칸이 높이를 받고, 넘치는 것은 본문 «안» 에서
       스크롤한다. 한 칸이라도 빠지면 파일 목록이 칸을 밀어내고 [자료 첨부]가 화면 밖으로 나간다. */
    expect(rule(".scax-side-rail")).toContain("height:100%");
    expect(rule(".scax-side-rail")).toContain("min-height:0");
    expect(rule(".scax-side-rail__body")).toContain("min-height:0");
    expect(rule(".scax-side-rail__body")).toContain("overflow-y:auto");
  });

  /* 스크롤은 되지만 막대는 안 그린다 (사용자 결정). 전역 규칙이 실려 있지 않으면 화면이 줄무늬가
     되고, 반대로 이 규칙을 «overflow 숨김» 으로 오해해 고치면 스크롤 자체가 죽는다 —
     둘 다 아니라는 것을 한 자리에서 짚는다. */
  it("스크롤 막대는 안 그리되 스크롤은 살아 있다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
    const { readFileSync } = await import("node:fs");
    const index: string = readFileSync("src/styles/index.css", "utf8");
    expect(index).toContain('@import "./scrollbar.css";');

    const bar: string = (readFileSync("src/styles/scrollbar.css", "utf8") as string).replace(/\s+/g, "");
    expect(bar).toContain("*{scrollbar-width:none}");
    expect(bar).toContain("*::-webkit-scrollbar{width:0;height:0}");
    // 막대를 지우는 것이지 넘침을 자르는 것이 아니다 — overflow 를 건드리는 전역 규칙은 없다
    expect(bar).not.toContain("*{overflow");
  });

  /* box-sizing 전역 리셋 — 이것이 빠지면 `width:100%` + padding + border 인 칸이 전부 부모보다
     넓어진다. 목록 카드가 레일 오른쪽에서 잘리고 예약 모달에 가로 스크롤이 생기던 원인이다.
     jsdom 은 그 넘침을 재지 못하므로 규칙 자체를 짚는다. */
  it("box-sizing 전역 리셋이 살아 있다 — 칸이 부모보다 넓어지지 않는다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
    const { readFileSync } = await import("node:fs");
    const shell: string = (readFileSync("src/styles/shell.css", "utf8") as string).replace(/\s+/g, "");
    expect(shell).toContain("*,*::before,*::after{box-sizing:border-box}");
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   [회의 시작] 한 번 = 회의 하나 (2026-09-14 사용자 보고).

   클릭 한 번에 `POST /api/meetings/quick-start` 가 1ms 간격으로 **두 번** 나가고 회의가 둘 생겼다.
   원인은 `setBusy` 의 «함수형 업데이터 안» 에서 서버를 부른 것이다 — 그 자리는 React 가 순수하다고
   전제하므로 개발 모드(StrictMode)가 일부러 두 번 돌려 본다. 두 번 다 «같은 current(false)» 를 보니
   `if (current) return current` 가드도 소용이 없었다.

   **그래서 이 검사는 StrictMode 로 렌더한다.** 그냥 render 하면 업데이터가 한 번만 돌아
   버그가 있어도 초록이다 — 잠금이 아니라 «검사» 가 거짓말을 하게 된다.
   ════════════════════════════════════════════════════════════════════════════ */

/** 셸의 머리 자리를 흉내 내되 **StrictMode 안에서** 세운다. */
function renderListStrict() {
  vi.mocked(api.listMeetings).mockResolvedValue({ upcoming: [], past: { items: [], next_cursor: null } });
  const onOpenMeeting = vi.fn();
  const onError = vi.fn();
  render(
    <StrictMode>
      <ListHost onError={onError} onNotice={vi.fn()} onOpenMeeting={onOpenMeeting} selected={null} />
    </StrictMode>,
  );
  return { onError, onOpenMeeting };
}

const startButton = () => screen.findByRole("button", { name: "회의 시작" });

describe("[회의 시작] — 한 번 누르면 회의 하나다", () => {
  it("StrictMode 에서도 클릭 한 번에 quick-start 는 한 번만 나간다", async () => {
    const { onOpenMeeting } = renderListStrict();
    vi.mocked(api.quickStartMeeting).mockResolvedValue(record({ meeting_id: "q1", status: "in_progress" }));

    fireEvent.click(await startButton());

    await waitFor(() => expect(onOpenMeeting).toHaveBeenCalledWith("q1"));
    // 여기가 이 검사의 전부다 — 두 번 나가면 회의가 둘 생긴다
    expect(api.quickStartMeeting).toHaveBeenCalledTimes(1);
    expect(onOpenMeeting).toHaveBeenCalledTimes(1);
  });

  it("응답을 기다리는 동안 연타해도 한 번만 나간다 — 잠금은 렌더를 기다리지 않는다", async () => {
    const { onOpenMeeting } = renderListStrict();
    /* 응답을 붙잡아 둔다 — 그 사이의 클릭은 «아직 busy 가 커밋되기 전» 이다.
       busy state 하나로 막았다면 여기서 두 번째·세 번째가 그대로 통과한다. */
    let release: (value: MeetingRecord) => void = () => {};
    vi.mocked(api.quickStartMeeting).mockReturnValue(new Promise<MeetingRecord>((resolve) => { release = resolve; }));

    const button = await startButton();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);
    expect(api.quickStartMeeting).toHaveBeenCalledTimes(1);

    await act(async () => {
      release(record({ meeting_id: "q1", status: "in_progress" }));
    });
    await waitFor(() => expect(onOpenMeeting).toHaveBeenCalledWith("q1"));
    expect(api.quickStartMeeting).toHaveBeenCalledTimes(1);
    expect(onOpenMeeting).toHaveBeenCalledTimes(1);
  });

  it("실패하면 그 사실을 내고 잠금이 풀린다 — 다시 누르면 다시 간다", async () => {
    const { onError, onOpenMeeting } = renderListStrict();
    vi.mocked(api.quickStartMeeting).mockRejectedValue(new Error("서버가 거절했습니다"));

    const button = await startButton();
    fireEvent.click(button);
    await waitFor(() => expect(onError).toHaveBeenCalledWith("서버가 거절했습니다"));
    expect(api.quickStartMeeting).toHaveBeenCalledTimes(1);
    // 실패는 회의를 열지 않는다
    expect(onOpenMeeting).not.toHaveBeenCalled();

    // 잠긴 채로 남으면 이 화면에서 회의를 다시 시작할 길이 없다
    vi.mocked(api.quickStartMeeting).mockResolvedValue(record({ meeting_id: "q2", status: "in_progress" }));
    fireEvent.click(await startButton());
    await waitFor(() => expect(onOpenMeeting).toHaveBeenCalledWith("q2"));
    expect(api.quickStartMeeting).toHaveBeenCalledTimes(2);
  });

  /*
   * 예약은 같은 원인이 아니다 — `submit()` 은 업데이터를 안 쓰고, 같은 입력이면 **같은
   * `Idempotency-Key`** 를 다시 실어 보낸다 (`rules.md` 「재전송은 영수증이다」).
   * 그래서 두 번 나가도 서버가 회의를 둘 만들지 않는다. 그 열쇠가 사라지면 빠른 시작과 같은
   * 증상이 예약에서도 나므로 여기서 잠근다.
   */
  it("예약은 재전송해도 같은 영수증을 들고 간다 — 회의가 둘 서지 않는다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 생성" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });

    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "같은 회의" } });
    addGuest(modal, "한서린");

    vi.mocked(api.bookMeeting).mockRejectedValueOnce(new Error("한 번 실패"));
    const create = within(modal).getByRole("button", { name: "회의 생성" });
    fireEvent.click(create);
    await waitFor(() => expect(api.bookMeeting).toHaveBeenCalledTimes(1));

    vi.mocked(api.bookMeeting).mockResolvedValue(record({ meeting_id: "b1" }));
    fireEvent.click(within(modal).getByRole("button", { name: "회의 생성" }));
    await waitFor(() => expect(api.bookMeeting).toHaveBeenCalledTimes(2));

    // 입력이 그대로면 열쇠도 그대로다 — 서버가 같은 요청으로 읽는다
    const [, firstKey] = vi.mocked(api.bookMeeting).mock.calls[0];
    const [, secondKey] = vi.mocked(api.bookMeeting).mock.calls[1];
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBe(firstKey);
  });
});
