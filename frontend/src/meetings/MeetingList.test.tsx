import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", async (actual) => ({
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

import { ApiError } from "../api";
import * as api from "../api";
import type { MeetingAgenda, MeetingRecord, MeetingRow } from "../viewModels";
import { roomReservationNotice } from "./BookingModal";
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

function renderList(upcoming: MeetingRow[], past: MeetingRow[], cursor: string | null = null) {
  vi.mocked(api.listMeetings).mockResolvedValue({ upcoming, past: { items: past, next_cursor: cursor } });
  const onOpenMeeting = vi.fn();
  const onNotice = vi.fn();
  render(<MeetingListPage onError={vi.fn()} onNotice={onNotice} onOpenMeeting={onOpenMeeting} />);
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
      expect(screen.getAllByText(label).some((node) => node.className.includes("status") || node.className.includes("danger-text"))).toBe(true);
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
  });

  it("삭제 확인은 갈래를 두지 않는다 — [회의 취소] 하나다", async () => {
    const { onNotice } = renderList([row({ meeting_id: "u1", status: "scheduled" })], []);
    vi.mocked(api.removeMeeting).mockResolvedValue(undefined);
    fireEvent.click(await screen.findByRole("button", { name: "삭제" }));
    expect(screen.getByText("회의를 취소할까요?")).toBeTruthy();
    const foot = document.querySelector(".modal-foot") as HTMLElement;
    expect(within(foot).getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "회의록만 삭제" })).toBeNull();
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

  it("읽기 패널에는 조작 버튼도 후보 건수 줄도 없다", async () => {
    renderList([], [row({ meeting_id: "p1" })]);
    vi.mocked(api.readMeeting).mockResolvedValue(
      record({}, [
        agenda({
          agenda_id: "a1",
          lines: [{ line_id: "l1", track: "final", order: 1, text: "마감은 09-12까지 끝낸다.", author: "AI", at_ms: null, evidence: [{ start_ms: 1000, end_ms: 2000 }] }],
          todos: [
            {
              provisional: false,
              todo_id: "t1",
              agenda_id: "a1",
              title: "정산 초안 회람",
              description: "",
              due_candidate: "09-11",
              checklist_candidate: [],
              reference: { meeting_id: "m1", agenda_id: "a1", line_ids: [] },
              linked: null,
            },
          ],
        }),
      ]),
    );
    fireEvent.click((await screen.findAllByText("주간 회의"))[0]);
    expect(await screen.findByText("안건 1. 8월 정산 마감 현황")).toBeTruthy();
    expect(screen.getByText("마감은 09-12까지 끝낸다.")).toBeTruthy();
    expect(screen.getByText("다음 할 일")).toBeTruthy();
    expect(screen.getByText("정산 초안 회람")).toBeTruthy();
    // 담당 후보 칸은 없다 (D19-3) — 기한만 선다
    expect(screen.getByText("09-11")).toBeTruthy();
    expect(screen.queryByText(/박도윤/)).toBeNull();
    // 읽기 전용이다 — 승격도, 후보 지우기도, 근거 건수 줄도 없다 (X-133)
    expect(screen.queryByRole("button", { name: "업무 생성" })).toBeNull();
    expect(screen.queryByText("근거")).toBeNull();
    expect(screen.queryByText(/후속업무 후보 \d+건/)).toBeNull();
    // 패널에서 하는 조작은 [공유]·[내보내기]·[회의록 열기] 셋뿐이다 — [다음 회의 예약]은 상세의 것이다
    expect(screen.queryByRole("button", { name: "다음 회의 예약" })).toBeNull();
    expect(screen.getByRole("button", { name: "공유" })).toBeTruthy();
    // 내보내기는 단추가 아니라 링크다 — 받는 것은 브라우저가 한다
    const exportLink = screen.getByRole("link", { name: "내보내기" });
    expect(exportLink.getAttribute("href")).toBe("/api/meetings/p1/export?format=html");
    expect(screen.queryByRole("button", { name: "내보내기" })).toBeNull();
    expect(screen.getByRole("button", { name: "회의록 열기" })).toBeTruthy();
  });

  it("예약 모달에는 반복 칸이 없고, 회의실에 「가능」 판정이 없다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    expect(within(modal).queryByText(/반복/)).toBeNull();
    expect(within(modal).queryByText("가능")).toBeNull();
    // 칸 순서 — 주제 · 일시 · 목적 · 안건 · 참석자 · 장소
    const labels = [...modal.querySelectorAll(".form-stack > .field > label")].map((node) =>
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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

  it("예약 푸터는 [회의 생성] 하나다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    const foot = modal.querySelector(".modal-foot") as HTMLElement;
    expect(within(foot).getAllByRole("button")).toHaveLength(1);
    expect(within(foot).getByRole("button", { name: "회의 생성" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "회의실까지 예약" })).toBeNull();
  });

  it("명부에 없는 이름은 「직원 정보 없음」 아래에 사외로 단다", async () => {
    renderList([], []);
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "회의 예약" }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의" } });
    fireEvent.click(within(modal).getByRole("button", { name: "닫기" }));
    expect(await screen.findByText("입력 정보는 저장되지 않습니다.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "나가기" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "버리기" })).toBeNull();
  });

  /* jsdom 은 스타일시트를 적용하지 않아 「포커스했을 때 무엇이 보이나」를 화면에서 잴 수 없다.
     규칙 자체를 검사한다 — 이 결정이 조용히 되돌아가는 것을 막는 자리다. */
  it("포커스는 칸 모양을 바꾸지 않는다 — 어떤 규칙도 포커스에 테두리·그림자를 두지 않는다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다. 파일을 읽는 것은 이 검사 하나뿐이다.
    const { readFileSync } = await import("node:fs");
    const css: string = readFileSync("src/styles.css", "utf8");
    expect(css).toContain(":focus, :focus-visible { outline: none; }");
    const focusRules = css
      .split("\n")
      .filter((line: string) => /:focus(-visible|-within)?\s*[,{]/.test(line) && !line.trim().startsWith("/*"));
    for (const rule of focusRules) {
      // 공백을 걷고 본다 — `outline:` 바로 뒤가 `none` 이 아니면 무언가를 «켜는» 규칙이다
      const value = rule.replace(/\s+/g, "");
      expect(value).not.toMatch(/outline:(?!none)/);
      expect(value).not.toMatch(/box-shadow:(?!none)/);
      expect(value).not.toMatch(/border-color:var\(--(accent|action)/);
    }
  });

  /* 높이를 재는 것은 jsdom 이 못 한다 — 기둥의 «규칙» 을 검사한다.
     한 칸이라도 빠지면 페이지가 늘어나고 목록이 패널을 밀어낸다. */
  it("목록은 패널 안에서만 스크롤한다 — 높이 기둥이 위에서 아래로 이어진다", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
    const { readFileSync } = await import("node:fs");
    const css: string = readFileSync("src/styles.css", "utf8");
    // 공백을 걷고 견준다 — 규칙이 어떻게 띄어져 있든 같은 줄로 읽힌다
    const lines = css.split("\n").map((line: string) => line.replace(/\s+/g, ""));
    const rule = (selector: string) => lines.find((line: string) => line.startsWith(`${selector}{`)) ?? "";

    // 정해진 높이가 있어야 아래에서 나눌 「남는 높이」가 생긴다
    expect(rule(".canvas.full-height")).toContain("height:100vh");
    expect(rule(".canvas.full-height>.page-surface")).toContain("min-height:0");
    expect(rule(".meeting-surface")).toContain("min-height:0");
    // 행이 auto 면 그리드가 가장 큰 패널의 내용만큼 자란다
    expect(rule(".meeting-columns")).toContain("grid-template-rows:minmax(0,1fr)");
    expect(rule(".meeting-columns")).toContain("min-height:0");
    expect(rule(".meeting-panel")).toContain("min-height:0");
    expect(rule(".meeting-panel")).toContain("overflow:hidden");
  });
});
