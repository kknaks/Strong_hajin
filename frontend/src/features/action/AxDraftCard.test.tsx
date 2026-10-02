import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (original) => ({
  ...(await original<typeof import("../../lib/api")>()),
  getTasks: vi.fn().mockResolvedValue([]),
  listProjects: vi.fn().mockResolvedValue([]),
  runActionCommand: vi.fn(),
  stageActionMaterialLink: vi.fn(),
  stageActionMaterialFile: vi.fn(),
  discardActionMaterialDraft: vi.fn(),
}));

import type { ActionEditContract, ActionItemEnvelope } from "../../lib/viewModels";
import { AxDraftCard, AxDraftModal, axDraftAgeDays, axDraftFromAction, axDraftFromEnvelope, type AxDraftSource } from "./AxDraftCard";
import * as api from "../../lib/api";
import { hasPendingBrowserOperation } from "../../lib/browserOperationGuard";

/* WORK-008 3b · SPEC-002 §2.9 — AX 업무 초안 요약 카드. */

const contract = (values: Record<string, unknown> = {}): ActionEditContract => ({
  editor: "task",
  base_submission_version: 2,
  values: {
    title: "KPI 설정",
    description: "분기 KPI 를 정한다",
    start_date: "2026-10-01",
    due_date: "2026-10-06",
    checklist: [],
    reference_task_ids: [],
    preceding_task_ids: [],
    parent_task_id: null,
    project_id: null,
    cc_member_ids: ["sora"],
    approver_id: null,
    assignee_id: "mina",
    ...values,
  },
  fields: [
    { id: "title", label: "제목", type: "text", required: true, editable: true },
    { id: "description", label: "내용", type: "textarea", required: false, editable: true },
    { id: "start_date", label: "시작일", type: "date", required: false, editable: true },
    { id: "due_date", label: "기한", type: "date", required: false, editable: true },
    { id: "cc_member_ids", label: "참조자", type: "multi_select", required: false, editable: true, options: [{ value: "sora", label: "소라 (기획)" }] },
    { id: "project_id", label: "프로젝트", type: "select", required: false, editable: true, options: [{ value: "p-1", label: "한빛 마케팅" }] },
    { id: "parent_task_id", label: "상위 업무", type: "select", required: false, editable: true, options: [{ value: "t-9", label: "상위 일" }] },
    { id: "assignee_id", label: "담당", type: "person", required: false, editable: false, value: "mina", label_value: "민아 (구성원)" },
  ],
});

const source = (over: Partial<AxDraftSource> = {}): AxDraftSource => ({
  actionId: "action-1",
  kind: "task",
  title: "KPI 설정",
  round: 1,
  state: "pending",
  contract: contract(),
  materials: [],
  commands: [
    { id: "reject", label: "거절", tone: "neutral" },
    { id: "confirm", label: "등록", tone: "primary" },
  ],
  createdAt: null,
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const card = () => screen.getByRole("region", { name: /AX 업무 생성/ });
/** 지금 페이지의 라벨 · 값 줄 — 「라벨=값」 으로 읽는다(E2E 1: 항목은 늘 서고 빈 값은 「없음」). */
const rows = () =>
  Array.from(within(card()).getByRole("tabpanel").querySelectorAll("dl > div")).map(
    (row) => `${row.querySelector("dt")?.textContent}=${row.querySelector("dd")?.textContent}`,
  );
const pageName = () => within(card()).getByRole("tabpanel").getAttribute("aria-label");

describe("AX 초안 요약 카드 — 머리와 넘기기", () => {
  it("머리에 배지 「AX」·회차·종류·제목·4칸 바·현재 페이지 이름이 서고 「SC AX」 가 없다", () => {
    render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    expect(within(card()).getByText("AX")).toBeTruthy();
    expect(within(card()).getByText("초안 · 1회차")).toBeTruthy();
    expect(within(card()).getByText("업무 생성")).toBeTruthy();
    expect(within(card()).getAllByRole("tab")).toHaveLength(4);
    expect(within(card()).getByRole("tab", { name: "기본 정보" }).getAttribute("aria-selected")).toBe("true");
    expect(document.body.textContent).not.toContain("SC AX");
    // 무채색 — 배지 「AX」·「초안 · N회차」 는 회색 중립 배지다 (E2E 1).
    expect(within(card()).getByText("AX").className).toContain("neutral");
    expect(within(card()).getByText("초안 · 1회차").className).toContain("neutral");
  });

  it("4칸 바 · ‹ › · ← → · 좌우 스와이프로 넘어가고, ‹ › 는 늘 보이며 첫/끝 페이지에서 그쪽이 비활성이다 (E2E 8 · 9)", () => {
    render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    const arrow = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
    expect(arrow("이전 페이지").disabled).toBe(true);
    expect(arrow("다음 페이지").disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "다음 페이지" }));
    expect(pageName()).toBe("체크리스트");
    fireEvent.click(within(card()).getByRole("tab", { name: "자료" }));
    expect(pageName()).toBe("자료");
    expect(arrow("다음 페이지").disabled).toBe(true);
    expect(arrow("이전 페이지").disabled).toBe(false);
    const body = within(card()).getByRole("tabpanel");
    fireEvent.keyDown(body, { key: "ArrowLeft" });
    expect(pageName()).toBe("업무 연결");
    fireEvent.keyDown(body, { key: "ArrowRight" });
    expect(pageName()).toBe("자료");
    fireEvent.click(screen.getByRole("button", { name: "이전 페이지" }));
    expect(pageName()).toBe("업무 연결");
    // 트랙패드 — 가로로 충분히 밀면 한 장
    fireEvent.wheel(within(card()).getByRole("tabpanel"), { deltaX: -60, deltaY: 0 });
    expect(pageName()).toBe("체크리스트");
  });

  it("본문은 라벨 · 값 두 열이고 순서가 고정이며, 빈 값은 숨기지 않고 「없음」 이다 (E2E 1)", () => {
    render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    // 갈래 · 기간 · 참조자 · 결재자 · 내용 — 결재자가 비어도 줄은 선다.
    expect(rows()).toEqual(["갈래=내 업무", "기간=2026/10/01 → 2026/10/06", "참조자=소라", "결재자=없음", "내용=분기 KPI 를 정한다"]);
    fireEvent.click(within(card()).getByRole("tab", { name: "체크리스트" }));
    expect(rows()).toEqual(["=없음"]);
    fireEvent.click(within(card()).getByRole("tab", { name: "업무 연결" }));
    expect(rows()).toEqual(["상위=없음", "프로젝트=없음", "참고=없음", "선행=없음"]);
    fireEvent.click(within(card()).getByRole("tab", { name: "자료" }));
    expect(rows()).toEqual(["파일=없음", "링크=없음"]);
  });

  it("기간은 하나만 있으면 있는 쪽만, 둘 다 없으면 없음 · 요청은 담당 후보가 기간 뒤에 선다", () => {
    const { unmount } = render(<AxDraftCard onCommand={vi.fn()} source={source({ contract: contract({ start_date: null }) })} />);
    expect(rows()[1]).toBe("기간=~ 2026/10/06");
    unmount();
    const empty = render(<AxDraftCard onCommand={vi.fn()} source={source({ contract: contract({ start_date: null, due_date: null, cc_member_ids: [], description: null }) })} />);
    expect(rows()).toEqual(["갈래=내 업무", "기간=없음", "참조자=없음", "결재자=없음", "내용=없음"]);
    empty.unmount();
    const request = contract({ assignee_id: "jiho" });
    request.fields = request.fields.map((field) => (field.id === "assignee_id" ? { ...field, options: [{ value: "jiho", label: "지호 (팀장)" }] } : field));
    render(<AxDraftCard onCommand={vi.fn()} source={source({ kind: "request", contract: request })} />);
    expect(screen.getByRole("region", { name: /AX 업무 요청/ })).toBeTruthy();
    const requestRows = Array.from(screen.getByRole("tabpanel").querySelectorAll("dl > div")).map((row) => row.querySelector("dt")?.textContent);
    expect(requestRows).toEqual(["갈래", "기간", "담당 후보", "참조자", "결재자", "내용"]);
  });

  it("채운 페이지는 요약 문구로 선다 — 체크리스트 · 업무 연결 · 자료", () => {
    render(
      <AxDraftCard
        onCommand={vi.fn()}
        source={source({
          contract: contract({ checklist: ["자료 모으기", "초안 쓰기"], project_id: "p-1", parent_task_id: "t-9", reference_task_ids: ["a", "b"], preceding_task_ids: ["c"] }),
          materials: [
            { material_draft_id: "m1", action_item_id: "action-1", source_kind: "file", name: "a.pdf", content_type: "application/pdf", size_bytes: 1, url: null, integrity_ref: "x", state: "staged", expires_at: "", claimed_task_id: null },
            { material_draft_id: "m2", action_item_id: "action-1", source_kind: "external_link", name: "가이드", content_type: "", size_bytes: 0, url: "https://example.test", integrity_ref: "y", state: "staged", expires_at: "", claimed_task_id: null },
          ],
        })}
      />,
    );
    fireEvent.click(within(card()).getByRole("tab", { name: "체크리스트" }));
    expect(rows()).toEqual(["=자료 모으기", "=초안 쓰기"]);
    fireEvent.click(within(card()).getByRole("tab", { name: "업무 연결" }));
    // 개수가 아니라 이름이다 — 이름은 편집 계약 선택지에서 찾고, 없으면 값 그대로다 (E2E 7).
    expect(rows()).toEqual(["상위=상위 일", "프로젝트=한빛 마케팅", "참고=a", "=b", "선행=c"]);
    fireEvent.click(within(card()).getByRole("tab", { name: "자료" }));
    expect(rows()).toEqual(["파일=a.pdf", "링크=가이드"]);
  });
});

describe("AX 초안 요약 카드 — 명령", () => {
  it("[등록] 은 초안 그대로 확인한다 — 고친 초안 없이, 자료 초안은 함께", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(
      <AxDraftCard
        onCommand={onCommand}
        source={source({ materials: [{ material_draft_id: "m1", action_item_id: "action-1", source_kind: "file", name: "a.pdf", content_type: "", size_bytes: 1, url: null, integrity_ref: "x", state: "staged", expires_at: "", claimed_task_id: null }] })}
      />,
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "등록" })));
    expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2, attachment_draft_ids: ["m1"] });
  });

  it("기한이 비어 있어도 [등록] 이 막히지 않는다 (P-1)", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source({ contract: contract({ due_date: null }) })} />);
    expect((screen.getByRole("button", { name: "등록" }) as HTMLButtonElement).disabled).toBe(false);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "등록" })));
    expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 2 });
  });

  it("[거절] 은 기존 거절 명령이다", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "거절" })));
    expect(onCommand).toHaveBeenCalledWith("reject", undefined);
  });

  it("등록 뒤에는 한 줄 요약(제목 · 기한 · 담당)과 [업무 열기] 로 접힌다", () => {
    const onOpenTask = vi.fn();
    render(<AxDraftCard onCommand={vi.fn()} onOpenTask={onOpenTask} source={source({ state: "approved", resultTaskId: "task-7" })} />);
    const done = document.querySelector(".ax-draft-card--done") as HTMLElement;
    expect(done.textContent).toContain("KPI 설정");
    expect(done.textContent).toContain("2026/10/06");
    expect(done.textContent).toContain("민아");
    expect(screen.queryByRole("tabpanel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "업무 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-7");
  });

  it("기억한 봉투로 그린 카드(locked)는 명령을 열지 않는다", () => {
    render(<AxDraftCard locked onCommand={vi.fn()} source={source()} />);
    for (const name of ["거절", "수정", "등록"]) expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("AX 초안 요약 카드 — [수정] = 「새 업무 추가」 창", () => {
  it("초안 값으로 열리고 갈래가 고정되며, 창의 등록이 고친 값으로 확인한다(confirm + draft)", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    expect((within(modal).getByLabelText("업무 제목") as HTMLInputElement).value).toBe("KPI 설정");
    // 갈래 토글이 없다 — 초안의 갈래가 곧 명령이다.
    expect(within(modal).queryByRole("tablist", { name: "생성 유형" })).toBeNull();
    expect((within(within(modal).getByRole("group", { name: "참조자" })).getByRole("checkbox", { name: "소라" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.change(within(modal).getByLabelText("업무 제목"), { target: { value: "KPI 설정 (고침)" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "등록" })));

    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    const [command, payload] = onCommand.mock.calls[0];
    expect(command).toBe("confirm");
    expect(payload.base_submission_version).toBe(2);
    expect(payload.draft).toMatchObject({
      title: "KPI 설정 (고침)",
      description: "분기 KPI 를 정한다",
      start_date: "2026-10-01",
      due_date: "2026-10-06",
      cc_member_ids: ["sora"],
      assignee_id: "mina", // 창에 칸이 없는 값은 초안 것 그대로 돌아간다
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "새 업무 추가" })).toBeNull());
  });

  it("창을 닫으면 고친 것을 버린다 — 카드는 초안 그대로이고 아무것도 보내지 않는다", async () => {
    const onCommand = vi.fn();
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.change(within(modal).getByLabelText("업무 제목"), { target: { value: "버릴 제목" } });
    fireEvent.click(within(modal).getAllByRole("button", { name: "닫기" }).at(-1)!);
    expect(screen.queryByRole("dialog", { name: "새 업무 추가" })).toBeNull();
    expect(within(card()).getByText("KPI 설정")).toBeTruthy();
    expect(onCommand).not.toHaveBeenCalled();
    // 다시 열면 초안 값으로 다시 선다.
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    expect((within(await screen.findByRole("dialog", { name: "새 업무 추가" })).getByLabelText("업무 제목") as HTMLInputElement).value).toBe("KPI 설정");
  });

  it("업무 요청 초안은 「새 업무 요청」 창으로 열리고 담당 후보가 초안의 사람이다", async () => {
    const request = contract({ assignee_id: "jiho" });
    request.fields = request.fields.map((field) =>
      field.id === "assignee_id" ? { ...field, editable: true, required: true, options: [{ value: "jiho", label: "지호 (팀장)" }] } : field,
    );
    render(<AxDraftCard onCommand={vi.fn()} source={source({ kind: "request", contract: request })} />);
    expect(screen.getByText("업무 요청")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 요청" });
    expect((within(modal).getByLabelText("요청할 업무") as HTMLInputElement).value).toBe("KPI 설정");
    expect(within(modal).queryByRole("tablist", { name: "생성 유형" })).toBeNull();
  });
});

describe("판단 대기 봉투 → 같은 카드", () => {
  const envelope = (over: Partial<ActionItemEnvelope> = {}): ActionItemEnvelope => ({
    action_item_id: "action-1",
    kind: "ax.task.create_self",
    status: "awaiting_review",
    subject: "KPI 설정",
    operation_label: "업무 생성",
    current_question: "등록할까요?",
    preview: [],
    allowed_commands: [{ id: "confirm", label: "등록", tone: "primary" }],
    submission_version: 2,
    waiting_on: null,
    resource: { type: "action", id: "action-1" },
    expected_version: 5,
    edit_contract: contract(),
    created_at: "2026-09-28T01:00:00+00:00",
    ...over,
  });

  it("두 kind 만 카드가 된다 — 다른 AX 카드와 편집 계약 없는 봉투는 아니다", () => {
    expect(axDraftFromEnvelope(envelope())).not.toBeNull();
    expect(axDraftFromEnvelope(envelope({ kind: "ax.work_request.create" }))?.kind).toBe("request");
    expect(axDraftFromEnvelope(envelope({ kind: "ax.meeting.reservation.create" }))).toBeNull();
    expect(axDraftFromEnvelope(envelope({ edit_contract: undefined }))).toBeNull();
  });

  it("만든 지 며칠은 만든 시각에서 서울 날짜로 센다 — 만료가 없다", () => {
    expect(axDraftAgeDays("2026-09-28T01:00:00+00:00", "2026-10-01")).toBe(3);
    expect(axDraftAgeDays(null, "2026-10-01")).toBeNull();
  });

  it("모달 카드의 [등록] 은 판단 원장 입구로 간다 — expected_version 과 회차를 싣는다", async () => {
    vi.mocked(api.runActionCommand).mockResolvedValue({} as never);
    const onDone = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    render(<AxDraftModal item={envelope()} onClose={onClose} onDone={onDone} />);
    expect(screen.getByRole("dialog", { name: "AX 제안" })).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "등록" })));
    expect(api.runActionCommand).toHaveBeenCalledWith("action-1", "confirm", { expected_version: 5, base_submission_version: 2 });
    expect(onDone).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   3b fix1 — 검수 FAIL-1 · WARN-1 · WARN-3 · WARN-4
   ════════════════════════════════════════════════════════════════════════════ */
describe("수정 창 — 참고 업무는 목록이 오기 전·실패·못 읽는 업무여도 초안 값 그대로 (FAIL-1)", () => {
  const withReferences = () => source({ contract: contract({ reference_task_ids: ["r-1", "r-2"] }) });

  async function editAndRegister(onCommand: ReturnType<typeof vi.fn>) {
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "등록" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    return onCommand.mock.calls[0][1].draft as Record<string, unknown>;
  }

  it("읽을 수 있는 업무 목록이 오기 전에 등록해도 참고 업무가 남는다", async () => {
    vi.mocked(api.getTasks).mockReturnValue(new Promise(() => {}) as never);
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={withReferences()} />);
    expect((await editAndRegister(onCommand)).reference_task_ids).toEqual(["r-1", "r-2"]);
  });

  it("목록 조회가 실패해도 참고 업무가 남는다", async () => {
    vi.mocked(api.getTasks).mockRejectedValue(new Error("down"));
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={withReferences()} />);
    expect((await editAndRegister(onCommand)).reference_task_ids).toEqual(["r-1", "r-2"]);
  });

  it("목록에 없는(못 읽는) 업무는 표에 「읽을 수 없는 업무」로 말하고 draft 에 그대로 싣는다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "r-1", title: "읽을 수 있는 업무", state: "open", version: 1, block_reason: null }] as never);
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={withReferences()} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(modal).getByRole("tab", { name: "업무 연결" }));
    expect(await within(modal).findByText("목록에 없는 참고 업무 1건 — 초안 그대로 함께 등록됩니다.")).toBeTruthy();
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "등록" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    expect(onCommand.mock.calls[0][1].draft.reference_task_ids).toEqual(["r-1", "r-2"]);
  });
});

describe("수정 창 — 확인 전에 자료를 붙인다 (WARN-1)", () => {
  it("링크를 붙이면 판단 항목 자료 초안으로 올라가고, 등록이 그 id 를 싣고, 카드 요약도 따라간다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([] as never);
    vi.mocked(api.stageActionMaterialLink).mockResolvedValue({
      material_draft_id: "md-1", action_item_id: "action-1", source_kind: "external_link", name: "가이드", content_type: "", size_bytes: 0,
      url: "https://example.test", integrity_ref: "x", state: "staged", expires_at: "", claimed_task_id: null,
    });
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(modal).getByRole("tab", { name: "자료" }));
    fireEvent.change(within(modal).getByLabelText("링크 주소"), { target: { value: "https://example.test" } });
    fireEvent.change(within(modal).getByLabelText("링크 이름"), { target: { value: "가이드" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "링크 추가" })));
    expect(api.stageActionMaterialLink).toHaveBeenCalledWith("action-1", { url: "https://example.test", label: "가이드" });
    expect(await within(modal).findByText("가이드")).toBeTruthy();

    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "등록" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    expect(onCommand.mock.calls[0][1].attachment_draft_ids).toEqual(["md-1"]);
  });

  it("창을 닫아도 붙인 자료는 초안에 남아 카드 자료 요약이 바뀌고, [등록] 도 그 id 를 싣는다 — 빼면 버린다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([] as never);
    vi.mocked(api.stageActionMaterialLink).mockResolvedValue({
      material_draft_id: "md-2", action_item_id: "action-1", source_kind: "external_link", name: "회의록", content_type: "", size_bytes: 0,
      url: "https://example.test/m", integrity_ref: "y", state: "staged", expires_at: "", claimed_task_id: null,
    });
    vi.mocked(api.discardActionMaterialDraft).mockResolvedValue(undefined);
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    let modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(modal).getByRole("tab", { name: "자료" }));
    fireEvent.change(within(modal).getByLabelText("링크 주소"), { target: { value: "https://example.test/m" } });
    fireEvent.change(within(modal).getByLabelText("링크 이름"), { target: { value: "회의록" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "링크 추가" })));
    fireEvent.click(within(modal).getAllByRole("button", { name: "닫기" }).at(-1)!);
    fireEvent.click(within(card()).getByRole("tab", { name: "자료" }));
    expect(rows()).toEqual(["파일=없음", "링크=회의록"]);

    // 다시 열어 빼면 서버의 초안을 버리고 요약도 「없음」으로 돌아간다.
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(modal).getByRole("tab", { name: "자료" }));
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "회의록 빼기" })));
    expect(api.discardActionMaterialDraft).toHaveBeenCalledWith("action-1", "md-2");
    fireEvent.click(within(modal).getAllByRole("button", { name: "닫기" }).at(-1)!);
    expect(rows()).toEqual(["파일=없음", "링크=없음"]);
  });
});

describe("채팅 카드의 만든 지 며칠 · 결재자 선택지 (WARN-3 · WARN-4)", () => {
  it("채팅 action 이 created_at 을 실으면 카드가 「만든 지 며칠」을 낸다 — 없으면 세우지 않는다", () => {
    const action = {
      action_id: "action-1", conversation_id: "c", turn_id: "t", action_type: "task.create_self", title: "업무 만들기",
      state: "pending" as const, version: 1, payload_summary: "", result: null, audit_ref: null, subject: "KPI 설정",
      commands: [{ id: "confirm", label: "등록", tone: "primary" }], edit_contract: contract(),
    };
    expect(axDraftFromAction({ ...action, created_at: "2026-09-29T01:00:00+00:00" })?.createdAt).toBe("2026-09-29T01:00:00+00:00");
    expect(axDraftFromAction(action)?.createdAt).toBeNull();
    const { unmount } = render(<AxDraftCard onCommand={vi.fn()} source={axDraftFromAction({ ...action, created_at: "2000-01-01T00:00:00+00:00" })!} />);
    expect(screen.getByText(/만든 지 \d+일/)).toBeTruthy();
    unmount();
    render(<AxDraftCard onCommand={vi.fn()} source={axDraftFromAction(action)!} />);
    expect(screen.queryByText(/만든 지/)).toBeNull();
  });

  it("편집 계약의 결재자 선택지를 창의 결재자 후보에 합친다 — 초안 결재자가 이름으로 선다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([] as never);
    const withApprover = contract({ approver_id: "boss" });
    withApprover.fields = [...withApprover.fields, { id: "approver_id", label: "결재자", type: "person", required: false, editable: true, options: [{ value: "boss", label: "김결재 (본부장)" }] }];
    render(<AxDraftCard onCommand={vi.fn()} source={source({ contract: withApprover })} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    expect(within(modal).getByLabelText("결재자").textContent).toContain("김결재");
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   3b fix2 — 재검수 N-1 · N-2 · N-3
   ════════════════════════════════════════════════════════════════════════════ */
describe("수정 창 — 재검수 N-1 ~ N-3", () => {
  const staged = (id: string, name: string) => ({
    material_draft_id: id, action_item_id: "action-1", source_kind: "file" as const, name, content_type: "", size_bytes: 1,
    url: null, integrity_ref: id, state: "staged" as const, expires_at: "", claimed_task_id: null,
  });

  async function openMaterials() {
    vi.mocked(api.getTasks).mockResolvedValue([] as never);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(modal).getByRole("tab", { name: "자료" }));
    return modal;
  }
  const pickFiles = (modal: HTMLElement, files: File[]) =>
    fireEvent.change(modal.querySelector('input[type="file"]') as HTMLInputElement, { target: { files } });

  it("N-1 파일을 올리는 중에는 새로 고침·이탈을 막고, 다 올리면 푼다", async () => {
    let finish!: (value: unknown) => void;
    vi.mocked(api.stageActionMaterialFile).mockReturnValue(new Promise((done) => (finish = done)) as never);
    render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    const modal = await openMaterials();
    pickFiles(modal, [new File(["x"], "큰파일.pdf")]);
    await waitFor(() => expect(hasPendingBrowserOperation()).toBe(true));
    await act(async () => finish(staged("md-9", "큰파일.pdf")));
    await waitFor(() => expect(hasPendingBrowserOperation()).toBe(false));
  });

  it("N-2 두 파일이 겹쳐 끝나도 둘 다 남고, 카드 요약도 둘을 센다", async () => {
    const answers: Array<(value: unknown) => void> = [];
    vi.mocked(api.stageActionMaterialFile).mockImplementation(() => new Promise((done) => answers.push(done)) as never);
    render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    const modal = await openMaterials();
    pickFiles(modal, [new File(["a"], "a.pdf"), new File(["b"], "b.pdf")]);
    await waitFor(() => expect(answers).toHaveLength(2));
    await act(async () => answers[1](staged("md-b", "b.pdf")));
    await act(async () => answers[0](staged("md-a", "a.pdf")));
    expect(within(modal).getByText("a.pdf")).toBeTruthy();
    expect(within(modal).getByText("b.pdf")).toBeTruthy();
    fireEvent.click(within(modal).getAllByRole("button", { name: "닫기" }).at(-1)!);
    fireEvent.click(within(card()).getByRole("tab", { name: "자료" }));
    expect(rows()).toEqual(["파일=b.pdf", "=a.pdf", "링크=없음"]);
  });

  it("N-3 목록 조회가 실패하면 「불러오지 못했다」고 말하고, 표에 못 선 참고 업무도 빼면 draft 에서 빠진다", async () => {
    vi.mocked(api.getTasks).mockRejectedValue(new Error("down"));
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source({ contract: contract({ reference_task_ids: ["ref-aaaa-1111", "ref-bbbb-2222"] }) })} />);
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    fireEvent.click(within(modal).getByRole("tab", { name: "업무 연결" }));
    expect(await within(modal).findByText("참고 업무 목록을 불러오지 못했습니다 — 초안의 참고 업무 2건은 그대로 함께 등록됩니다.")).toBeTruthy();
    fireEvent.click(within(modal).getByRole("button", { name: "참고 업무 ref-aaaa 빼기" }));
    expect(within(modal).getByText("참고 업무 목록을 불러오지 못했습니다 — 초안의 참고 업무 1건은 그대로 함께 등록됩니다.")).toBeTruthy();
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "등록" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalled());
    expect(onCommand.mock.calls[0][1].draft.reference_task_ids).toEqual(["ref-bbbb-2222"]);
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   WORK-008 E2E 묶음 3~11 — 제목 줄 · 채워지는 진행 바 · 실제 목록 · 「외 N개」
   ════════════════════════════════════════════════════════════════════════════ */
describe("AX 초안 카드 — E2E 묶음", () => {
  it("본문 위 제목 줄에 지금 페이지 이름이 서고, 머리에는 옛 옅은 페이지 이름이 없다 (E2E 8 · 9)", () => {
    const { container } = render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    expect(within(card()).getByRole("tabpanel").querySelector(".ax-draft-card__pagetitle")?.textContent).toBe("기본 정보");
    expect(container.querySelector(".ax-draft-card__page")).toBeNull();
    expect(container.querySelector(".ax-draft-card__nav")).toBeNull();
    fireEvent.click(within(card()).getByRole("tab", { name: "업무 연결" }));
    expect(within(card()).getByRole("tabpanel").querySelector(".ax-draft-card__pagetitle")?.textContent).toBe("업무 연결");
  });

  it("4칸 바는 채워지는 진행 바다 — 지금 페이지까지 검정, 그 뒤 회색 (E2E 10)", () => {
    render(<AxDraftCard onCommand={vi.fn()} source={source()} />);
    const on = () => within(card()).getAllByRole("tab").map((tab) => tab.className.includes("--on"));
    expect(on()).toEqual([true, false, false, false]);
    fireEvent.click(within(card()).getByRole("tab", { name: "업무 연결" }));
    expect(on()).toEqual([true, true, true, false]);
    fireEvent.click(within(card()).getByRole("tab", { name: "자료" }));
    expect(on()).toEqual([true, true, true, true]);
  });

  it("목록이 고정 높이(일곱 줄)를 넘으면 마지막 줄이 「외 N개」 로 접힌다 (E2E 7 · 6)", () => {
    const steps = Array.from({ length: 10 }, (_, index) => `단계 ${index + 1}`);
    render(<AxDraftCard onCommand={vi.fn()} source={source({ contract: contract({ checklist: steps }) })} />);
    fireEvent.click(within(card()).getByRole("tab", { name: "체크리스트" }));
    expect(rows()).toEqual(["=단계 1", "=단계 2", "=단계 3", "=단계 4", "=단계 5", "=단계 6", "=외 4개"]);
    // 항목마다 DS 의 장식용 체크 상자(☐)가 선다 — 누를 수 없다.
    const box = within(card()).getByRole("tabpanel").querySelector(".ax-draft-card__step-line input[type=checkbox]") as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(box.tabIndex).toBe(-1);
  });
});
