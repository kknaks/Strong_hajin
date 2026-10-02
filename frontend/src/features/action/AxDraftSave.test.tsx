import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (original) => ({
  ...(await original<typeof import("../../lib/api")>()),
  getTasks: vi.fn().mockResolvedValue([]),
  listProjects: vi.fn().mockResolvedValue([]),
  runActionCommand: vi.fn(),
  getActionItems: vi.fn(),
  stageActionMaterialLink: vi.fn(),
  stageActionMaterialFile: vi.fn(),
  discardActionMaterialDraft: vi.fn(),
}));

import * as api from "../../lib/api";
import type { ActionEditContract, ActionItemEnvelope } from "../../lib/viewModels";
import { CommandConfirmationForm } from "./CommandConfirmationForm";
import { ActionCommandButtons, withoutEditorOnlyCommands } from "./ActionPreview";
import { AxDraftCard, AxDraftModal, type AxDraftSource } from "./AxDraftCard";
import { CreateWorkModal } from "../work/WorkModals";

/**
 * AX 초안 「수정」 창 = **저장** (WORK-009 2a-1 · SPEC-002 §2.9 · §4 「초안 저장」).
 *
 * 창의 주 단추는 「저장」이고 `save_draft` 를 부른다 — 확정(confirm)이 아니다. 저장하면 카드는 서버의 새 회차를
 * 보이고 pending 그대로 [거절][수정][등록] 이 선다. 다음 「등록」은 그 새 회차로 간다.
 */

const contract = (values: Record<string, unknown> = {}, round = 2): ActionEditContract => ({
  editor: "task",
  base_submission_version: round,
  save_command: "save_draft",
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
    cc_member_ids: [],
    approver_id: null,
    ...values,
  },
  fields: [
    { id: "title", label: "제목", type: "text", required: true, editable: true },
    { id: "description", label: "내용", type: "textarea", required: false, editable: true },
  ],
});

const commands = [
  { id: "confirm", label: "등록", tone: "primary" },
  { id: "save_draft", label: "저장", tone: "neutral" },
  { id: "reject", label: "거절", tone: "neutral" },
];

const source = (over: Partial<AxDraftSource> = {}): AxDraftSource => ({
  actionId: "action-1",
  kind: "task",
  title: "KPI 설정",
  round: 2,
  state: "pending",
  contract: contract(),
  materials: [],
  commands,
  createdAt: null,
  ...over,
});

const envelope = (over: Partial<ActionItemEnvelope> = {}): ActionItemEnvelope => ({
  action_item_id: "action-1",
  kind: "ax.task.create_self",
  status: "awaiting_review",
  subject: "KPI 설정",
  operation_label: "업무 생성",
  current_question: "등록할까요?",
  preview: [],
  allowed_commands: commands,
  submission_version: 2,
  waiting_on: null,
  resource: { type: "action", id: "action-1" },
  expected_version: 5,
  edit_contract: contract(),
  created_at: null,
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

async function openEditor() {
  fireEvent.click(screen.getByRole("button", { name: "수정" }));
  return screen.findByRole("dialog", { name: "새 업무 추가" });
}

describe("수정 창의 주 단추 = 「저장」", () => {
  it("문구가 「저장」/「저장 중…」이고 「등록」은 창에 없다 — 닫기는 그대로", async () => {
    const onCommand = vi.fn(() => new Promise<void>(() => undefined));
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    const modal = await openEditor();
    expect(within(modal).queryByRole("button", { name: "등록" })).toBeNull();
    expect(within(modal).getAllByRole("button", { name: "닫기" }).length).toBeGreaterThan(0);
    const save = within(modal).getByRole("button", { name: "저장" });
    // 서랍 밖 창이라 앱 DS 의 주 단추 그대로다(파랑 — SPEC-002 §2.9 「범위 밖」).
    expect(save.className).toContain("scax-button--solid-primary");
    await act(async () => fireEvent.click(save));
    expect(within(modal).getByRole("button", { name: "저장 중…" })).toBeTruthy();
  });

  it("저장은 save_draft 하나만 부르고 confirm 을 부르지 않는다 — 고친 초안 전체와 지금 회차를 싣는다", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    const modal = await openEditor();
    fireEvent.change(within(modal).getByLabelText("업무 제목"), { target: { value: "KPI 설정 (고침)" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1));
    const [command, payload] = onCommand.mock.calls[0];
    expect(command).toBe("save_draft");
    expect(onCommand.mock.calls.some(([id]) => id === "confirm")).toBe(false);
    expect(payload.base_submission_version).toBe(2);
    expect(payload.draft).toMatchObject({ title: "KPI 설정 (고침)", due_date: "2026-10-06" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "새 업무 추가" })).toBeNull());
  });

  it("저장이 실패하면 문구가 창 안에 서고 고친 값이 남는다 — 낡음도 서버 문장 그대로", async () => {
    const onCommand = vi.fn().mockRejectedValue(new Error("base submission version is stale"));
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    const modal = await openEditor();
    fireEvent.change(within(modal).getByLabelText("업무 제목"), { target: { value: "남아야 할 고침" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));
    expect(await within(modal).findByText("base submission version is stale")).toBeTruthy();
    expect((within(modal).getByLabelText("업무 제목") as HTMLInputElement).value).toBe("남아야 할 고침");
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();
  });

  it("저장 명령이 열려 있지 않으면 [수정] 이 서지 않는다 — 남길 길이 없는 창을 열지 않는다", () => {
    render(<AxDraftCard onCommand={vi.fn()} source={source({ commands: commands.filter((command) => command.id !== "save_draft") })} />);
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
    expect(screen.getByRole("button", { name: "등록" })).toBeTruthy();
  });
});

describe("저장 뒤 카드 — 새 회차 값으로, 등록은 새 회차로", () => {
  it("채팅 카드: 다시 읽어 온 새 회차를 그리고, 「등록」은 draft 없이 그 회차로 확인한다", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<AxDraftCard onCommand={onCommand} source={source()} />);
    expect(screen.getByText("초안 · 2회차")).toBeTruthy();
    // 부르는 쪽(대화 재조회)이 새 회차를 건넨다.
    rerender(<AxDraftCard onCommand={onCommand} source={source({ title: "KPI 설정 (고침)", round: 3, contract: contract({ title: "KPI 설정 (고침)" }, 3) })} />);
    expect(screen.getByText("초안 · 3회차")).toBeTruthy();
    expect(screen.getByText("KPI 설정 (고침)")).toBeTruthy();
    for (const name of ["거절", "수정", "등록"]) expect(screen.getByRole("button", { name })).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "등록" })));
    expect(onCommand).toHaveBeenCalledWith("confirm", { base_submission_version: 3 });
  });

  it("홈·칩 카드 창(AxDraftModal): 저장 응답의 새 회차를 그리고 창은 남으며, 목록을 다시 읽고, 다음 등록이 새 회차로 간다", async () => {
    const saved = envelope({ subject: "KPI 설정 (고침)", submission_version: 3, edit_contract: contract({ title: "KPI 설정 (고침)" }, 3) });
    vi.mocked(api.runActionCommand).mockResolvedValueOnce(saved).mockResolvedValueOnce(saved);
    const onDone = vi.fn();
    const onClose = vi.fn();
    const onNotice = vi.fn();
    render(<AxDraftModal item={envelope()} onClose={onClose} onDone={onDone} onNotice={onNotice} />);
    const modal = await openEditor();
    fireEvent.change(within(modal).getByLabelText("업무 제목"), { target: { value: "KPI 설정 (고침)" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));

    await waitFor(() => expect(api.runActionCommand).toHaveBeenCalledTimes(1));
    const [id, command, payload] = vi.mocked(api.runActionCommand).mock.calls[0];
    expect([id, command]).toEqual(["action-1", "save_draft"]);
    expect(payload).toMatchObject({ expected_version: 5, base_submission_version: 2, draft: expect.objectContaining({ title: "KPI 설정 (고침)" }) });
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onNotice).toHaveBeenCalledWith("AX 초안을 저장했습니다.");
    expect(onClose).not.toHaveBeenCalled();
    const cardWindow = screen.getByRole("dialog", { name: "AX 제안" });
    expect(within(cardWindow).getByText("초안 · 3회차")).toBeTruthy();

    await act(async () => fireEvent.click(within(cardWindow).getByRole("button", { name: "등록" })));
    await waitFor(() => expect(api.runActionCommand).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.runActionCommand).mock.calls[1]).toEqual(["action-1", "confirm", { expected_version: 5, base_submission_version: 3 }]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

describe("채팅 경로 — save_draft 는 판단 원장 명령 입구로 간다", () => {
  it("decideAction 이 save_draft 를 /api/action-items/{id}/commands/save_draft 로 보낸다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const { decideAction } = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
    await decideAction("action-1", 5, "save_draft", { base_submission_version: 2, draft: { title: "x" } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/action-items/action-1/commands/save_draft");
    expect(JSON.parse(init.body)).toEqual({ expected_version: 5, base_submission_version: 2, draft: { title: "x" } });
  });
});

describe("범용 명령 렌더러는 save_draft 를 단추로 세우지 않는다", () => {
  it("결과 카드 명령 단추·필터 — 「저장」 단추가 없다", () => {
    expect(withoutEditorOnlyCommands(commands).map((command) => command.id)).toEqual(["confirm", "reject"]);
    render(<ActionCommandButtons commands={commands} onCommand={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "저장" })).toBeNull();
    expect(screen.getByRole("button", { name: "등록" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "거절" })).toBeTruthy();
  });

  it("명령 확인 폼 — 봉투에 save_draft 가 실려 와도 단추로 세우지 않는다", () => {
    const commandContract: ActionEditContract = { editor: "command", base_submission_version: 1, values: {}, fields: [] };
    render(<CommandConfirmationForm actionId="a-9" commands={commands} contract={commandContract} onCommand={vi.fn()} principalId="mina" />);
    expect(screen.queryByRole("button", { name: "저장" })).toBeNull();
  });
});

describe("다른 「새 업무 추가」 자리는 그대로", () => {
  it("axDraft 없이 열면 주 단추는 「업무 추가」·「업무 요청 보내기」이고 「저장」이 없다", () => {
    const base = { assigneeCandidates: [], onClose: vi.fn(), onCreated: vi.fn(), onError: vi.fn(), ownerName: "" };
    const { unmount } = render(<CreateWorkModal {...base} canCreateRequest={false} canCreateTask />);
    expect(screen.getByRole("button", { name: "업무 추가" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "저장" })).toBeNull();
    unmount();
    render(<CreateWorkModal {...base} canCreateRequest canCreateTask={false} initial={{ title: "다시 요청", supersedesRequestId: "r-1" }} />);
    expect(screen.getByRole("button", { name: "업무 요청 보내기" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "저장" })).toBeNull();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   fix1 — 검수 W-1 ~ W-4
   ════════════════════════════════════════════════════════════════════════════ */

describe("AxDraftModal — 부모가 다시 건네는 item (fix1 W-4)", () => {
  it("저장 뒤 부모가 옛 회차 item 을 새 객체로 다시 내려도 높은 회차를 지킨다", async () => {
    vi.mocked(api.runActionCommand).mockResolvedValueOnce(envelope({ submission_version: 3, edit_contract: contract({}, 3) }));
    const props = { onClose: vi.fn(), onDone: vi.fn() };
    const { rerender } = render(<AxDraftModal item={envelope()} {...props} />);
    const modal = await openEditor();
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));
    await waitFor(() => expect(screen.getByText("초안 · 3회차")).toBeTruthy());
    rerender(<AxDraftModal item={envelope()} {...props} />);
    expect(screen.getByText("초안 · 3회차")).toBeTruthy();
    expect(screen.queryByText("초안 · 2회차")).toBeNull();
  });

  it("부모가 더 높은 회차 item 을 내리면 그것을 따른다", () => {
    const props = { onClose: vi.fn(), onDone: vi.fn() };
    const { rerender } = render(<AxDraftModal item={envelope()} {...props} />);
    expect(screen.getByText("초안 · 2회차")).toBeTruthy();
    rerender(<AxDraftModal item={envelope({ submission_version: 4, edit_contract: contract({ title: "다른 탭에서 고침" }, 4) })} {...props} />);
    expect(screen.getByText("초안 · 4회차")).toBeTruthy();
  });
});

describe("W-1 낡은 저장 — 거부되고 최신 회차를 다시 읽는다", () => {
  it("홈·칩 창: 422 낡음이면 목록과 이 창의 봉투를 다시 읽고, 입력은 남으며, 다시 저장하면 새 기준으로 간다", async () => {
    vi.mocked(api.runActionCommand)
      .mockRejectedValueOnce(new api.ApiError(422, "base submission version is stale"))
      .mockResolvedValueOnce(envelope({ submission_version: 4, edit_contract: contract({ title: "내 고침" }, 4) }));
    vi.mocked(api.getActionItems).mockResolvedValue([envelope({ submission_version: 3, edit_contract: contract({ title: "다른 곳에서 저장" }, 3) })]);
    const onDone = vi.fn();
    render(<AxDraftModal item={envelope()} onClose={vi.fn()} onDone={onDone} />);
    const modal = await openEditor();
    fireEvent.change(within(modal).getByLabelText("업무 제목"), { target: { value: "내 고침" } });
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));

    expect(await within(modal).findByText("base submission version is stale")).toBeTruthy();
    expect(onDone).toHaveBeenCalled();
    expect(api.getActionItems).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("초안 · 3회차")).toBeTruthy());
    expect((within(modal).getByLabelText("업무 제목") as HTMLInputElement).value).toBe("내 고침");

    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));
    await waitFor(() => expect(api.runActionCommand).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.runActionCommand).mock.calls[1][2]).toMatchObject({ base_submission_version: 3, draft: expect.objectContaining({ title: "내 고침" }) });
  });
});

describe("W-2 저장 중에는 창이 닫히지 않고 카드도 잠긴다", () => {
  it("Esc·×·바깥 클릭이 창을 닫지 않고, 카드의 등록·거절·수정이 잠긴다", async () => {
    const onCommand = vi.fn(() => new Promise<void>(() => undefined));
    render(<AxDraftCard onCommand={onCommand} source={source()} />);
    const modal = await openEditor();
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));
    expect(within(modal).getByRole("button", { name: "저장 중…" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    for (const close of within(modal).getAllByRole("button", { name: "닫기" })) fireEvent.click(close);
    const backdrop = modal.closest(".scax-modal-backdrop, .modal-backdrop") ?? modal.parentElement!;
    fireEvent.click(backdrop);
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();

    for (const name of ["등록", "거절", "수정"]) {
      const button = within(screen.getByRole("region", { name: /AX 업무 생성/ })).getByRole("button", { name }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    }
  });

  it("일반 「새 업무 추가」는 지금처럼 Esc 로 닫힌다", () => {
    const onClose = vi.fn();
    render(<CreateWorkModal assigneeCandidates={[]} canCreateRequest={false} canCreateTask onClose={onClose} onCreated={vi.fn()} onError={vi.fn()} ownerName="" />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("W-3 저장 뒤 갱신 실패는 저장 실패가 아니다", () => {
  it("홈·칩 창: 목록 재조회가 실패해도 저장은 성공으로 알리고, 수정 창은 닫히며 카드는 새 회차다", async () => {
    vi.mocked(api.runActionCommand).mockResolvedValueOnce(envelope({ submission_version: 3, edit_contract: contract({}, 3) }));
    const onDone = vi.fn().mockRejectedValue(new Error("my-work unavailable"));
    const onNotice = vi.fn();
    render(<AxDraftModal item={envelope()} onClose={vi.fn()} onDone={onDone} onNotice={onNotice} />);
    const modal = await openEditor();
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));

    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("AX 초안을 저장했습니다."));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "새 업무 추가" })).toBeNull());
    expect(screen.queryByText("my-work unavailable")).toBeNull();
    expect(screen.queryByText("AX 초안을 저장하지 못했습니다.")).toBeNull();
    expect(screen.getByText("초안 · 3회차")).toBeTruthy();
  });
});

describe("add1 자료 초안 id — 저장과 등록이 같은 목록을 싣는다", () => {
  const material = (id: string) => ({
    material_draft_id: id, action_item_id: "action-1", source_kind: "external_link", name: id, content_type: "", size_bytes: 0,
    url: "https://example.test", integrity_ref: "x", state: "staged", expires_at: "", claimed_task_id: null,
  });

  it("목록 순서가 달라도 저장과 등록의 attachment_draft_ids 가 같다", async () => {
    const onCommand = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<AxDraftCard onCommand={onCommand} source={source({ materials: [material("md-b"), material("md-a")] as never })} />);
    const modal = await openEditor();
    await act(async () => fireEvent.click(within(modal).getByRole("button", { name: "저장" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1));
    const saved = onCommand.mock.calls[0][1].attachment_draft_ids;
    expect(saved).toEqual(["md-a", "md-b"]);

    // 다시 읽은 봉투가 다른 순서로 와도 「등록」은 같은 목록을 싣는다.
    rerender(<AxDraftCard onCommand={onCommand} source={source({ round: 3, contract: contract({}, 3), materials: [material("md-a"), material("md-b")] as never })} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "등록" })));
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(2));
    expect(onCommand.mock.calls[1]).toEqual(["confirm", { base_submission_version: 3, attachment_draft_ids: saved }]);
  });
});
