import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionMeetingCard } from "./ActionMeetingCard";
import type { ActionItem } from "./viewModels";


const proposal: ActionItem = {
  action_id: "meeting-action-1",
  conversation_id: "conversation-1",
  turn_id: "turn-1",
  action_type: "meeting.create",
  title: "회의 생성 확인",
  subject: "출시 점검 회의",
  operation_label: "회의 생성",
  state: "pending",
  version: 1,
  payload_summary: "회의 생성: 출시 점검 회의",
  result: null,
  audit_ref: null,
  preview: [
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
      organization_id: "scax",
      title: "출시 점검 회의",
      starts_at: "2026-09-10T01:00:00+00:00",
      ends_at: "2026-09-10T02:00:00+00:00",
      visibility: "private",
      attendee_ids: ["jiho"],
      include_initial_note: true,
      initial_note_body: "출시 준비 상황을 확인한다.",
      initial_note_source_status: "not_found",
      initial_note_source_evidence: [{
        source_type: "conversation_turn",
        source_id: "turn-1",
        label: "현재 대화",
        excerpt: "저번 출시 논의로 회의를 잡아줘",
        locator: { conversation_id: "conversation-1", turn_id: "turn-1" },
      }],
    },
    fields: [
      { id: "organization_id", label: "조직", type: "select", required: true, editable: true, options: [{ value: "scax", label: "SCAX" }, { value: "product", label: "제품팀" }] },
      { id: "title", label: "회의 명", type: "text", required: true, editable: true },
      { id: "starts_at", label: "시작", type: "datetime", required: true, editable: true },
      { id: "ends_at", label: "종료", type: "datetime", required: true, editable: true },
      { id: "visibility", label: "공개 범위", type: "select", required: true, editable: true, options: [{ value: "private", label: "비공개" }, { value: "public", label: "공개" }] },
      { id: "host_id", label: "주최자", type: "person", required: true, editable: false, value: "mina", label_value: "민아 (구성원)" },
      { id: "attendee_ids", label: "참석자", type: "multi_select", required: false, editable: true, options: [{ value: "jiho", label: "지호 (팀장)", organization_ids: ["scax", "product"] }, { value: "hyeon", label: "현우 (인사)", organization_ids: ["scax"] }] },
      { id: "include_initial_note", label: "회의록 초안도 만들기", type: "boolean", required: false, editable: true },
      { id: "initial_note_body", label: "회의록 초안", type: "textarea", required: false, editable: true },
    ],
    warnings: ["같은 조직에 시간이 겹치는 일정이 1건 있습니다."],
  },
};


describe("AX Meeting proposal card", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("matches the attachment-free Meeting draft card and keeps both add affordances visible", () => {
    const withoutNote: ActionItem = {
      ...proposal,
      preview: [
        { id: "description", label: "내용", value: "한 주간 진행한 내용을 서로 공유합니다.", kind: "text" },
        { id: "host", label: "주최자", value: "민아", kind: "person" },
        { id: "attendees", label: "참석자", value: "지호, 현우", kind: "people" },
        { id: "starts_at", label: "시작", value: "2026-09-10T01:00:00+00:00", kind: "datetime" },
        { id: "ends_at", label: "종료", value: "2026-09-10T02:00:00+00:00", kind: "datetime" },
      ],
      edit_contract: {
        ...proposal.edit_contract!,
        values: {
          ...proposal.edit_contract!.values,
          description: "한 주간 진행한 내용을 서로 공유합니다.",
          include_initial_note: false,
          initial_note_body: null,
        },
        fields: [
          ...proposal.edit_contract!.fields.slice(0, 2),
          { id: "description", label: "내용", type: "textarea", required: false, editable: true },
          ...proposal.edit_contract!.fields.slice(2),
        ],
      },
    };
    const { container } = render(<ActionMeetingCard action={withoutNote} onCommand={vi.fn()} principalId="mina" />);
    const card = within(container);

    expect(card.getByText("회의")).toBeTruthy();
    expect(card.getByText("출시 점검 회의")).toBeTruthy();
    expect(card.getByText("한 주간 진행한 내용을 서로 공유합니다.")).toBeTruthy();
    expect(card.getByText("2026.09.10")).toBeTruthy();
    expect(card.getByText("10:00 - 11:00")).toBeTruthy();
    expect(card.getByRole("button", { name: "업무나 자료 첨부" })).toBeTruthy();
    expect(card.getByRole("button", { name: /회의록 추가/ })).toBeTruthy();
    expect(card.getByRole("button", { name: "수정" })).toBeTruthy();
    expect(card.getByRole("button", { name: "등록" })).toBeTruthy();
    expect(card.queryByText("아직 첨부가 없습니다.")).toBeNull();

    fireEvent.click(card.getByRole("button", { name: "업무나 자료 첨부" }));
    const picker = within(document.body).getByRole("dialog", { name: "업무나 자료 첨부" });
    expect(within(picker).getByRole("tab", { name: "검색" })).toBeTruthy();
    expect(within(picker).getByRole("tab", { name: "링크 추가" })).toBeTruthy();
    fireEvent.click(within(picker).getByRole("button", { name: "첨부 선택 닫기" }));
    fireEvent.click(card.getByRole("button", { name: "회의록 추가" }));
    expect(card.getByRole("button", { name: "새 회의록 내용 수정" })).toBeTruthy();
    expect(card.getByLabelText("회의록 초안")).toBeTruthy();
  });

  it("edits the typed Meeting draft in place and keeps frozen source provenance outside the command", async () => {
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
    expect(card.queryByLabelText("조직")).toBeNull();
    expect(card.queryByLabelText("공개 범위")).toBeNull();
    expect(card.getByLabelText("주최자").textContent).toContain("민아 (구성원)");
    expect((card.getByLabelText("날짜") as HTMLInputElement).value).toBe("2026.09.10");
    expect(card.getByRole("button", { name: "시간 시작 시각" }).textContent).toContain("10:00");
    expect(card.getByRole("button", { name: "시간 종료 시각" }).textContent).toContain("11:00");
    expect(card.getByRole("button", { name: "참석자" }).textContent).toContain("지호 (팀장)");
    fireEvent.change(card.getByLabelText("회의 명"), { target: { value: "사람이 다듬은 출시 회의" } });
    fireEvent.change(card.getByLabelText("날짜"), { target: { value: "2026.09.11" } });
    fireEvent.click(card.getByRole("button", { name: "시간 시작 시각" }));
    fireEvent.click(card.getByRole("option", { name: "12:00" }));
    fireEvent.click(card.getByRole("button", { name: "새 회의록 내용 수정" }));
    expect(card.getByText(/지난 논의 기록을 찾지 못해 현재 대화만 사용/)).toBeTruthy();
    fireEvent.change(card.getByLabelText("회의록 초안"), { target: { value: "담당자별 출시 준비를 확인한다." } });
    fireEvent.click(card.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      draft: {
        organization_id: "scax",
        title: "사람이 다듬은 출시 회의",
        description: null,
        starts_at: "2026-09-11T03:00:00.000Z",
        ends_at: "2026-09-11T04:00:00.000Z",
        visibility: "private",
        attendee_ids: ["jiho"],
        reference_task_ids: [],
        include_initial_note: true,
        initial_note_body: "담당자별 출시 준비를 확인한다.",
      },
    }));
  });

  it("resets edited Meeting fields to the server proposal without leaving the editor", () => {
    const { container } = render(<ActionMeetingCard action={proposal} onCommand={vi.fn()} principalId="mina" />);
    const card = within(container);
    fireEvent.click(card.getByRole("button", { name: "수정" }));
    fireEvent.change(card.getByLabelText("회의 명"), { target: { value: "임시 회의명" } });
    fireEvent.change(card.getByLabelText("날짜"), { target: { value: "2026.09.12" } });

    fireEvent.click(card.getByRole("button", { name: "초기화" }));

    expect((card.getByLabelText("회의 명") as HTMLInputElement).value).toBe("출시 점검 회의");
    expect((card.getByLabelText("날짜") as HTMLInputElement).value).toBe("2026.09.10");
    expect(card.getByRole("button", { name: "저장" })).toBeTruthy();
  });

  it("can explicitly exclude the precomputed note and blocks an invalid time range", () => {
    const onCommand = vi.fn();
    const { container } = render(<ActionMeetingCard action={proposal} onCommand={onCommand} principalId="mina" />);
    const card = within(container);
    fireEvent.click(card.getByRole("button", { name: "수정" }));
    fireEvent.click(card.getByRole("button", { name: "새 회의록 제외" }));
    expect(card.queryByLabelText("회의록 초안")).toBeNull();
    expect(card.queryByRole("region", { name: "회의록 초안 근거" })).toBeNull();
    fireEvent.click(card.getByRole("button", { name: "시간 종료 시각" }));
    fireEvent.click(card.getByRole("option", { name: "09:30" }));
    fireEvent.click(card.getByRole("button", { name: "저장" }));
    expect(card.getByText("종료 시각은 시작 시각보다 늦어야 합니다.")).toBeTruthy();
    expect(document.activeElement).toBe(card.getByRole("button", { name: "시간 시작 시각" }));
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("uses a chip selector for eligible attendees while preserving hidden organization and visibility", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const withCrossOrganizationAttendee: ActionItem = {
      ...proposal,
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, attendee_ids: ["hyeon"] },
      },
    };
    const { container } = render(
      <ActionMeetingCard action={withCrossOrganizationAttendee} onCommand={onCommand} principalId="mina" />,
    );
    const card = within(container);
    fireEvent.click(card.getByRole("button", { name: "수정" }));
    expect(card.getByRole("button", { name: "참석자" }).textContent).toContain("현우 (인사)");
    fireEvent.click(card.getByRole("button", { name: "참석자" }));
    fireEvent.click(card.getByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(card.getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", expect.objectContaining({
      draft: expect.objectContaining({
        organization_id: "scax",
        visibility: "private",
        attendee_ids: ["hyeon", "jiho"],
      }),
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
    expect(card.getByText("새 회의록", { exact: true })).toBeTruthy();
    expect(card.queryByRole("list", { name: "일정 경고" })).toBeNull();
    expect(card.queryByRole("button", { name: "업무나 자료 첨부" })).toBeNull();
    expect(card.queryByRole("button", { name: /첨부 제외/ })).toBeNull();
    expect(card.queryByRole("button", { name: "새 회의록 제외" })).toBeNull();
    expect(card.queryByRole("button", { name: "수정" })).toBeNull();
    expect(card.queryByRole("button", { name: "등록" })).toBeNull();
    fireEvent.click(card.getByRole("button", { name: "회의 상세 보기" }));
    expect(onOpenMeeting).toHaveBeenCalledWith("meeting-1");
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("does not leave an empty note section on a completed note-free Meeting", () => {
    const withoutNote: ActionItem = {
      ...proposal,
      state: "approved",
      version: 2,
      commands: [],
      result: { meeting_id: "meeting-2" },
      edit_contract: {
        ...proposal.edit_contract!,
        values: {
          ...proposal.edit_contract!.values,
          include_initial_note: false,
          initial_note_body: null,
        },
      },
    };
    const { container } = render(<ActionMeetingCard action={withoutNote} onCommand={vi.fn()} />);
    const card = within(container);

    expect(card.queryByText("회의록", { exact: true })).toBeNull();
    expect(card.queryByRole("button", { name: "회의록 추가" })).toBeNull();
  });

  it("shows attached work and the new note as removable draft rows without opening the full editor", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const withAttachmentAndNote: ActionItem = {
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
      <ActionMeetingCard action={withAttachmentAndNote} onCommand={onCommand} principalId="mina" />,
    );
    const card = within(container);

    expect(card.getByText("SC AX 클라이언트 미팅 공유")).toBeTruthy();
    expect(card.getByText("새 회의록", { exact: true })).toBeTruthy();
    expect(card.queryByLabelText("회의 명")).toBeNull();

    fireEvent.click(card.getByRole("button", { name: "SC AX 클라이언트 미팅 공유 첨부 제외" }));
    fireEvent.click(card.getByRole("button", { name: "새 회의록 제외" }));
    expect(card.queryByText("SC AX 클라이언트 미팅 공유")).toBeNull();
    expect(card.queryByText("새 회의록", { exact: true })).toBeNull();
    expect(card.getByRole("button", { name: "회의록 추가" })).toBeTruthy();
    expect(card.queryByLabelText("회의 명")).toBeNull();

    fireEvent.click(card.getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      draft: expect.objectContaining({
        reference_task_ids: [],
        include_initial_note: false,
        initial_note_body: null,
      }),
    }));
  });
});
