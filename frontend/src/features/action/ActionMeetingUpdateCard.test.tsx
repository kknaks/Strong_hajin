import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  ...((await actual()) as object),
  readMeetingRooms: vi.fn(),
}));

import * as api from "../../lib/api";
import { axDraftCard, meetingScreen } from "../../lib/labels";
import type { ActionItem } from "../../lib/viewModels";
import { pickRoom, roomOptions, roomTrigger, roomValue, waitForRoom } from "../meetings/roomSelectTestKit";
import { ActionMeetingUpdateCard } from "./ActionMeetingUpdateCard";

/*
 * WORK-012 WP3-FE — AX 회의 수정 카드(SPEC-010 §2.4 · §4.3 · WP3 계약 고정 1·2).
 * 예전엔 결과 카드(`ActionResultCard`)라 고칠 칸이 없었다 — 이제 편집 계약(`editor="meeting_update"`)으로 고칠 수 있는 카드.
 * 장소 글자 칸 없음 · 회의실은 공용 셀렉트(수정 모양) · 초안 저장·회차 없음 · [등록]의 draft = 바뀐 칸만 + `room`.
 */

const updateAction = (over: Partial<ActionItem> = {}, values: Record<string, unknown> = {}): ActionItem => ({
  action_id: "update-1",
  conversation_id: "conversation-1",
  turn_id: "turn-1",
  action_type: "meeting.info.update",
  title: "회의 수정 확인",
  subject: "주간 회의",
  operation_label: "회의 수정",
  state: "pending",
  version: 1,
  payload_summary: "회의 수정: 주간 회의",
  result: null,
  audit_ref: null,
  preview: [],
  commands: [
    { id: "confirm", label: "이 내용으로 회의 수정", tone: "primary" },
    { id: "reject", label: "거절", tone: "neutral" },
  ],
  edit_contract: {
    editor: "meeting_update",
    base_submission_version: 2,
    values: {
      meeting_id: "m1",
      title: "주간 회의",
      purpose: "점검",
      starts_at: "2026-10-08T06:00:00Z",
      ends_at: "2026-10-08T07:00:00Z",
      attendee_ids: ["jiho"],
      external_attendees: [],
      room_id: 3,
      room_name: "회의실 3",
      ...values,
    },
    fields: [{ id: "attendee_ids", label: "참석자", type: "multi_select", required: false, editable: true, options: [{ value: "jiho", label: "지호" }, { value: "mina", label: "민아" }] }],
  },
  ...over,
});

const rooms = [
  { room_id: 3, name: "회의실 3", capacity: 6, available: true, current: true, unavailable_reason: null },
  { room_id: 1, name: "회의실 1 (8인)", capacity: 8, available: true, current: false, unavailable_reason: null },
];

function renderCard(action = updateAction(), onCommand = vi.fn(async (_command: string, _payload?: Record<string, unknown>) => undefined), onOpenMeeting = vi.fn()) {
  render(<ActionMeetingUpdateCard action={action} onCommand={onCommand} onOpenMeeting={onOpenMeeting} />);
  return { onCommand, onOpenMeeting };
}

const card = () => screen.getByRole("region", { name: /AX 회의 수정/ });
const confirmButton = () => within(card()).getByRole("button", { name: axDraftCard.confirm }) as HTMLButtonElement;

beforeEach(() => vi.mocked(api.readMeetingRooms).mockResolvedValue(rooms));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AX 회의 수정 카드 — 고칠 수 있는 카드", () => {
  it("칸은 편집 계약 값으로 차 있고 · 장소 글자 칸이 없으며 · 회의실은 맨 위 「기존」 줄(수정 모양) · 회차·초안 저장이 없다", async () => {
    renderCard();
    const region = card();
    expect(within(region).getByText(axDraftCard.badge)).toBeTruthy();
    expect(within(region).getByText(axDraftCard.updateKind)).toBeTruthy();
    expect(within(region).queryByText(/회차/)).toBeNull();
    expect((within(region).getByLabelText(/회의명/) as HTMLInputElement).value).toBe("주간 회의");
    // 장소 «글자 칸» 이 없다 — 「장소」 는 회의실 셀렉트(드롭다운 · 2루프 E-3)의 이름일 뿐이다
    expect(within(region).queryByRole("textbox", { name: "장소" })).toBeNull();
    expect(roomTrigger(region).classList.contains("select-trigger")).toBe(true);
    expect(within(region).queryByRole("button", { name: axDraftCard.edit })).toBeNull();
    await waitForRoom(region, meetingScreen.roomKeep("회의실 3"));
    expect(api.readMeetingRooms).toHaveBeenCalledWith(expect.objectContaining({ meeting_id: "m1", people: 1 }));
  });

  it("고친 것이 없으면 [등록]은 초안 없이 회차만 싣는다", async () => {
    const { onCommand } = renderCard();
    await waitForRoom(card(), meetingScreen.roomKeep("회의실 3"));
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2 }));
  });

  it("[등록]의 draft 는 바뀐 칸만 — 제목과 방을 바꾸면 `title` 과 `room: {room_id}` 둘뿐이다", async () => {
    const { onCommand } = renderCard();
    fireEvent.change(within(card()).getByLabelText(/회의명/), { target: { value: "주간 회의 (옮김)" } });
    await pickRoom(card(), "회의실 1 (8인)");
    fireEvent.click(confirmButton());
    await waitFor(() =>
      expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2, draft: { title: "주간 회의 (옮김)", room: { room_id: 1 } } }),
    );
  });

  it("「회의실 예약 없음」 을 고르면 `room: {room_id: null}` · 시각을 바꾸면 시작·종료 둘 다", async () => {
    const { onCommand } = renderCard();
    await pickRoom(card(), meetingScreen.noRoom);
    fireEvent.click(within(card()).getByRole("button", { name: /종료 시각/ }));
    fireEvent.click(await screen.findByRole("option", { name: "16:30" }));
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    const payload = onCommand.mock.calls[0][1] as { draft: Record<string, unknown> };
    expect(payload.draft).toEqual({ starts_at: "2026-10-08T15:00:00+09:00", ends_at: "2026-10-08T16:30:00+09:00", room: { room_id: null } });
  });

  it("방이 없던 회의는 「예약 없음」 에서 열리고 기존 줄이 없다 — 그대로 두면 `room` 을 싣지 않는다", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([rooms[1]]);
    const { onCommand } = renderCard(updateAction({}, { room_id: null, room_name: null }));
    await waitForRoom(card(), "회의실 1 (8인)");
    expect(within(card()).queryByText(/기존 —/)).toBeNull();
    fireEvent.change(within(card()).getByLabelText(/목적/), { target: { value: "범위 점검" } });
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2, draft: { purpose: "범위 점검" } }));
  });

  it("기존 방을 새 조건에 못 쓰면 [등록]이 막히고 이유가 선다 (H-2)", async () => {
    vi.mocked(api.readMeetingRooms).mockResolvedValue([{ ...rooms[0], available: false, unavailable_reason: "capacity" }, rooms[1]]);
    renderCard();
    await within(card()).findByText("새 인원보다 작은 방");
    await waitFor(() => expect(confirmButton().disabled).toBe(true));
    expect(within(card()).getByText(meetingScreen.roomPickAgain)).toBeTruthy();
  });

  it("확정이 409 ROOM_BOOKING_REFUSED 면 셀렉트가 가능한 방만으로 다시 서고 다시 고를 때까지 막힌다 (AC-08 · AC-21)", async () => {
    const onCommand = vi.fn(async () => {
      throw new api.ApiError(409, "Conflict", { code: "ROOM_BOOKING_REFUSED", message: "taken", available_rooms: [{ room_id: 5, name: "회의실 5 (8인)", capacity: 8 }] });
    });
    renderCard(updateAction(), onCommand);
    await pickRoom(card(), "회의실 1 (8인)");
    fireEvent.click(confirmButton());
    expect(await waitForRoom(card(), "회의실 5 (8인)")).toBeTruthy();
    expect(within(card()).getByText(meetingScreen.roomRejected.ROOM_BOOKING_REFUSED)).toBeTruthy();
    expect(confirmButton().disabled).toBe(true);
  });

  it("[거절] 이 선다", async () => {
    const { onCommand } = renderCard();
    fireEvent.click(within(card()).getByRole("button", { name: axDraftCard.reject }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("reject", undefined));
  });

  it("등록 뒤에는 한 줄과 [회의 열기]", () => {
    const { onOpenMeeting } = renderCard(updateAction({ state: "approved" }));
    fireEvent.click(screen.getByRole("button", { name: axDraftCard.openMeeting }));
    expect(onOpenMeeting).toHaveBeenCalledWith("m1");
  });
});

/*
 * WP3 수정 1 — 검수 F-1 · 계약 고정 §5. AX 가 방을 제안하면 편집 계약에 `proposed_room_id`(·`proposed_room_name`)가 실린다.
 * 카드는 제안 방을 미리 골라 두고 「AX 제안」 표지를 단다. 사람이 「기존 (변경 안 함)」 으로 되돌리면 `room: {keep: true}` 를 명시한다.
 * (예전에는 이 칸을 읽지 않아 「변경 안 함」 을 보고 누른 사람의 회의가 서버 쪽 제안 방으로 옮겨졌다)
 */
describe("AX 회의 수정 카드 — AX 가 제안한 방 (F-1 · 계약 고정 §5)", () => {
  /* 계약 고정 §6 — 「제안했는가」 는 `room_proposed: true` 가 말한다 */
  const proposing = (proposed: number | null, name: string | null = null) =>
    updateAction({}, { room_proposed: true, proposed_room_id: proposed, ...(name ? { proposed_room_name: name } : {}) });

  it("제안 방이 미리 골라져 있고 「AX 제안」 표지가 선다 — 그대로 [등록]하면 그 방이 `room` 으로 실린다", async () => {
    const { onCommand } = renderCard(proposing(1, "회의실 1 (8인)"));
    await waitFor(() => expect(roomValue(card())).toBe("회의실 1 (8인)"));
    expect(within(card()).getByText("AX 제안 · 회의실 1 (8인)")).toBeTruthy();
    // 「기존 (변경 안 함)」 은 골라져 있지 않다 — 화면이 보이는 것과 실리는 것이 같다
    expect(roomValue(card())).not.toBe(meetingScreen.roomKeep("회의실 3"));
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2, draft: { room: { room_id: 1 } } }));
  });

  it("사람이 「기존 (변경 안 함)」 으로 되돌리면 표지가 사라지고 `room: {keep: true}` 를 명시한다", async () => {
    const { onCommand } = renderCard(proposing(1, "회의실 1 (8인)"));
    await pickRoom(card(), meetingScreen.roomKeep("회의실 3"));
    expect(within(card()).queryByText(/AX 제안/)).toBeNull();
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2, draft: { room: { keep: true } } }));
  });

  it("「예약 없음」 제안(`proposed_room_id: null`)은 「회의실 예약 없음」 을 미리 고르고 `room: {room_id: null}` 을 싣는다", async () => {
    const { onCommand } = renderCard(proposing(null));
    await waitFor(() => expect(roomValue(card())).toBe(meetingScreen.noRoom));
    expect(within(card()).getByText("AX 제안 · 회의실 예약 없음")).toBeTruthy();
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2, draft: { room: { room_id: null } } }));
  });

  it("제안이 지금 방과 같으면 제안이 아니다 — 표지 없이 「기존」 그대로 · `room` 을 싣지 않는다", async () => {
    const { onCommand } = renderCard(proposing(3, "회의실 3"));
    await waitForRoom(card(), meetingScreen.roomKeep("회의실 3"));
    expect(within(card()).queryByText(/AX 제안/)).toBeNull();
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2 }));
  });

  it("제안 칸이 없는 계약은 지금처럼 — 「기존」 에서 열리고 그대로 두면 `room` 이 없다", async () => {
    const { onCommand } = renderCard();
    await waitForRoom(card(), meetingScreen.roomKeep("회의실 3"));
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2 }));
  });
});

/*
 * WP3 수정 2 — 재검수 F-r2-1 · 계약 고정 §6·§7. `proposed_room_id: null` 만으로는 「예약 없음 제안」 과 「제안 없음」 을 못 가른다 —
 * `room_proposed` 가 true 일 때만 제안이다. 그리고 `409 ROOM_RESERVATION_UNCONFIRMED` 는 코드별 문구.
 */
describe("AX 회의 수정 카드 — `room_proposed` · 확인 중 409 (F-r2-1 · W-r2-1)", () => {
  it("방 있는 회의 + 시간만 바꾸자는 제안(`room_proposed: false`, `proposed_room_id: null`) — 기존 방 그대로 · 표지 없음 · 그대로 등록해도 `room` 이 없다", async () => {
    const { onCommand } = renderCard(updateAction({}, { room_proposed: false, proposed_room_id: null, starts_at: "2026-10-08T07:00:00Z", ends_at: "2026-10-08T08:00:00Z" }));
    await waitFor(() => expect(roomValue(card())).toBe(meetingScreen.roomKeep("회의실 3")));
    expect(within(card()).queryByText(/AX 제안/)).toBeNull();
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2 }));
  });

  it("`room_proposed` 칸이 없으면 `proposed_room_id` 가 있어도 제안으로 보지 않는다", async () => {
    const { onCommand } = renderCard(updateAction({}, { proposed_room_id: 1 }));
    await waitFor(() => expect(roomValue(card())).toBe(meetingScreen.roomKeep("회의실 3")));
    expect(within(card()).queryByText(/AX 제안/)).toBeNull();
    fireEvent.click(confirmButton());
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2 }));
  });

  it("확정이 409 ROOM_RESERVATION_UNCONFIRMED 면 「회의실 예약을 아직 확인 중입니다 — 잠시 뒤 다시 시도해 주세요」(§7) · 셀렉트는 그대로", async () => {
    const onCommand = vi.fn(async () => {
      throw new api.ApiError(409, "Conflict", { code: "ROOM_RESERVATION_UNCONFIRMED", message: "reservation is being confirmed" });
    });
    renderCard(updateAction(), onCommand);
    await pickRoom(card(), "회의실 1 (8인)");
    fireEvent.click(confirmButton());
    expect(await within(card()).findByText(meetingScreen.saveErrors.ROOM_RESERVATION_UNCONFIRMED)).toBeTruthy();
    // 거절(가능한 방 목록)이 아니다 — 고른 방은 그대로 남고 다시 [등록]할 수 있다
    expect(roomValue(card())).toBe("회의실 1 (8인)");
    expect(confirmButton().disabled).toBe(false);
  });

  it("모르는 코드의 409 는 서버 `detail.message` 를 그대로 낸다", async () => {
    const onCommand = vi.fn(async () => {
      throw new api.ApiError(409, "Conflict", { code: "SOMETHING_NEW", message: "서버가 쓴 문장" });
    });
    renderCard(updateAction(), onCommand);
    await waitForRoom(card(), meetingScreen.roomKeep("회의실 3"));
    fireEvent.click(confirmButton());
    expect(await within(card()).findByText("서버가 쓴 문장")).toBeTruthy();
  });
});

/*
 * 2루프 E-4 — 좁은 AX 서랍에서 칸이 한 줄로 몰려 넘치던 것. `.scax-actioncard > div { display: flex }`(ax.css) 가 카드의 **직속** div 를
 * 가로 줄로 만든다 — 칸 묶음은 생성 카드처럼 `.action-task-content`(세로 격자) 안에 있어야 한다. 실제 폭 넘침은 jsdom 이 재지 못한다(앱 실물 확인).
 */
describe("AX 회의 수정 카드 — 세로 배치 (2루프 E-4)", () => {
  it("칸 묶음은 카드의 직속 div 가 아니라 `.action-task-content` 안이고 · 날짜·시작·종료만 한 줄 · 참석자/외부 참석자/회의실은 그 아래로 쌓인다", async () => {
    renderCard();
    await waitForRoom(card(), meetingScreen.roomKeep("회의실 3"));
    const section = card().closest(".scax-actioncard") ?? card();
    const fields = section.querySelector(".action-meeting-fields") as HTMLElement;
    // 직속 div 가 아니다 — 직속이면 ax.css 의 flex 줄이 걸린다
    expect(fields.parentElement?.classList.contains("action-task-content")).toBe(true);
    expect(fields.parentElement?.parentElement).toBe(section);
    expect([...section.children].some((child) => child.tagName === "DIV" && child.classList.contains("action-task-fields"))).toBe(false);
    // 한 줄은 일정 하나 — 날짜 + 시각 범위
    const schedule = fields.querySelector(".action-meeting-schedule") as HTMLElement;
    expect(schedule.parentElement).toBe(fields);
    expect(schedule.querySelector(".time-range")).toBeTruthy();
    expect(schedule.querySelector(".action-meeting-people")).toBeNull();
    // 참석자 묶음(참석자 · 외부 참석자)과 회의실은 일정 줄 밖, 같은 세로 격자의 다음 칸들이다
    const people = fields.querySelector(".action-meeting-people") as HTMLElement;
    expect(people.parentElement).toBe(fields);
    expect(people.compareDocumentPosition(schedule) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(roomTrigger(card()).closest(".action-meeting-schedule, .action-meeting-people")).toBeNull();
  });

  it("CSS — `.action-task-card > .action-task-content` 는 세로 격자다(카드 직속 div 의 flex 줄을 덮는다)", async () => {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
    const { readFileSync } = await import("node:fs");
    const css = (readFileSync("src/styles/ax.css", "utf8") as string).replace(/\s+/g, "");
    expect(css).toMatch(/\.action-task-card>\.action-task-content\{display:grid;/);
    expect(css).toContain(".action-task-card.action-task-fields{grid-template-columns:minmax(0,1fr);}");
  });
});
