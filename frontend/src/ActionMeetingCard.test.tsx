import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionMeetingCard } from "./ActionMeetingCard";
import type { ActionItem } from "./viewModels";


/**
 * 날짜 칸은 달력을 여는 트리거 하나다 (DS-17) — 값은 단추 글자로 서고, 고치는 길은 달력에서 날을 누르는 것이다.
 * 목록(Select·TimeField·달력)은 포털로 `document.body` 에 서므로(DS-18) 카드 안이 아니라 화면에서 찾는다.
 */
const dateTrigger = (scope: HTMLElement, label: string) => within(scope).getByRole("button", { name: `${label} 달력 열기` });

function pickDate(scope: HTMLElement, label: string, isoDate: string) {
  fireEvent.click(dateTrigger(scope, label));
  const panel = screen.getByRole("group", { name: label });
  fireEvent.click(panel.querySelector(`[data-date="${isoDate}"]`) as HTMLElement);
}

/**
 * The contract below is what `ActionPresenter._meeting_edit_contract` actually emits for
 * `meeting.reservation.create` — the only public meeting creation contract. A fixture that drifts
 * from it would let the card ship fields the server rejects, so keep the two in step.
 */
const proposal: ActionItem = {
  action_id: "meeting-action-1",
  conversation_id: "conversation-1",
  turn_id: "turn-1",
  action_type: "meeting.reservation.create",
  title: "회의 생성 확인",
  subject: "출시 점검 회의",
  operation_label: "회의 생성",
  state: "pending",
  version: 1,
  payload_summary: "회의 생성: 출시 점검 회의",
  result: null,
  audit_ref: null,
  preview: [
    { id: "purpose", label: "목적", value: "출시 준비 상황을 확인한다.", kind: "text" },
    { id: "starts_at", label: "시작", value: "2026-09-10T01:00:00+00:00", kind: "datetime" },
    { id: "ends_at", label: "종료", value: "2026-09-10T02:00:00+00:00", kind: "datetime" },
    { id: "host", label: "주최자", value: "민아 (구성원)", kind: "person" },
  ],
  commands: [
    { id: "confirm", label: "이 내용으로 회의 생성", tone: "primary" },
    { id: "reject", label: "거절", tone: "neutral" },
  ],
  edit_contract: {
    editor: "meeting",
    base_submission_version: 1,
    values: {
      title: "출시 점검 회의",
      purpose: "출시 준비 상황을 확인한다.",
      starts_at: "2026-09-10T01:00:00Z",
      ends_at: "2026-09-10T02:00:00Z",
      location: null,
      attendee_ids: ["jiho"],
      external_attendees: ["파트너 김"],
      agendas: [{ title: "일정 확인" }],
      carried_from_meeting_id: null,
      room_id: 3,
    },
    fields: [
      { id: "title", label: "회의 명", type: "text", required: false, editable: true },
      { id: "purpose", label: "목적", type: "textarea", required: false, editable: true },
      { id: "starts_at", label: "시작", type: "datetime", required: true, editable: true },
      { id: "ends_at", label: "종료", type: "datetime", required: true, editable: true },
      { id: "location", label: "장소", type: "text", required: false, editable: true },
      { id: "attendee_ids", label: "참석자", type: "multi_select", required: false, editable: true, options: [{ value: "jiho", label: "지호 (팀장)" }, { value: "hyeon", label: "현우 (인사)" }] },
      { id: "external_attendees", label: "외부 참석자", type: "string_list", required: false, editable: true },
      { id: "agendas", label: "안건", type: "object_list", required: false, editable: true },
      { id: "carried_from_meeting_id", label: "이어온 회의", type: "text", required: false, editable: true },
      { id: "room_id", label: "회의실 번호", type: "number", required: false, editable: true },
    ],
    warnings: ["같은 조직에 시간이 겹치는 일정이 1건 있습니다."],
  },
};

/** Everything the contract carried but the card does not render must survive a confirm untouched. */
const carried = {
  external_attendees: ["파트너 김"],
  agendas: [{ title: "일정 확인" }],
  carried_from_meeting_id: null,
  room_id: 3,
};


describe("AX Meeting proposal card", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("renders the reservation proposal with no surface left from the retired creation contract", () => {
    const { container } = render(<ActionMeetingCard action={proposal} onCommand={vi.fn()} principalId="mina" />);
    const card = within(container);

    expect(card.getByText("회의")).toBeTruthy();
    expect(card.getByText("출시 점검 회의")).toBeTruthy();
    expect(card.getByText("출시 준비 상황을 확인한다.")).toBeTruthy();
    expect(card.getByText("2026.09.10")).toBeTruthy();
    expect(card.getByText("10:00 - 11:00")).toBeTruthy();
    expect(card.getByRole("button", { name: "업무나 자료 첨부" })).toBeTruthy();
    expect(card.getByRole("button", { name: "수정" })).toBeTruthy();
    expect(card.getByRole("button", { name: "등록" })).toBeTruthy();
    // `meeting.create` 의 초기 회의록·공개 범위·조직은 계약과 함께 사라졌다.
    expect(card.queryByRole("button", { name: /회의록/ })).toBeNull();
    expect(card.queryByText("회의록", { exact: true })).toBeNull();

    fireEvent.click(card.getByRole("button", { name: "수정" }));
    expect(card.queryByLabelText("조직")).toBeNull();
    expect(card.queryByLabelText("공개 범위")).toBeNull();
    expect(card.queryByLabelText("내용")).toBeNull();
    expect(card.getByLabelText("목적")).toBeTruthy();
    expect(card.getByLabelText("장소")).toBeTruthy();
  });

  it("edits the reservation in place and returns every contract value it does not render", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <ActionMeetingCard action={proposal} onCommand={onCommand} principalId="mina" />,
    );
    const card = within(container);
    expect(card.getByText("출시 점검 회의")).toBeTruthy();
    expect(card.getByText("2026.09.10")).toBeTruthy();
    expect(card.queryByText("2026-09-10T01:00:00+00:00")).toBeNull();
    expect(card.getByText("같은 조직에 시간이 겹치는 일정이 1건 있습니다.")).toBeTruthy();

    fireEvent.click(card.getByRole("button", { name: "수정" }));
    expect(dateTrigger(container, "날짜").textContent).toBe("2026.09.10");
    expect(card.getByRole("button", { name: "시간 시작 시각" }).textContent).toContain("10:00");
    expect(card.getByRole("button", { name: "시간 종료 시각" }).textContent).toContain("11:00");
    expect(card.getByRole("button", { name: "참석자" }).textContent).toContain("지호 (팀장)");
    fireEvent.change(card.getByLabelText("회의 명"), { target: { value: "사람이 다듬은 출시 회의" } });
    fireEvent.change(card.getByLabelText("장소"), { target: { value: "8층 회의실" } });
    pickDate(container, "날짜", "2026-09-11");
    fireEvent.click(card.getByRole("button", { name: "시간 시작 시각" }));
    fireEvent.click(screen.getByRole("option", { name: "12:00" }));
    fireEvent.click(card.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      draft: {
        title: "사람이 다듬은 출시 회의",
        purpose: "출시 준비 상황을 확인한다.",
        starts_at: "2026-09-11T03:00:00.000Z",
        ends_at: "2026-09-11T04:00:00.000Z",
        location: "8층 회의실",
        attendee_ids: ["jiho"],
        ...carried,
      },
    }));
  });

  it("resets edited Meeting fields to the server proposal without leaving the editor", () => {
    const { container } = render(<ActionMeetingCard action={proposal} onCommand={vi.fn()} principalId="mina" />);
    const card = within(container);
    fireEvent.click(card.getByRole("button", { name: "수정" }));
    fireEvent.change(card.getByLabelText("회의 명"), { target: { value: "임시 회의명" } });
    pickDate(container, "날짜", "2026-09-12");

    fireEvent.click(card.getByRole("button", { name: "초기화" }));

    expect((card.getByLabelText("회의 명") as HTMLInputElement).value).toBe("출시 점검 회의");
    expect(dateTrigger(container, "날짜").textContent).toBe("2026.09.10");
    expect(card.getByRole("button", { name: "저장" })).toBeTruthy();
  });

  it("blocks an invalid time range before sending a confirmation", () => {
    const onCommand = vi.fn();
    const { container } = render(<ActionMeetingCard action={proposal} onCommand={onCommand} principalId="mina" />);
    const card = within(container);
    fireEvent.click(card.getByRole("button", { name: "수정" }));
    fireEvent.click(card.getByRole("button", { name: "시간 종료 시각" }));
    fireEvent.click(screen.getByRole("option", { name: "09:30" }));
    fireEvent.click(card.getByRole("button", { name: "저장" }));
    expect(card.getByText("종료 시각은 시작 시각보다 늦어야 합니다.")).toBeTruthy();
    expect(document.activeElement).toBe(card.getByRole("button", { name: "시간 시작 시각" }));
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("uses a chip selector for attendees while carrying the unrendered reservation values", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const withOtherAttendee: ActionItem = {
      ...proposal,
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, attendee_ids: ["hyeon"] },
      },
    };
    const { container } = render(
      <ActionMeetingCard action={withOtherAttendee} onCommand={onCommand} principalId="mina" />,
    );
    const card = within(container);
    fireEvent.click(card.getByRole("button", { name: "수정" }));
    expect(card.getByRole("button", { name: "참석자" }).textContent).toContain("현우 (인사)");
    fireEvent.click(card.getByRole("button", { name: "참석자" }));
    fireEvent.click(screen.getByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(card.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", expect.objectContaining({
      draft: expect.objectContaining({ attendee_ids: ["hyeon", "jiho"], ...carried }),
    })));
  });

  it("opens the persisted Meeting receipt without sending confirmation again", () => {
    const onCommand = vi.fn();
    const onOpenMeeting = vi.fn();
    const resolved: ActionItem = {
      ...proposal,
      state: "approved",
      version: 2,
      commands: [],
      result: { meeting_id: "meeting-1" },
      material_drafts: [{
        material_draft_id: "material-1",
        action_item_id: "meeting-action-1",
        source_kind: "external_link",
        name: "회의 안건",
        content_type: "text/uri-list",
        size_bytes: 0,
        url: "https://example.com/agenda",
        integrity_ref: "url:https://example.com/agenda",
        state: "claimed",
        expires_at: "2026-09-10T00:00:00Z",
        claimed_task_id: null,
        claimed_meeting_id: "meeting-1",
      }],
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, reference_task_ids: ["task-1"] },
        fields: [
          ...proposal.edit_contract!.fields,
          { id: "reference_task_ids", label: "참고 업무", type: "multi_select", required: false, editable: true, options: [{ value: "task-1", label: "출시 준비 업무" }] },
        ],
      },
    };
    const { container } = render(
      <ActionMeetingCard action={resolved} onCommand={onCommand} onOpenMeeting={onOpenMeeting} />,
    );
    const card = within(container);
    expect(card.getByText("회의")).toBeTruthy();
    expect(card.getByText("출시 준비 업무")).toBeTruthy();
    expect(card.getByText("회의 안건")).toBeTruthy();
    expect(card.queryByRole("list", { name: "일정 경고" })).toBeNull();
    expect(card.queryByRole("button", { name: "업무나 자료 첨부" })).toBeNull();
    expect(card.queryByRole("button", { name: /첨부 제외/ })).toBeNull();
    expect(card.queryByRole("button", { name: "수정" })).toBeNull();
    expect(card.queryByRole("button", { name: "등록" })).toBeNull();
    fireEvent.click(card.getByRole("button", { name: "회의 상세 보기" }));
    expect(onOpenMeeting).toHaveBeenCalledWith("meeting-1");
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("shows attached work as a removable draft row without opening the full editor", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const withAttachment: ActionItem = {
      ...proposal,
      edit_contract: {
        ...proposal.edit_contract!,
        values: {
          ...proposal.edit_contract!.values,
          reference_task_ids: ["task-1"],
        },
        fields: [
          ...proposal.edit_contract!.fields,
          {
            id: "reference_task_ids",
            label: "참고 업무",
            type: "multi_select",
            required: false,
            editable: true,
            options: [{ value: "task-1", label: "SC AX 클라이언트 미팅 공유" }],
          },
        ],
      },
    };
    const { container } = render(
      <ActionMeetingCard action={withAttachment} onCommand={onCommand} principalId="mina" />,
    );
    const card = within(container);

    expect(card.getByText("SC AX 클라이언트 미팅 공유")).toBeTruthy();
    expect(card.queryByLabelText("회의 명")).toBeNull();

    fireEvent.click(card.getByRole("button", { name: "SC AX 클라이언트 미팅 공유 첨부 제외" }));
    expect(card.queryByText("SC AX 클라이언트 미팅 공유")).toBeNull();
    expect(card.queryByLabelText("회의 명")).toBeNull();

    fireEvent.click(card.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      draft: expect.objectContaining({ reference_task_ids: [], ...carried }),
    }));
  });
});
