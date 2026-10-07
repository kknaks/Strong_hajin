import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  readMeetingRooms: vi.fn(),
}));

import * as api from "../../lib/api";
import { meetingScreen } from "../../lib/labels";
import type { MeetingRoom } from "../../lib/viewModels";
import { RoomSelect, type RoomChoice, type RoomQuery, type RoomSelectStatus } from "./RoomSelect";

/*
 * WORK-012 WP3-FE — 회의실 셀렉트 부품 하나(SPEC-010 §2.2 · §4.1 · WP3 계약 고정 4).
 * 상태 넷(가용 있음 · 가용 없음 · 조회 실패 · 기존 방 못 씀)과 수정 모양(맨 위 기존 줄) · 다시 받기(300ms) · 고른 방이 빠짐 · W-3 · 거절 목록.
 */

const query: RoomQuery = { starts_at: "2026-10-08T15:00:00+09:00", ends_at: "2026-10-08T16:00:00+09:00", people: 3 };
const room = (over: Partial<MeetingRoom> & { room_id: number; name: string }): MeetingRoom => ({ capacity: 6, available: true, current: false, unavailable_reason: null, ...over });

let lastStatus: RoomSelectStatus | null = null;

function Host({
  initial = "none",
  meetingId = null,
  currentName = null,
  requestedName = null,
  refused = null,
  q = query,
}: {
  initial?: RoomChoice;
  meetingId?: string | null;
  currentName?: string | null;
  requestedName?: string | null;
  refused?: MeetingRoom[] | null;
  q?: RoomQuery | null;
}) {
  const [value, setValue] = useState<RoomChoice>(initial);
  return (
    <>
      <output data-testid="value">{value}</output>
      <RoomSelect
        currentName={currentName}
        meetingId={meetingId}
        onChange={setValue}
        onStatus={(status) => (lastStatus = status)}
        query={q}
        refused={refused}
        requestedName={requestedName}
        value={value}
      />
    </>
  );
}

const radios = () => [...document.querySelectorAll<HTMLInputElement>('input[name="meeting-room"]')];
const labels = () => radios().map((input) => input.closest("label")?.textContent ?? "");
const value = () => screen.getByTestId("value").textContent;

beforeEach(() => {
  lastStatus = null;
  vi.mocked(api.readMeetingRooms).mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("회의실 셀렉트 — 상태 넷", () => {
  it("가용 있음 — 「회의실 예약 없음」 이 기본이고 그 시간·인원의 방만 선다 · 조건(people·meeting_id)을 실어 묻는다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 1, name: "회의실 1 (6인)" }), room({ room_id: 2, name: "회의실 2 (4인)" })]);
    render(<Host />);
    await screen.findByText("회의실 1 (6인)");
    expect(labels()).toEqual(["회의실 예약 없음", "회의실 1 (6인)", "회의실 2 (4인)"]);
    expect(radios()[0].checked).toBe(true);
    expect(api.readMeetingRooms).toHaveBeenCalledWith({ ...query, meeting_id: null });
    fireEvent.click(screen.getByLabelText("회의실 2 (4인)"));
    expect(value()).toBe("2");
    expect(lastStatus).toEqual({ state: "ok", blocked: false, reason: null });
  });

  it("가용 없음(조회는 됨) — 「예약 없음」 만 서고 「이 시간·인원에 예약 가능한 회의실이 없습니다」", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
    render(<Host />);
    expect(await screen.findByText(meetingScreen.roomsEmpty)).toBeTruthy();
    expect(labels()).toEqual(["회의실 예약 없음"]);
    expect(screen.queryByText(meetingScreen.roomsFailed)).toBeNull();
  });

  it("조회 실패(503 ROOM_SERVICE_UNAVAILABLE) — 「가용 없음」 과 다른 문구 + [다시 시도] 가 다시 묻는다", async () => {
    vi.mocked(api.readMeetingRooms).mockRejectedValueOnce(new api.ApiError(503, "Service Unavailable", { code: "ROOM_SERVICE_UNAVAILABLE" }));
    render(<Host />);
    expect(await screen.findByText(meetingScreen.roomsFailed)).toBeTruthy();
    expect(screen.queryByText(meetingScreen.roomsEmpty)).toBeNull();
    expect(lastStatus?.state).toBe("failed");

    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 1, name: "회의실 1 (6인)" })]);
    fireEvent.click(screen.getByRole("button", { name: meetingScreen.roomsRetry }));
    expect(await screen.findByText("회의실 1 (6인)")).toBeTruthy();
    expect(api.readMeetingRooms).toHaveBeenCalledTimes(2);
  });

  it("기존 방을 새 조건에 못 씀(수정) — 맨 위 줄이 비활성 + 이유 · 선택은 「예약 없음」 으로 옮겨 가지 않고 막힌 이유를 낸다 (H-2 · OQ-1016)", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([
      room({ room_id: 3, name: "회의실 3 (4인)", capacity: 4, available: false, current: true, unavailable_reason: "capacity" }),
      room({ room_id: 1, name: "회의실 1 (6인)" }),
    ]);
    render(<Host currentName="회의실 3 (4인)" initial="keep" meetingId="m1" />);
    // 기존 줄은 목록이 오기 전에도(이름으로) 서고, 목록이 오면 비활성으로 바뀐다
    await waitFor(() => expect(radios()[0].disabled).toBe(true));
    const keep = radios()[0];
    expect(keep.disabled).toBe(true);
    expect(keep.checked).toBe(true);
    expect(keep.closest("label")?.textContent).toContain("새 인원보다 작은 방");
    expect(value()).toBe("keep");
    expect(lastStatus).toEqual({ state: "ok", blocked: true, reason: meetingScreen.roomPickAgain });
    // 다른 방을 고르면 풀린다
    fireEvent.click(screen.getByLabelText("회의실 1 (6인)"));
    await waitFor(() => expect(lastStatus?.blocked).toBe(false));
  });
});

describe("회의실 셀렉트 — 수정 모양", () => {
  it("기존 방을 쓸 수 있으면 맨 위 「기존 — 회의실 N (변경 안 함)」 · 구분선 · 「예약 없음」 · 가용 목록 · 조회에 meeting_id 를 싣는다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([
      room({ room_id: 3, name: "회의실 3", current: true }),
      room({ room_id: 1, name: "회의실 1 (6인)" }),
    ]);
    render(<Host currentName="회의실 3" initial="keep" meetingId="m1" />);
    await screen.findByText(meetingScreen.roomKeep("회의실 3"));
    expect(labels()).toEqual([meetingScreen.roomKeep("회의실 3"), "회의실 예약 없음", "회의실 1 (6인)"]);
    expect(document.querySelector(".meeting-room-sep")).toBeTruthy();
    expect(radios()[0].disabled).toBe(false);
    expect(api.readMeetingRooms).toHaveBeenCalledWith({ ...query, meeting_id: "m1" });
    expect(lastStatus?.blocked).toBe(false);
  });

  it("시간이 겹쳐 못 쓰면 이유는 「새 시간에 예약 불가」", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 3, name: "회의실 3", current: true, available: false, unavailable_reason: "time_conflict" })]);
    render(<Host currentName="회의실 3" initial="keep" meetingId="m1" />);
    await screen.findByText("새 시간에 예약 불가");
  });

  it("조회 실패면 기존 줄은 「(확인 못 함)」 으로 고를 수 있다 (S-3)", async () => {
    vi.mocked(api.readMeetingRooms).mockRejectedValue(new api.ApiError(503, "Service Unavailable", { code: "ROOM_SERVICE_UNAVAILABLE" }));
    render(<Host currentName="회의실 3" initial="keep" meetingId="m1" />);
    await screen.findByText(meetingScreen.roomKeepUnchecked("회의실 3"));
    expect(radios()[0].disabled).toBe(false);
    expect(lastStatus).toEqual({ state: "failed", blocked: false, reason: null });
  });

  it("원래 방이 없던 회의면 기존 줄도 없다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 1, name: "회의실 1 (6인)" })]);
    render(<Host meetingId="m1" />);
    await screen.findByText("회의실 1 (6인)");
    expect(labels()).toEqual(["회의실 예약 없음", "회의실 1 (6인)"]);
    expect(document.querySelector(".meeting-room-sep")).toBeNull();
  });
});

describe("회의실 셀렉트 — 다시 받기 · 빠진 방 · 불러오기 · 거절", () => {
  it("조건이 바뀌면 300ms 기다렸다 한 번 다시 받는다", async () => {
    vi.useFakeTimers();
    vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
    const { rerender } = render(<Host q={query} />);
    await act(async () => undefined);
    expect(api.readMeetingRooms).toHaveBeenCalledTimes(1);
    rerender(<Host q={{ ...query, people: 4 }} />);
    rerender(<Host q={{ ...query, people: 5 }} />);
    await act(async () => vi.advanceTimersByTime(299));
    expect(api.readMeetingRooms).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTime(1));
    expect(api.readMeetingRooms).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.readMeetingRooms).mock.calls.at(-1)?.[0]?.people).toBe(5);
  });

  it("고른 방이 새 목록에서 빠지면 조용히 「예약 없음」 이 되지 않고 이유를 말하며 막는다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValueOnce([room({ room_id: 1, name: "회의실 1 (6인)" })]);
    const { rerender } = render(<Host q={query} />);
    fireEvent.click(await screen.findByLabelText("회의실 1 (6인)"));
    vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
    rerender(<Host q={{ ...query, people: 9 }} />);
    await screen.findByText(meetingScreen.roomPickedGone("1"), {}, { timeout: 2000 });
    expect(value()).toBe("1");
    expect(lastStatus?.blocked).toBe(true);
  });

  it("불러온 지난 회의의 방 — 쓸 수 있으면 미리 고르고, 못 쓰면 이유를 한 줄로 (WP1 검수 W-3)", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 7, name: "5F 대회의실" })]);
    render(<Host requestedName="5F 대회의실" />);
    await waitFor(() => expect(value()).toBe("7"));
    cleanup();
    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 1, name: "회의실 1 (6인)" })]);
    render(<Host requestedName="5F 대회의실" />);
    expect(await screen.findByText(meetingScreen.roomCarriedGone("5F 대회의실"))).toBeTruthy();
    expect(value()).toBe("none");
  });

  it("저장이 409 로 거절되면 그때 가능한 방만으로 다시 선다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([room({ room_id: 1, name: "회의실 1 (6인)" })]);
    const { rerender } = render(<Host />);
    await screen.findByText("회의실 1 (6인)");
    rerender(<Host refused={[{ room_id: 11, name: "3F 회의실 (8인)", capacity: 8 }]} />);
    expect(await screen.findByText("3F 회의실 (8인)")).toBeTruthy();
    expect(screen.queryByText("회의실 1 (6인)")).toBeNull();
  });
});
