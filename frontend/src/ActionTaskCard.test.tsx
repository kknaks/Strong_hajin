import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionTaskCard, TaskAttachmentGroup } from "./ActionTaskCard";
import { discardActionMaterialDraft, stageActionMaterialFile, stageActionMaterialLink } from "./api";
import { ACTION_DRAFT_STORAGE_KEY, actionDraftStorageId } from "./useActionDraft";
import type { ActionItem, ActionMaterialDraft } from "./viewModels";

vi.mock("./api", () => ({
  discardActionMaterialDraft: vi.fn(),
  stageActionMaterialFile: vi.fn(),
  stageActionMaterialLink: vi.fn(),
}));

/**
 * 날짜 칸은 입력칸이 아니라 «달력을 여는 트리거» 다 (DS-17) — 값은 그 단추의 글자로 서고,
 * 고치는 길은 달력을 열어 날을 누르는 것이다. 카드가 준 구분자(`.`)가 그 글자에 그대로 실린다.
 */
const dateTrigger = (scope: HTMLElement, label: string) => within(scope).getByRole("button", { name: `${label} 달력 열기` });

function pickDate(scope: HTMLElement, label: string, isoDate: string) {
  fireEvent.click(dateTrigger(scope, label));
  const panel = screen.getByRole("group", { name: label });
  fireEvent.click(panel.querySelector(`[data-date="${isoDate}"]`) as HTMLElement);
}

const proposal: ActionItem = {
  action_id: "action-1",
  conversation_id: "conversation-1",
  turn_id: "turn-1",
  action_type: "task.create_self",
  title: "업무 생성 확인",
  subject: "AX 원안",
  operation_label: "업무 생성",
  state: "pending",
  version: 1,
  payload_summary: "업무 생성 확인",
  result: null,
  audit_ref: null,
  preview: [
    { id: "description", label: "설명", value: "처음 설명", kind: "text" },
    { id: "assignee", label: "담당", value: "지호 (팀장)", kind: "person" },
    { id: "due_date", label: "기한", value: "2026-09-20", kind: "date" },
  ],
  commands: [
    { id: "confirm", label: "이 내용으로 업무 생성", tone: "primary" },
    { id: "reject", label: "거절", tone: "neutral" },
  ],
  edit_contract: {
    editor: "task",
    base_submission_version: 1,
    values: {
      title: "AX 원안",
      description: "처음 설명",
      start_date: null,
      due_date: "2026-09-20",
      checklist: ["준비"],
      reference_task_ids: [],
      parent_task_id: null,
      project_id: null,
    },
    fields: [
      { id: "title", label: "업무 명", type: "text", required: true, editable: true },
      { id: "description", label: "내용", type: "textarea", required: false, editable: true },
      { id: "assignee_id", label: "담당자", type: "person", required: true, editable: false, value: "jiho", label_value: "지호 (팀장)" },
      { id: "start_date", label: "시작일", type: "date", required: false, editable: true },
      { id: "due_date", label: "기한", type: "date", required: true, editable: true },
      { id: "project_id", label: "프로젝트", type: "select", required: false, editable: true, options: [] },
      { id: "checklist", label: "체크리스트", type: "string_list", required: false, editable: true },
      { id: "reference_task_ids", label: "참고 업무", type: "multi_select", required: false, editable: true, options: [] },
    ],
  },
};

function materialDraft(
  id: string,
  sourceKind: "external_link" | "file",
  name: string,
): ActionMaterialDraft {
  return {
    material_draft_id: id,
    action_item_id: "action-1",
    source_kind: sourceKind,
    name,
    content_type: sourceKind === "file" ? "text/plain" : "text/uri-list",
    size_bytes: sourceKind === "file" ? 12 : 0,
    url: sourceKind === "external_link" ? "https://example.com/brief" : null,
    integrity_ref: sourceKind === "file" ? "sha256:test" : "observed:test",
    state: "staged",
    expires_at: "2026-09-09T00:00:00+00:00",
    claimed_task_id: null,
  };
}

function storedDraft(
  draft: Record<string, unknown>,
  baseSubmissionVersion = 1,
  principalId = "jiho",
  actionId = "action-1",
) {
  const id = actionDraftStorageId(principalId, actionId, baseSubmissionVersion);
  window.localStorage.setItem(ACTION_DRAFT_STORAGE_KEY, JSON.stringify({
    [id]: {
      principal_id: principalId,
      action_item_id: actionId,
      base_submission_version: baseSubmissionVersion,
      draft,
    },
  }));
}

describe("AX Task proposal card", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("renders task, link, and file as one attachment group and confirms their separate canonical identities", async () => {
    const withAttachments: ActionItem = {
      ...proposal,
      material_drafts: [materialDraft("link-1", "external_link", "기획 링크"), materialDraft("file-1", "file", "검토안.txt")],
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, reference_task_ids: ["task-ref"] },
        fields: proposal.edit_contract!.fields.map((field) => field.id === "reference_task_ids"
          ? { ...field, options: [{ value: "task-ref", label: "선행 업무" }] }
          : field),
      },
    };
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ActionTaskCard action={withAttachments} onCommand={onCommand} principalId="jiho" />);
    const card = container.querySelector(".action-task-card") as HTMLElement;
    expect(within(card).getByText("SC AX")).toBeTruthy();
    expect(within(card).getByText("초안")).toBeTruthy();
    expect(within(card).getByText("담당자")).toBeTruthy();
    expect(within(card).queryByText("담당")).toBeNull();
    expect(card.querySelector(".action-task-summary")).toBeTruthy();
    expect(within(card).getByText("2026.09.20")).toBeTruthy();
    expect(card.querySelector("details")).toBeNull();
    const group = within(container).getByRole("region", { name: "첨부" });
    expect(within(group).getByText("선행 업무")).toBeTruthy();
    expect(within(group).getByText("기획 링크")).toBeTruthy();
    expect(within(group).getByText("검토안.txt")).toBeTruthy();
    expect(group.querySelectorAll("li")).toHaveLength(3);

    fireEvent.click(within(container).getByRole("button", { name: "등록" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      attachment_draft_ids: ["link-1", "file-1"],
    }));
  });

  it("prioritizes the due date in the proposal summary and presents people by name only", () => {
    const dueFirst: ActionItem = {
      ...proposal,
      preview: [
        { id: "description", label: "설명", value: "데모에서 업무가 안전하게 이어지는 경험을 검증한다.", kind: "text" },
        { id: "assignee", label: "담당자", value: "유나 (대표)", kind: "person" },
        { id: "start_date", label: "시작일", value: "2026-09-10", kind: "date" },
        { id: "due_date", label: "기한", value: "2026-10-10", kind: "date" },
        { id: "project", label: "프로젝트", value: "SC AX", kind: "text" },
        { id: "checklist", label: "체크리스트", value: "2단계 · 자료 정리 → 리허설", kind: "text" },
        { id: "references", label: "참고 업무", value: "SC AX 클라이언트 미팅 공유", kind: "text" },
      ],
      edit_contract: {
        ...proposal.edit_contract!,
        values: {
          ...proposal.edit_contract!.values,
          start_date: "2026-09-10",
          due_date: "2026-10-10",
          checklist: ["자료 정리", "리허설"],
        },
        fields: proposal.edit_contract!.fields.map((field) => field.id === "assignee_id"
          ? { ...field, label_value: "유나 (대표)" }
          : field),
      },
    };

    const { container } = render(<ActionTaskCard action={dueFirst} onCommand={vi.fn()} principalId="yuna" />);
    const card = container.querySelector(".action-task-card") as HTMLElement;
    const summary = within(card).getByText("AX 원안").closest(".action-task-summary") as HTMLElement;

    expect(within(summary).getByText("데모에서 업무가 안전하게 이어지는 경험을 검증한다.")).toBeTruthy();
    expect(within(summary).getByText("담당자")).toBeTruthy();
    expect(within(summary).getByText("유나")).toBeTruthy();
    expect(within(summary).getByText("기한")).toBeTruthy();
    expect(within(summary).getByText("2026.10.10")).toBeTruthy();
    expect(within(summary).queryByText("유나 (대표)")).toBeNull();
    expect(within(summary).queryByText("시작일")).toBeNull();
    expect(within(summary).queryByText("2026.09.10")).toBeNull();
    expect(within(summary).queryByText("프로젝트")).toBeNull();
    expect(within(summary).getByText("체크리스트")).toBeTruthy();
    const checklist = within(summary).getByRole("list", { name: "체크리스트 항목" });
    expect(within(checklist).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["자료 정리", "리허설"]);
    expect(within(summary).queryByText(/→/)).toBeNull();
    expect(within(summary).queryByText("참고 업무")).toBeNull();

    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    expect(dateTrigger(card, "시작일").textContent).toBe("2026.09.10");
    expect(within(card).getByText("유나")).toBeTruthy();
    expect(within(card).queryByText("유나 (대표)")).toBeNull();
  });

  it("omits an empty checklist from the proposal summary", () => {
    const emptyChecklist: ActionItem = {
      ...proposal,
      preview: [
        ...(proposal.preview ?? []),
        { id: "checklist", label: "체크리스트", value: "   ", kind: "text" },
      ],
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, checklist: [] },
      },
    };

    const { container } = render(<ActionTaskCard action={emptyChecklist} onCommand={vi.fn()} principalId="jiho" />);
    const summary = container.querySelector(".action-task-summary") as HTMLElement;

    expect(within(summary).queryByText("체크리스트")).toBeNull();
  });

  it("reuses the task card shell for an editable work request without submitting task-only fields", async () => {
    const workRequest: ActionItem = {
      ...proposal,
      action_type: "work_request.create",
      subject: "이번 분기 보고",
      operation_label: "업무 요청",
      preview: [
        { id: "description", label: "설명", value: "지난 보고를 참고해 주세요", kind: "text" },
        { id: "requester", label: "요청자", value: "민아 (구성원)", kind: "person" },
        { id: "assignee", label: "요청 대상", value: "지호 (팀장)", kind: "person" },
        { id: "due_date", label: "기한", value: "2026-09-30", kind: "date" },
        { id: "references", label: "참고 업무", value: "지난 분기 보고", kind: "text" },
      ],
      commands: [
        { id: "confirm", label: "이 내용으로 업무 요청", tone: "primary" },
        { id: "reject", label: "거절", tone: "neutral" },
      ],
      edit_contract: {
        editor: "task",
        base_submission_version: 1,
        values: {
          title: "이번 분기 보고",
          description: "지난 보고를 참고해 주세요",
          assignee_id: "jiho",
          due_date: "2026-09-30",
          cc_member_ids: [],
          checklist: ["수치 검토"],
          reference_task_ids: ["task-ref"],
        },
        fields: [
          { id: "title", label: "업무 명", type: "text", required: true, editable: true },
          { id: "description", label: "내용", type: "textarea", required: false, editable: true },
          { id: "assignee_id", label: "요청 대상", type: "select", required: true, editable: true, options: [{ value: "jiho", label: "지호" }] },
          { id: "due_date", label: "기한", type: "date", required: false, editable: true },
          { id: "cc_member_ids", label: "참조자", type: "multi_select", required: false, editable: true, options: [] },
          { id: "checklist", label: "체크리스트", type: "string_list", required: false, editable: true },
          { id: "reference_task_ids", label: "참고 업무", type: "multi_select", required: false, editable: true, options: [{ value: "task-ref", label: "지난 분기 보고" }] },
        ],
      },
    };
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ActionTaskCard action={workRequest} onCommand={onCommand} principalId="mina" />);

    expect(within(container).getByText("요청 대상")).toBeTruthy();
    expect(within(container).getAllByText("지난 분기 보고")).toHaveLength(1);
    fireEvent.click(within(container).getByRole("button", { name: "수정" }));
    fireEvent.change(within(container).getByLabelText("내용"), { target: { value: "수치만 참고해 주세요" } });
    fireEvent.click(within(container).getByRole("button", { name: "지난 분기 보고 첨부 제외" }));
    fireEvent.click(within(container).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      draft: {
        title: "이번 분기 보고",
        description: "수치만 참고해 주세요",
        assignee_id: "jiho",
        due_date: "2026-09-30",
        cc_member_ids: [],
        checklist: ["수치 검토"],
        reference_task_ids: [],
      },
    }));
  });

  it("opens a search-first attachment picker from the full-width summary control", async () => {
    vi.mocked(stageActionMaterialLink).mockResolvedValue(materialDraft("link-2", "external_link", "새 링크"));
    vi.mocked(discardActionMaterialDraft).mockResolvedValue(undefined);
    const editable: ActionItem = {
      ...proposal,
      edit_contract: {
        ...proposal.edit_contract!,
        fields: proposal.edit_contract!.fields.map((field) => field.id === "reference_task_ids"
          ? { ...field, options: [{ value: "task-ref", label: "선행 업무" }] }
          : field),
      },
    };
    const { container } = render(<ActionTaskCard action={editable} onCommand={vi.fn()} principalId="jiho" />);
    const trigger = within(container).getByRole("button", { name: "업무나 자료 첨부" });
    expect(trigger.classList.contains("action-task-attachment-trigger")).toBe(true);
    expect(trigger.classList.contains("empty")).toBe(true);
    expect(within(container).queryByText("아직 첨부가 없습니다.")).toBeNull();
    fireEvent.click(trigger);
    expect(within(container).getByLabelText("업무 명")).toBeTruthy();
    const picker = screen.getByRole("dialog", { name: "업무나 자료 첨부" });
    expect(within(picker).getByRole("tab", { name: "검색" })).toBeTruthy();
    expect(within(picker).getByRole("tab", { name: "링크 추가" })).toBeTruthy();
    expect(within(picker).getByRole("tab", { name: "파일 추가" })).toBeTruthy();
    expect(within(picker).queryByRole("button", { name: "선행 업무" })).toBeNull();
    expect(within(picker).getByText("찾고 싶은 업무나 자료를 검색하세요")).toBeTruthy();

    fireEvent.change(within(picker).getByLabelText("업무나 자료 검색"), { target: { value: "선행" } });
    fireEvent.click(await within(picker).findByRole("button", { name: /선행 업무/ }));
    fireEvent.click(within(picker).getByRole("button", { name: "첨부" }));
    expect(within(container).getByText("선행 업무")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "업무나 자료 첨부" })).toBeNull();
    fireEvent.click(within(container).getByRole("button", { name: "업무나 자료 첨부" }));
    const linkPicker = screen.getByRole("dialog", { name: "업무나 자료 첨부" });
    fireEvent.click(within(linkPicker).getByRole("tab", { name: "링크 추가" }));
    fireEvent.change(within(linkPicker).getByLabelText("링크 주소"), { target: { value: "https://example.com/new" } });
    fireEvent.change(within(linkPicker).getByLabelText("링크 이름"), { target: { value: "새 링크" } });
    fireEvent.click(within(linkPicker).getByRole("button", { name: "링크 추가" }));
    await waitFor(() => expect(within(container).getByText("새 링크")).toBeTruthy());
    expect(stageActionMaterialLink).toHaveBeenCalledWith("action-1", { url: "https://example.com/new", label: "새 링크" });

    fireEvent.click(within(container).getByRole("button", { name: "새 링크 첨부 제외" }));
    await waitFor(() => expect(discardActionMaterialDraft).toHaveBeenCalledWith("action-1", "link-2"));
    expect(within(container).queryByText("새 링크")).toBeNull();
  });

  it("uses the required dotted due-date control and a DS reset icon", () => {
    const { container } = render(<ActionTaskCard action={proposal} onCommand={vi.fn()} principalId="jiho" />);
    fireEvent.click(within(container).getByRole("button", { name: "수정" }));
    const due = dateTrigger(container, "기한");
    // 브라우저 기본 달력 칸을 쓰지 않는다 — 우리 달력을 여는 단추 하나다 (DS-17)
    expect(due.tagName).toBe("BUTTON");
    expect(container.querySelector('input[type="date"]')).toBeNull();
    expect(due.textContent).toBe("2026.09.20");
    // 필수는 레이블의 * 로 선다 (required 프롭)
    expect(container.querySelector('label[for="action-task-due_date"]')?.textContent).toBe("기한 *");
    // pickerIcon="chevron-down" — 칸 안 오른쪽 아이콘
    expect(due.querySelector("svg")).toBeTruthy();
    expect(within(container).getByRole("button", { name: "초기화" }).querySelector("svg")).toBeTruthy();
  });

  it("opens the editor at a missing required due date instead of submitting an unusable draft", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const withoutDue: ActionItem = {
      ...proposal,
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, due_date: null },
      },
    };
    const { container } = render(<ActionTaskCard action={withoutDue} onCommand={onCommand} principalId="jiho" />);
    fireEvent.click(within(container).getByRole("button", { name: "등록" }));
    expect(await within(container).findByRole("button", { name: "기한 달력 열기" })).toBe(document.activeElement);
    expect(within(container).getByRole("alert").textContent).toContain("기한");
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("keeps a locally staged material when polling briefly returns an older Action projection", async () => {
    vi.mocked(stageActionMaterialLink).mockResolvedValue(materialDraft("link-staged", "external_link", "방금 추가한 링크"));
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { container, rerender } = render(<ActionTaskCard action={proposal} onCommand={onCommand} principalId="jiho" />);

    fireEvent.click(within(container).getByRole("button", { name: "수정" }));
    fireEvent.click(within(container).getByRole("button", { name: "업무나 자료 첨부" }));
    const picker = screen.getByRole("dialog", { name: "업무나 자료 첨부" });
    fireEvent.click(within(picker).getByRole("tab", { name: "링크 추가" }));
    fireEvent.change(within(picker).getByLabelText("링크 주소"), { target: { value: "https://example.com/new" } });
    fireEvent.change(within(picker).getByLabelText("링크 이름"), { target: { value: "방금 추가한 링크" } });
    fireEvent.click(within(picker).getByRole("button", { name: "링크 추가" }));
    await waitFor(() => expect(within(container).getByText("방금 추가한 링크")).toBeTruthy());

    rerender(<ActionTaskCard action={{ ...proposal, material_drafts: [] }} onCommand={onCommand} principalId="jiho" />);
    expect(within(container).getByText("방금 추가한 링크")).toBeTruthy();

    rerender(<ActionTaskCard action={{ ...proposal, material_drafts: [materialDraft("link-staged", "external_link", "방금 추가한 링크")] }} onCommand={onCommand} principalId="jiho" />);
    rerender(<ActionTaskCard action={{ ...proposal, material_drafts: [] }} onCommand={onCommand} principalId="jiho" />);
    expect(within(container).queryByText("방금 추가한 링크")).toBeNull();

    rerender(<ActionTaskCard action={{ ...proposal, material_drafts: [materialDraft("link-staged", "external_link", "방금 추가한 링크")] }} onCommand={onCommand} principalId="jiho" />);
    fireEvent.click(within(container).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("confirm", expect.objectContaining({
      attachment_draft_ids: ["link-staged"],
    })));
  });

  it("blocks confirmation while a file upload is incomplete and surfaces upload failure", async () => {
    let rejectUpload: (reason: Error) => void = () => undefined;
    vi.mocked(stageActionMaterialFile).mockReturnValue(new Promise((_resolve, reject) => { rejectUpload = reject; }));
    const { container } = render(<ActionTaskCard action={proposal} onCommand={vi.fn()} principalId="jiho" />);
    fireEvent.click(within(container).getByRole("button", { name: "수정" }));
    fireEvent.click(within(container).getByRole("button", { name: "업무나 자료 첨부" }));
    const picker = screen.getByRole("dialog", { name: "업무나 자료 첨부" });
    fireEvent.click(within(picker).getByRole("tab", { name: "파일 추가" }));
    const file = new File(["draft"], "업로드.txt", { type: "text/plain" });
    fireEvent.change(within(picker).getByLabelText("첨부할 파일"), { target: { files: [file] } });
    expect(within(container).getByText("업로드 중…")).toBeTruthy();
    expect((within(container).getByRole("button", { name: "저장" }) as HTMLButtonElement).disabled).toBe(true);

    const leaving = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(leaving);
    expect(leaving.defaultPrevented).toBe(true);
    expect(within(container).getByRole('button', { name: '취소' }).hasAttribute('disabled')).toBe(true);
    rejectUpload(new Error("storage unavailable"));
    await waitFor(() => expect(within(container).getByText(/실패 · storage unavailable/)).toBeTruthy());
    expect((within(container).getByRole("button", { name: "저장" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps only the latest attachment search response and hides results as soon as the query is cleared", async () => {
    const resolvers = new Map<string, (options: Array<{ value: string; label: string }>) => void>();
    const searchReferences = vi.fn((query: string) => query === "fail"
      ? Promise.reject(new Error("search unavailable"))
      : new Promise<Array<{ value: string; label: string }>>((resolve) => {
        resolvers.set(query, resolve);
      }));
    const onChangeDraft = vi.fn();
    render(
      <TaskAttachmentGroup
        contract={{
          ...proposal.edit_contract!,
          fields: proposal.edit_contract!.fields.map((field) => field.id === "reference_task_ids"
            ? { ...field, options: [{ value: "old", label: "오래된 결과" }, { value: "new", label: "최신 결과" }] }
            : field),
        }}
        draft={{ reference_task_ids: [] }}
        editable
        materials={[]}
        onAddFile={vi.fn()}
        onAddLink={vi.fn()}
        onChangeDraft={onChangeDraft}
        onRemoveMaterial={vi.fn()}
        onRemoveTransfer={vi.fn()}
        searchReferences={searchReferences}
        transfers={[]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "업무나 자료 첨부" }));
    const picker = screen.getByRole("dialog", { name: "업무나 자료 첨부" });
    const input = within(picker).getByLabelText("업무나 자료 검색");
    fireEvent.change(input, { target: { value: "old" } });
    fireEvent.change(input, { target: { value: "new" } });
    await act(async () => resolvers.get("new")?.([{ value: "new", label: "최신 결과" }]));
    expect(await within(picker).findByRole("button", { name: /최신 결과/ })).toBeTruthy();
    await act(async () => resolvers.get("old")?.([{ value: "old", label: "오래된 결과" }]));
    expect(within(picker).queryByText("오래된 결과")).toBeNull();
    expect(within(picker).getByText("최신 결과")).toBeTruthy();

    fireEvent.change(input, { target: { value: "late" } });
    fireEvent.change(input, { target: { value: "" } });
    expect(within(picker).getByText("찾고 싶은 업무나 자료를 검색하세요")).toBeTruthy();
    await act(async () => resolvers.get("late")?.([{ value: "old", label: "오래된 결과" }]));
    expect(within(picker).queryByText("오래된 결과")).toBeNull();
    fireEvent.change(input, { target: { value: "   " } });
    expect(within(picker).getByText("찾고 싶은 업무나 자료를 검색하세요")).toBeTruthy();
    fireEvent.change(input, { target: { value: "fail" } });
    expect(await within(picker).findByText("검색하지 못했습니다")).toBeTruthy();
    expect(onChangeDraft).not.toHaveBeenCalled();
  });

  it("restores an unsubmitted edit after the card is closed and mounted again for the same principal", () => {
    const first = render(<ActionTaskCard action={proposal} onCommand={vi.fn()} principalId="jiho" />);
    const firstCard = first.container.querySelector("[data-action-id='action-1']") as HTMLElement;
    fireEvent.click(within(firstCard).getByRole("button", { name: "수정" }));
    fireEvent.change(within(firstCard).getByLabelText("업무 명"), { target: { value: "브라우저에 남을 초안" } });
    first.unmount();

    const second = render(<ActionTaskCard action={proposal} onCommand={vi.fn()} principalId="jiho" />);
    const secondCard = second.container.querySelector("[data-action-id='action-1']") as HTMLElement;
    expect((within(secondCard).getByLabelText("업무 명") as HTMLInputElement).value).toBe("브라우저에 남을 초안");
    second.unmount();

    const other = render(<ActionTaskCard action={proposal} onCommand={vi.fn()} principalId="mina" />);
    expect(within(other.container).queryByLabelText("업무 명")).toBeNull();
    expect(within(other.container).getByText("AX 원안")).toBeTruthy();
  });

  it("drops fields outside the typed Task draft contract when recovering browser data", async () => {
    storedDraft({ ...proposal.edit_contract!.values, title: "복구할 초안", provider_html: "보이면 안 됨" });

    const { container } = render(<ActionTaskCard action={proposal} onCommand={vi.fn()} principalId="jiho" />);
    expect((within(container).getByLabelText("업무 명") as HTMLInputElement).value).toBe("복구할 초안");
    expect(within(container).queryByText("보이면 안 됨")).toBeNull();
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}") as Record<string, { draft: Record<string, unknown> }>;
      expect(Object.values(saved)[0].draft).not.toHaveProperty("provider_html");
    });
  });

  it("does not auto-merge a saved draft when the server advances the base Submission", async () => {
    const onCommand = vi.fn();
    const { container, rerender } = render(<ActionTaskCard action={proposal} onCommand={onCommand} principalId="jiho" />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    fireEvent.change(within(card).getByLabelText("업무 명"), { target: { value: "내 저장 초안" } });
    const latest: ActionItem = {
      ...proposal,
      version: 2,
      subject: "서버 최신안",
      edit_contract: {
        ...proposal.edit_contract!,
        base_submission_version: 2,
        values: { ...proposal.edit_contract!.values, title: "서버 최신안" },
      },
    };

    rerender(<ActionTaskCard action={latest} onCommand={onCommand} principalId="jiho" />);

    await waitFor(() => expect(within(card).getByRole("status", { name: "저장 초안 충돌" }).textContent).toContain("자동으로 합치지 않았습니다"));
    expect(within(card).getByText("내 초안: 내 저장 초안")).toBeTruthy();
    expect(within(card).getByText("최신안: 서버 최신안")).toBeTruthy();
    expect((within(card).getByRole("button", { name: "저장" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(card).getByRole("button", { name: "최신안으로 다시 시작" }));
    expect((within(card).getByLabelText("업무 명") as HTMLInputElement).value).toBe("서버 최신안");
    expect(JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}")).toEqual({});
  });

  it("rebases a stale browser draft only after the user explicitly chooses it", () => {
    storedDraft({ ...proposal.edit_contract!.values, title: "사용자가 고른 초안" });
    const latest: ActionItem = {
      ...proposal,
      version: 2,
      subject: "서버 최신안",
      edit_contract: {
        ...proposal.edit_contract!,
        base_submission_version: 2,
        values: { ...proposal.edit_contract!.values, title: "서버 최신안" },
      },
    };
    const { container } = render(<ActionTaskCard action={latest} onCommand={vi.fn()} principalId="jiho" />);

    fireEvent.click(within(container).getByRole("button", { name: "내 초안으로 다시 편집" }));

    expect((within(container).getByLabelText("업무 명") as HTMLInputElement).value).toBe("사용자가 고른 초안");
    const saved = JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}") as Record<string, { base_submission_version: number }>;
    expect(Object.values(saved).map((record) => record.base_submission_version)).toEqual([2]);
  });

  it("edits in the same card and confirms the complete draft against its base Submission", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ActionTaskCard action={proposal} onCommand={onCommand} principalId="jiho" />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;

    expect(within(card).getByText("AX 원안")).toBeTruthy();
    expect(within(card).getByText("지호")).toBeTruthy();
    expect(within(card).queryByText("지호 (팀장)")).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));

    const title = within(card).getByLabelText("업무 명") as HTMLInputElement;
    expect(title.value).toBe("AX 원안");
    fireEvent.change(title, { target: { value: "사람이 고친 안" } });
    fireEvent.click(within(card).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1));
    expect(onCommand).toHaveBeenCalledWith("confirm", {
      base_submission_version: 1,
      draft: {
        title: "사람이 고친 안",
        description: "처음 설명",
        start_date: null,
        due_date: "2026-09-20",
        checklist: ["준비"],
        reference_task_ids: [],
        parent_task_id: null,
        project_id: null,
      },
    });
    expect(JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}")).toEqual({});
  });

  it("resets only the local draft and keeps a no-change confirmation on Submission 1", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ActionTaskCard action={proposal} onCommand={onCommand} principalId="jiho" />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    fireEvent.change(within(card).getByLabelText("업무 명"), { target: { value: "임시 변경" } });
    fireEvent.click(within(card).getByRole("button", { name: "초기화" }));
    expect((within(card).getByLabelText("업무 명") as HTMLInputElement).value).toBe("AX 원안");
    expect(JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}")).toEqual({});

    fireEvent.click(within(card).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1));
    expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 1 });
  });

  it("cancels an inline edit back to the registration summary without retaining the local field draft", () => {
    const onCommand = vi.fn();
    const { container } = render(<ActionTaskCard action={proposal} onCommand={onCommand} principalId="jiho" />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    fireEvent.change(within(card).getByLabelText("업무 명"), { target: { value: "취소할 초안" } });

    fireEvent.click(within(card).getByRole("button", { name: "취소" }));

    expect(within(card).queryByLabelText("업무 명")).toBeNull();
    expect(within(card).getByText("AX 원안")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "등록" })).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "거절" })).toBeNull();
    expect(onCommand).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}")).toEqual({});
  });

  it("renders the persisted receipt and opens the Task without sending confirm again", () => {
    const onCommand = vi.fn();
    const onOpenTask = vi.fn();
    const resolved: ActionItem = {
      ...proposal,
      state: "approved",
      version: 2,
      commands: [],
      result: { task_id: "task-1" },
    };
    const { container } = render(<ActionTaskCard action={resolved} onCommand={onCommand} onOpenTask={onOpenTask} />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    expect(within(card).getByText("내 업무")).toBeTruthy();
    expect(within(container).getByText("업무가 등록되었습니다.")).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "수정" })).toBeNull();
    expect(within(card).queryByRole("button", { name: "등록" })).toBeNull();
    expect(within(card).queryByRole("button", { name: "업무나 자료 첨부" })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "업무 상세보기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-1");
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("renders the cancellable request receipt and opens its detail without confirming again", async () => {
    const assigned: ActionItem = {
      ...proposal,
      action_type: "task.assign",
      operation_label: "업무 요청",
      state: "approved",
      commands: [{ id: "cancel_assignment", label: "취소", tone: "danger" }],
      preview: [
        { id: "assignee", label: "담당자", value: "민아 (구성원)", kind: "person" },
        { id: "requester", label: "요청자", value: "지호 (팀장)", kind: "person" },
      ],
      result: {
        assignment_id: "assignment-1",
        status: "pending",
        task: { task_id: "task-2", title: "요청한 업무" },
      },
    };
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const onOpenTask = vi.fn();
    const { container } = render(<ActionTaskCard action={assigned} onCommand={onCommand} onOpenTask={onOpenTask} />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    expect(within(card).getByText("요청")).toBeTruthy();
    expect(within(container).getByText(/민아님에게 업무가 요청되었습니다/)).toBeTruthy();
    expect(within(container).getByText(/상대방이 요청을 확인하기 전까지 취소할 수 있어요/)).toBeTruthy();
    expect(within(container).queryByText("업무가 등록되었습니다.")).toBeNull();
    const cancel = within(card).getByRole("button", { name: "취소" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith("cancel_assignment", undefined));
    fireEvent.click(within(card).getByRole("button", { name: "요청내용 보기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-2");
    expect(onCommand).toHaveBeenCalledTimes(1);

    const cancelled: ActionItem = {
      ...assigned,
      commands: [],
      result: { ...assigned.result, status: "cancelled" },
    };
    cleanup();
    const settled = render(<ActionTaskCard action={cancelled} onCommand={onCommand} onOpenTask={onOpenTask} />);
    expect(within(settled.container).getByText("취소됨")).toBeTruthy();
    expect(within(settled.container).getByText("업무 요청을 취소했습니다.")).toBeTruthy();
    expect(within(settled.container).queryByRole("button", { name: "취소" })).toBeNull();
  });

  it("presents a pending assignment as a request with its target and requester", () => {
    const request: ActionItem = {
      ...proposal,
      action_type: "task.assign",
      operation_label: "업무 요청",
      preview: [
        { id: "description", label: "설명", value: "요청 설명", kind: "text" },
        { id: "assignee", label: "담당자", value: "민아 (구성원)", kind: "person" },
        { id: "requester", label: "요청자", value: "지호 (팀장)", kind: "person" },
        { id: "due_date", label: "기한", value: "2026-09-30", kind: "date" },
        { id: "checklist", label: "체크리스트", value: "2단계 · 초안 → 검토", kind: "text" },
      ],
      edit_contract: {
        ...proposal.edit_contract!,
        values: { ...proposal.edit_contract!.values, checklist: ["초안", "검토"] },
      },
    };

    const { container } = render(<ActionTaskCard action={request} onCommand={vi.fn()} principalId="jiho" />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    const requestTarget = within(container).getByLabelText("요청 대상");
    expect(within(requestTarget).getByText("민아")).toBeTruthy();
    expect(within(requestTarget).queryByText("민아 (구성원)")).toBeNull();
    expect(within(card).getByText("요청")).toBeTruthy();
    expect(within(card).getByText("담당자")).toBeTruthy();
    expect(within(card).getByText("요청자")).toBeTruthy();
    expect(within(card).getByText("체크리스트")).toBeTruthy();
    const checklist = within(card).getByRole("list", { name: "체크리스트 항목" });
    expect(within(checklist).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["초안", "검토"]);
    expect(within(card).queryByText(/→/)).toBeNull();
    expect(within(card).getByText("민아")).toBeTruthy();
    expect(within(card).getByText("지호")).toBeTruthy();
    expect(within(card).queryByText("민아 (구성원)")).toBeNull();
    expect(within(card).queryByText("지호 (팀장)")).toBeNull();
  });

  it("focuses an invalid field without discarding the inline draft", () => {
    const onCommand = vi.fn();
    const { container } = render(<ActionTaskCard action={proposal} onCommand={onCommand} />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    // 기한은 원안 그대로 09-20 이고, 시작일을 그 뒤로 옮겨 어긋나게 한다
    pickDate(card, "시작일", "2026-09-30");

    fireEvent.click(within(card).getByRole("button", { name: "저장" }));

    expect(within(card).getByRole("alert").textContent).toContain("기한은 시작일보다 빠를 수 없습니다");
    expect(document.activeElement).toBe(dateTrigger(card, "기한"));
    expect(dateTrigger(card, "시작일").textContent).toBe("2026.09.30");
    expect(onCommand).not.toHaveBeenCalled();
  });

  it("keeps the edited draft visible when the server rejects a stale base", async () => {
    const onCommand = vi.fn().mockRejectedValue(new Error("base submission version is stale"));
    const { container } = render(<ActionTaskCard action={proposal} onCommand={onCommand} />);
    const card = container.querySelector("[data-action-id='action-1']") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    fireEvent.change(within(card).getByLabelText("업무 명"), { target: { value: "보존할 수정안" } });
    fireEvent.click(within(card).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(within(card).getByRole("alert").textContent).toContain("수정한 내용은 유지"));
    expect((within(card).getByLabelText("업무 명") as HTMLInputElement).value).toBe("보존할 수정안");
  });
});
