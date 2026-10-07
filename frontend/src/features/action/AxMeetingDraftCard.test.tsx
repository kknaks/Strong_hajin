import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  ...((await actual()) as object),
  readMeetingRooms: vi.fn(),
  readMeeting: vi.fn(),
  bookMeeting: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
  stageActionMaterialFile: vi.fn(),
  stageActionMaterialLink: vi.fn(),
  discardActionMaterialDraft: vi.fn(),
}));

import * as api from "../../lib/api";
import { axDraftCard } from "../../lib/labels";
import type { ActionItem } from "../../lib/viewModels";
import { resetRoster } from "../meetings/roster";
import { AxDraftCard, axDraftFromAction, isAxDraftAction, isAxTaskDraftKind } from "./AxDraftCard";

/*
 * WORK-012 WP3-FE — AX 회의 생성 카드를 `AxDraftCard` 틀로(SPEC-010 §2.4 · §4.4 · OQ-901 · OQ-1001).
 * 흐름·디자인은 업무 생성 카드와 같고(쪽 나눔 · AX 배지 · 회차 · 거절 · 수정 → 초안 저장 · 등록 · 접힌 한 줄), 내용은 회의 고유 필드.
 */

const meetingAction = (over: Partial<ActionItem> = {}): ActionItem => ({
  action_id: "meeting-action-1",
  conversation_id: "conversation-1",
  turn_id: "turn-1",
  action_type: "meeting.reservation.create",
  title: "회의 생성 확인",
  subject: "주간 회의",
  operation_label: "회의 생성",
  state: "pending",
  version: 1,
  payload_summary: "회의 생성: 주간 회의",
  result: null,
  audit_ref: null,
  preview: [{ id: "host", label: "주최자", value: "민아", kind: "person" }],
  commands: [
    { id: "confirm", label: "이 내용으로 회의 생성", tone: "primary" },
    { id: "save_draft", label: "저장", tone: "neutral" },
    { id: "reject", label: "거절", tone: "neutral" },
  ],
  material_drafts: [],
  edit_contract: {
    editor: "meeting",
    base_submission_version: 1,
    save_command: "save_draft",
    values: {
      title: "주간 회의",
      purpose: "이번 주 진행 점검",
      starts_at: "2026-10-08T06:00:00Z",
      ends_at: "2026-10-08T07:00:00Z",
      attendee_ids: ["jiho"],
      external_attendees: ["파트너 김"],
      agendas: [
        { title: "지난주 미결", source: "carried" },
        { title: "새 안건", source: "manual" },
      ],
      carried_from_meeting_id: "past-1",
      room_id: 3,
    },
    fields: [
      { id: "title", label: "회의 명", type: "text", required: false, editable: true },
      { id: "attendee_ids", label: "참석자", type: "multi_select", required: false, editable: true, options: [{ value: "jiho", label: "지호" }] },
      { id: "room_id", label: "회의실 번호", type: "number", required: false, editable: true },
    ],
  },
  ...over,
});

const rooms = [
  { room_id: 3, name: "회의실 3 (6인)", capacity: 6, available: true, current: false, unavailable_reason: null },
  { room_id: 1, name: "회의실 1 (8인)", capacity: 8, available: true, current: false, unavailable_reason: null },
];

function renderCard(action = meetingAction(), onCommand = vi.fn(async (_command: string, _payload?: Record<string, unknown>) => undefined), onOpenMeeting = vi.fn()) {
  const source = axDraftFromAction(action);
  if (!source) throw new Error("회의 생성 Action 이 카드가 되지 않았다");
  render(<AxDraftCard onCommand={onCommand} onOpenMeeting={onOpenMeeting} source={source} />);
  return { onCommand, onOpenMeeting };
}

const card = () => screen.getByRole("region", { name: /AX 회의 생성/ });
const panel = () => within(card()).getByRole("tabpanel");
const goTo = (name: string) => fireEvent.click(within(card()).getByRole("tab", { name }));

beforeEach(() => {
  resetRoster();
  vi.mocked(api.readMeetingRooms).mockResolvedValue(rooms);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AX 회의 생성 카드 — 판별", () => {
  it("`meeting.reservation.create` + `meeting` 편집기는 쪽 나눔 카드가 된다 — 편집기가 다르면 아니다", () => {
    expect(isAxDraftAction(meetingAction())).toBe(true);
    expect(isAxDraftAction(meetingAction({ edit_contract: { ...meetingAction().edit_contract!, editor: "task" } }))).toBe(false);
    // 내 업무의 「AX 제안」 칩은 업무 초안만 — 회의 생성 초안은 그 자리에 서지 않는다
    expect(isAxTaskDraftKind("ax.meeting.reservation.create")).toBe(false);
    expect(isAxTaskDraftKind("ax.task.create_self")).toBe(true);
  });
});

describe("AX 회의 생성 카드 — 머리 · 쪽", () => {
  it("머리에 AX 배지 · 「초안 · 1회차」 · 「회의 생성」 · 제목 · 쪽 넷(기본 정보 · 참석자 · 회의실·안건 · 자료) — 업무 연결 쪽은 없다", () => {
    renderCard();
    const region = card();
    expect(within(region).getByText(axDraftCard.badge)).toBeTruthy();
    expect(within(region).getByText("초안 · 1회차")).toBeTruthy();
    expect(within(region).getByText("회의 생성")).toBeTruthy();
    expect(within(region).getAllByRole("tab").map((tab) => tab.getAttribute("aria-label"))).toEqual(["기본 정보", "참석자", "회의실·안건", "자료"]);
    expect(within(region).queryByRole("tab", { name: "업무 연결" })).toBeNull();
  });

  it("기본 정보 — 회의명 · 날짜 · 시간(KST) · 주최자 · 목적", () => {
    renderCard();
    const text = panel().textContent ?? "";
    expect(text).toContain("주간 회의");
    expect(text).toContain("15:00 – 16:00");
    expect(text).toContain("민아");
    expect(text).toContain("이번 주 진행 점검");
  });

  it("참석자 — 사내(선택지 이름) · 사외", () => {
    renderCard();
    goTo("참석자");
    const text = panel().textContent ?? "";
    expect(text).toContain("지호");
    expect(text).toContain("파트너 김");
  });

  it("회의실·안건 — 공용 셀렉트에 AX 가 제안한 방이 미리 골라져 있고, 안건은 넘어온 것을 표시한다", async () => {
    renderCard();
    goTo("회의실·안건");
    await waitFor(() => expect((within(panel()).getByLabelText("회의실 3 (6인)") as HTMLInputElement).checked).toBe(true));
    expect(api.readMeetingRooms).toHaveBeenCalledWith(expect.objectContaining({ people: 2, meeting_id: null }));
    const text = panel().textContent ?? "";
    expect(text).toContain("지난주 미결 · 지난 회의에서 넘어옴");
    expect(text).toContain("새 안건");
  });
});

describe("AX 회의 생성 카드 — 명령", () => {
  it("[등록] — 고친 것이 없으면 초안 없이 회차만 · 카드에서 회의실을 바꾸면 그 값을 초안으로 싣는다", async () => {
    const { onCommand } = renderCard();
    fireEvent.click(within(card()).getByRole("button", { name: axDraftCard.confirm }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 1 }));

    cleanup();
    const second = renderCard();
    goTo("회의실·안건");
    fireEvent.click(await within(panel()).findByLabelText("회의실 1 (8인)"));
    fireEvent.click(within(card()).getByRole("button", { name: axDraftCard.confirm }));
    await waitFor(() => expect(second.onCommand).toHaveBeenCalled());
    const payload = second.onCommand.mock.calls[0][1] as { draft: Record<string, unknown> };
    expect(payload.draft.room_id).toBe(1);
    expect(payload.draft.title).toBe("주간 회의");
  });

  it("[거절] 이 선다 — 예전 회의 카드는 숨겼다", async () => {
    const { onCommand } = renderCard();
    fireEvent.click(within(card()).getByRole("button", { name: axDraftCard.reject }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("reject", undefined));
  });

  it("[수정] = 생성 모달이 초안 값으로 열리고, 「저장」 은 회의를 만들지 않고 초안 저장(save_draft)이다", async () => {
    const { onCommand } = renderCard();
    fireEvent.click(within(card()).getByRole("button", { name: axDraftCard.edit }));
    const modal = await screen.findByRole("dialog", { name: "회의 예약" });
    expect((within(modal).getByPlaceholderText("회의명을 적으세요") as HTMLInputElement).value).toBe("주간 회의");
    expect(within(modal).getByText("지난주 미결")).toBeTruthy();
    expect(within(modal).getByText("파트너 김")).toBeTruthy();
    // 지난 회의 제안 카드는 서지 않는다
    expect(within(modal).queryByRole("button", { name: "불러오기" })).toBeNull();

    fireEvent.change(within(modal).getByPlaceholderText("회의명을 적으세요"), { target: { value: "주간 회의 (고침)" } });
    fireEvent.click(within(modal).getByRole("button", { name: axDraftCard.save }));
    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    const [command, payload] = onCommand.mock.calls[0] as [string, { base_submission_version: number; draft: Record<string, unknown> }];
    expect(command).toBe("save_draft");
    expect(payload.base_submission_version).toBe(1);
    expect(payload.draft.title).toBe("주간 회의 (고침)");
    expect(payload.draft.agendas).toEqual([
      { title: "지난주 미결", source: "carried" },
      { title: "새 안건", source: "manual" },
    ]);
    expect(payload.draft.carried_from_meeting_id).toBe("past-1");
    expect(api.bookMeeting).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "회의 예약" })).toBeNull());
  });

  it("등록 뒤에는 한 줄(제목 · 날짜 시간)과 [회의 열기] 로 접힌다", () => {
    const { onOpenMeeting } = renderCard(meetingAction({ state: "approved", result: { meeting_id: "m-new" } }));
    fireEvent.click(screen.getByRole("button", { name: axDraftCard.openMeeting }));
    expect(onOpenMeeting).toHaveBeenCalledWith("m-new");
    expect(document.querySelector('[data-state="approved"]')?.textContent).toContain("주간 회의");
  });
});
