import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  getTask: vi.fn(),
  getTasks: vi.fn(),
  listProjects: vi.fn(),
  updateTask: vi.fn(),
  releaseSuccessor: vi.fn(),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  getTaskMaterials: vi.fn(),
  getTaskAssignments: vi.fn(),
  getTaskProposals: vi.fn(),
  createTaskProposal: vi.fn(),
  respondTaskProposal: vi.fn(),
  withdrawTaskProposal: vi.fn(),
  reopenTask: vi.fn(),
  getTaskChildren: vi.fn(),
  withdrawWorkRequest: vi.fn(),
  hideWorkRequestListEntry: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  attachTaskMaterialLink: vi.fn(),
  attachTaskMaterialReference: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  reassignTask: vi.fn(),
  taskMaterialContentUrl: () => "",
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  amendWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  submitTaskCompletion: vi.fn(),
  getActionItems: vi.fn(),
  runActionCommand: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";

/**
 * 업무 상세의 **메타 정보 격자**와 **인라인 즉시 저장** (SPEC-007 §2.10.1 · §2.10.2 · §2.10.4 · §2.10.7 · §2.10.8 · WORK-010 2a).
 *
 * 행은 진행 상태 · 버전 · 담당 · 시작 예정일 · 실제 시작일 · 실제 종료일 · 마감일 · 결재 · 참조 · 출처 순이다(날짜 넷은 E2E-5).
 * 값이 없는 읽기 전용 행은 서지 않고, 고칠 수 있는 화면의 시작 예정일·마감일은 비어 있어도 선다.
 * 실제 두 값은 시각이라 **서울 날짜**로 낸다 — UTC 로 자르면 서울 자정~09시 값이 하루 밀린다.
 */

const base: DirectTask = {
  task_id: "task-d",
  title: "분기 보고서",
  state: "open",
  version: 1,
  block_reason: null,
  description: null,
} as unknown as DirectTask;

const detail = (extra: Record<string, unknown> = {}) =>
  ({
    ...base,
    checklist: [],
    references: [],
    children: [],
    predecessors: [],
    successors: [],
    hidden_successor_count: 0,
    project_id: null,
    ...extra,
  }) as unknown as DirectTask;

function renderDrawer(task: DirectTask, props: Record<string, unknown> = {}) {
  vi.mocked(api.getTask).mockResolvedValue(task as never);
  const onUpdate = vi.fn();
  const onError = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={vi.fn()}
      onClose={vi.fn()}
      onError={onError}
      onNotice={vi.fn()}
      onTransition={vi.fn().mockResolvedValue(true)}
      onUpdate={onUpdate}
      ownerName="민아"
      task={task}
      {...props}
    />,
  );
  return { onUpdate: (props.onUpdate as typeof onUpdate | undefined) ?? onUpdate, onError };
}

/** 메타 정보 격자의 행 — 「라벨 값」 한 줄씩 (읽기 전용 화면에서 쓴다 — 입력칸의 숨은 라벨이 끼지 않는다). */
async function rows(): Promise<string[]> {
  const meta = await screen.findByLabelText("메타 정보");
  return [...meta.querySelectorAll(".meta-info > div")].map(
    (row) => `${row.querySelector("dt")?.textContent ?? ""} ${row.querySelector("dd")?.textContent ?? ""}`,
  );
}
/** 행 이름만. */
async function labels(): Promise<string[]> {
  const meta = await screen.findByLabelText("메타 정보");
  return [...meta.querySelectorAll(".meta-info dt")].map((dt) => dt.textContent ?? "");
}
/** 이름으로 한 행. */
function row(name: string): HTMLElement {
  const meta = screen.getByLabelText("메타 정보");
  const found = [...meta.querySelectorAll(".meta-info > div")].find((div) => div.querySelector("dt")?.textContent === name);
  if (!found) throw new Error(`no row ${name}`);
  return found as HTMLElement;
}
/** 앱 달력에서 날짜 하나를 고른다 — 보이는 달 안의 날짜만 고른다(날짜 의존 실패를 피한다). */
function pick(label: string, iso: string) {
  fireEvent.click(screen.getByRole("button", { name: `${label} 달력 열기` }));
  fireEvent.click(screen.getByRole("group", { name: label }).querySelector(`[data-date="${iso}"]`) as HTMLElement);
}
/** 제목 칸에 쓰고 Enter. */
function retitle(text: string) {
  const slot = screen.getByLabelText("업무 제목");
  fireEvent.click(slot);
  slot.textContent = text;
  fireEvent.keyDown(slot, { key: "Enter" });
}
const savedAs = (version: number, extra: Record<string, unknown> = {}) => ({ ...detail(extra), version }) as unknown as DirectTask;

beforeEach(() => {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([] as never);
  vi.mocked(api.listProjects).mockResolvedValue([] as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("헤더 (SPEC-007 §2.10.1)", () => {
  it("제목 · × 만 선다 — 머리글·상태·버전·「편집」·「AX」가 없다", async () => {
    renderDrawer(detail({ due_date: "2020-01-01" }));
    const dialog = await screen.findByRole("dialog", { name: "업무 상세" });
    const head = dialog.querySelector(".scax-modal__head") as HTMLElement;
    expect(head.querySelector(".modal-kicker")).toBeNull();
    expect(head.querySelector(".scax-chip-row")).toBeNull();
    expect(head.querySelector(".status")).toBeNull();
    expect(head.textContent).not.toContain("v1");
    expect(head.textContent).not.toContain("마감일 초과");
    expect(within(head).queryByRole("button", { name: "편집" })).toBeNull();
    expect(screen.queryByRole("button", { name: /AX에게 이 업무 묻기/ })).toBeNull();
    expect(within(head).getByLabelText("업무 제목").textContent).toBe("분기 보고서");
    expect(within(head).getByRole("button", { name: "상세 닫기" })).toBeTruthy();
    // 제목은 한 번만 — 본문에 같은 제목이 또 서지 않는다
    expect(screen.getAllByText("분기 보고서")).toHaveLength(1);
  });

  it("업무 상세 전용 modifier 가 붙는다 — 공용 모달 규칙은 그대로이고 간격은 이 화면 스코프에서만 준다(R6)", async () => {
    renderDrawer(detail());
    const dialog = await screen.findByRole("dialog", { name: "업무 상세" });
    expect(dialog.classList.contains("scax-modal")).toBe(true);
    expect(dialog.classList.contains("scax-modal--task-detail")).toBe(true);
  });

  it("고칠 수 없는 입구에서는 제목이 글자뿐이다 — 눌러도 열리지 않는다", async () => {
    renderDrawer(detail(), { canManage: false });
    const dialog = await screen.findByRole("dialog", { name: "업무 상세" });
    expect(screen.queryByLabelText("업무 제목")).toBeNull();
    fireEvent.click(within(dialog).getByText("분기 보고서"));
    expect(document.querySelector("[contenteditable='true']")).toBeNull();
  });
});

describe("메타 정보 격자 (SPEC-007 §2.10.2)", () => {
  it("행 순서 — 진행 상태 · 버전 · 담당 · 시작 예정일 · 실제 시작일 · 실제 종료일 · 마감일 (완료 업무, 읽기)", async () => {
    const task = detail({
      state: "done",
      version: 4,
      start_date: "2026-10-01",
      due_date: "2026-10-06",
      started_at: "2026-10-02T01:00:00Z",
      completed_at: "2026-10-04T08:00:00Z",
    });
    renderDrawer(task, { canManage: false });
    await waitFor(async () =>
      expect(await rows()).toEqual([
        "진행 상태 완료",
        "버전 v4",
        "담당 민아",
        "시작 예정일 2026/10/01",
        "실제 시작일 2026/10/02",
        "실제 종료일 2026/10/04",
        "마감일 2026/10/06",
      ]),
    );
  });

  it("시작 전 업무 — 실제 두 값의 행은 서지 않는다", async () => {
    renderDrawer(detail({ start_date: "2026-10-01", due_date: "2026-10-06" }), { canManage: false });
    await waitFor(async () =>
      expect(await rows()).toEqual(["진행 상태 시작 전", "버전 v1", "담당 민아", "시작 예정일 2026/10/01", "마감일 2026/10/06"]),
    );
  });

  it("실제 두 값은 서울 날짜다 — UTC 15:00 이후 시각은 다음 날로 선다", async () => {
    renderDrawer(detail({ state: "done", started_at: "2026-10-05T15:30:00Z", completed_at: "2026-10-06T14:59:00Z" }), { canManage: false });
    await waitFor(async () =>
      expect(await rows()).toEqual(["진행 상태 완료", "버전 v1", "담당 민아", "실제 시작일 2026/10/06", "실제 종료일 2026/10/06"]),
    );
  });

  it("고칠 수 있는 화면에서는 비어 있는 시작 예정일·마감일도 행이 서고 날짜 칸이다 — 실제 두 값은 입력이 없다", async () => {
    renderDrawer(detail({ state: "in_progress", started_at: "2026-10-02T01:00:00Z" }));
    await waitFor(async () => expect(await labels()).toEqual(["진행 상태", "버전", "담당", "시작 예정일", "실제 시작일", "마감일"]));
    expect(within(row("시작 예정일")).getByRole("button", { name: "시작 예정일 달력 열기" })).toBeTruthy();
    expect(within(row("마감일")).getByRole("button", { name: "마감일 달력 열기" })).toBeTruthy();
    // 진행 상태는 셀렉트다(2b) — 갈 곳이 있는 업무라서. 나머지 읽기 행은 입력이 없다.
    expect(within(row("진행 상태")).getByRole("button", { name: "진행 상태 바꾸기" })).toBeTruthy();
    for (const name of ["버전", "담당", "실제 시작일"]) {
      expect(within(row(name)).queryByRole("button")).toBeNull();
      expect(row(name).querySelector("input, textarea, [contenteditable='true']")).toBeNull();
    }
  });

  it("결재 · 참조 · 출처는 마감일 뒤에 그 순서로 선다", async () => {
    renderDrawer(
      detail({
        due_date: "2026-10-06",
        approver_id: "jiho",
        cc_member_ids: ["yuna"],
        origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "jiho", display_name: "지호 (팀장)" }, source: null },
      }),
      {
        canManage: false,
        personas: [
          { id: "jiho", display_name: "지호 (팀장)" },
          { id: "yuna", display_name: "유나 (대표)" },
        ],
      },
    );
    await waitFor(async () =>
      expect(await rows()).toEqual([
        "진행 상태 시작 전",
        "버전 v1",
        "담당 민아",
        "마감일 2026/10/06",
        "결재 지호",
        "참조 유나",
        "출처 지호가 보낸 업무",
      ]),
    );
  });

  it("마감이 지났으면 「마감일 초과」 배지가 마감일 값 옆에 선다 — 머리에는 없다", async () => {
    renderDrawer(detail({ due_date: "2020-01-01" }));
    await screen.findByLabelText("메타 정보");
    const badge = within(row("마감일")).getByText("마감일 초과");
    expect(badge.closest(".meta-info__value")).toBeTruthy();
    expect(screen.getAllByText("마감일 초과")).toHaveLength(1);
  });
});

describe("출처 행 (SPEC-007 §2.10.8)", () => {
  it("AX 제안 — 「AX 제안 · 판단 보기」이고 링크 글자는 업무 제목이 아니다", async () => {
    const onOpenSource = vi.fn();
    renderDrawer(
      detail({ origin: { kind: "self_created", actor_role: null, actor: null, source: { type: "action_item", id: "ai-1", title: "분기 보고서" } } }),
      { onOpenSource },
    );
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.textContent).toBe("AX 제안·판단 보기");
    fireEvent.click(within(chip).getByRole("button", { name: "판단 보기" }));
    expect(onOpenSource).toHaveBeenCalledWith({ type: "action_item", id: "ai-1", title: "분기 보고서" });
    expect(within(chip).queryByRole("button", { name: "분기 보고서" })).toBeNull();
  });

  it("판단 상세를 열 수 없는 화면(홈·캘린더)은 「AX 제안」 글자만이다", async () => {
    renderDrawer(detail({ origin: { kind: "self_created", actor_role: null, actor: null, source: { type: "action_item", id: "ai-1", title: "분기 보고서" } } }));
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.textContent).toBe("AX 제안");
    expect(within(chip).queryByRole("button")).toBeNull();
  });

  it("출처가 없으면 행이 서지 않는다", async () => {
    renderDrawer(detail({ origin: null }), { canManage: false });
    await waitFor(async () => expect(await labels()).toEqual(["진행 상태", "버전", "담당"]));
    expect(screen.queryByLabelText("업무 출처")).toBeNull();
  });
});

describe("인라인 즉시 저장 (SPEC-007 §2.10.4)", () => {
  it("「편집」·「편집 끝내기」·「변경 저장」이 없다", async () => {
    renderDrawer(detail());
    await screen.findByLabelText("메타 정보");
    for (const name of ["편집", "편집 끝내기", "변경 저장"]) expect(screen.queryByRole("button", { name })).toBeNull();
  });

  it("날짜는 고르는 즉시 그 한 칸만 저장한다", async () => {
    const { onUpdate } = renderDrawer(detail({ start_date: "2026-10-01", due_date: "2026-10-20" }));
    vi.mocked(onUpdate).mockResolvedValue(savedAs(2, { start_date: "2026-10-01", due_date: "2026-10-22" }));
    await screen.findByLabelText("메타 정보");
    pick("마감일", "2026-10-22");
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(onUpdate).mock.calls[0][1]).toEqual({ due_date: "2026-10-22" });
    expect(vi.mocked(onUpdate).mock.calls[0][0].version).toBe(1);
    await waitFor(() => expect(within(row("버전")).getByText("v2")).toBeTruthy());
  });

  it("같은 날짜를 다시 고르면 요청이 나가지 않는다", async () => {
    const { onUpdate } = renderDrawer(detail({ due_date: "2026-10-20" }));
    await screen.findByLabelText("메타 정보");
    pick("마감일", "2026-10-20");
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("시작 예정일이 마감일보다 늦어지는 선택은 보내지 않고, 원래 값으로 두고 칸 옆에 문장을 낸다", async () => {
    const { onUpdate } = renderDrawer(detail({ start_date: "2026-10-01", due_date: "2026-10-10" }));
    await screen.findByLabelText("메타 정보");
    pick("시작 예정일", "2026-10-15");
    expect(onUpdate).not.toHaveBeenCalled();
    expect(within(row("시작 예정일")).getByRole("alert").textContent).toBe("시작 예정일은 마감일보다 늦을 수 없습니다.");
    expect(within(row("시작 예정일")).getByRole("button", { name: "시작 예정일 달력 열기" }).textContent).toContain("2026/10/01");
  });

  it("두 칸을 연달아 고치면 하나씩 나가고, 뒤 요청이 앞 응답의 version 을 싣는다", async () => {
    let finishFirst: (task: DirectTask) => void = () => undefined;
    const onUpdate = vi.fn()
      .mockImplementationOnce(() => new Promise<DirectTask>((resolve) => { finishFirst = resolve; }))
      .mockResolvedValueOnce(savedAs(3, { title: "새 제목", due_date: "2026-10-22" }));
    renderDrawer(detail({ due_date: "2026-10-20" }), { onUpdate });
    await screen.findByLabelText("메타 정보");
    retitle("새 제목");
    pick("마감일", "2026-10-22");
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(1));
    // 앞 저장이 끝나기 전에는 다음 저장이 나가지 않는다
    expect(onUpdate).toHaveBeenCalledTimes(1);
    finishFirst(savedAs(2, { title: "새 제목", due_date: "2026-10-20" }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledTimes(2));
    expect(vi.mocked(onUpdate).mock.calls[0][0].version).toBe(1);
    expect(vi.mocked(onUpdate).mock.calls[0][1]).toEqual({ title: "새 제목" });
    expect(vi.mocked(onUpdate).mock.calls[1][0].version).toBe(2);
    expect(vi.mocked(onUpdate).mock.calls[1][1]).toEqual({ due_date: "2026-10-22" });
  });

  it("422 stale 이면 다시 보내지 않고 상세를 다시 읽어 서버 값으로 서고, 칸 옆에 다시 불렀다고 말한다", async () => {
    const onUpdate = vi.fn().mockRejectedValue(Object.assign(new Error("task version is stale"), { status: 422 }));
    renderDrawer(detail({ due_date: "2026-10-20" }), { onUpdate });
    await screen.findByLabelText("메타 정보");
    // 다른 곳에서 이미 바뀌었다 — 다시 읽으면 서버 값(10/25, v5)이 온다
    vi.mocked(api.getTask).mockResolvedValue(detail({ due_date: "2026-10-25", version: 5 }) as never);
    const reads = vi.mocked(api.getTask).mock.calls.length;
    pick("마감일", "2026-10-22");
    await waitFor(() => expect(within(row("마감일")).getByRole("alert").textContent).toBe("다른 곳에서 바뀌어 최신 값으로 다시 불렀습니다."));
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.getTask).mock.calls.length).toBeGreaterThan(reads);
    expect(within(row("마감일")).getByRole("button", { name: "마감일 달력 열기" }).textContent).toContain("2026/10/25");
    expect(within(row("버전")).getByText("v5")).toBeTruthy();
  });

  it("그 밖의 실패는 원래 값으로 돌아가고 칸 옆에 서버 문장이 선다", async () => {
    const onUpdate = vi.fn().mockRejectedValue(new Error("마감일 형식이 올바르지 않습니다"));
    renderDrawer(detail({ due_date: "2026-10-20" }), { onUpdate });
    await screen.findByLabelText("메타 정보");
    pick("마감일", "2026-10-22");
    await waitFor(() => expect(within(row("마감일")).getByRole("alert").textContent).toBe("마감일 형식이 올바르지 않습니다"));
    expect(within(row("마감일")).getByRole("button", { name: "마감일 달력 열기" }).textContent).toContain("2026/10/20");
  });

  it("업무 내용은 blur 에 저장하고 Enter 는 줄바꿈이다 — 안 바뀌면 보내지 않고, Esc 는 취소다", async () => {
    const { onUpdate } = renderDrawer(detail({ description: "첫 줄" }));
    vi.mocked(onUpdate).mockResolvedValue(savedAs(2, { description: "첫 줄\n둘째 줄" }));
    await screen.findByLabelText("메타 정보");
    // 안 바뀜 — 요청 없음
    fireEvent.click(screen.getByRole("button", { name: "업무 내용 고치기" }));
    fireEvent.blur(screen.getByRole("textbox", { name: "업무 내용 고치기" }));
    expect(onUpdate).not.toHaveBeenCalled();
    // Esc — 취소, 요청 없음, 모달도 닫히지 않는다
    fireEvent.click(screen.getByRole("button", { name: "업무 내용 고치기" }));
    let area = screen.getByRole("textbox", { name: "업무 내용 고치기" });
    fireEvent.change(area, { target: { value: "버릴 내용" } });
    fireEvent.keyDown(area, { key: "Escape" });
    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "업무 상세" })).toBeTruthy();
    // Enter 는 저장이 아니다 — blur 가 저장한다
    fireEvent.click(screen.getByRole("button", { name: "업무 내용 고치기" }));
    area = screen.getByRole("textbox", { name: "업무 내용 고치기" });
    fireEvent.change(area, { target: { value: "첫 줄\n둘째 줄" } });
    fireEvent.keyDown(area, { key: "Enter" });
    expect(onUpdate).not.toHaveBeenCalled();
    fireEvent.blur(area);
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ version: 1 }), { description: "첫 줄\n둘째 줄" }));
  });

  it("제목 — 빈 값과 안 바뀐 값은 보내지 않고, Esc 는 취소이며 모달을 닫지 않는다", async () => {
    const onClose = vi.fn();
    const { onUpdate } = renderDrawer(detail(), { onClose });
    await screen.findByLabelText("메타 정보");
    retitle("   ");
    retitle("분기 보고서");
    const slot = screen.getByLabelText("업무 제목");
    fireEvent.click(slot);
    slot.textContent = "버릴 제목";
    fireEvent.keyDown(slot, { key: "Escape" });
    expect(onUpdate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(slot.textContent).toBe("분기 보고서");
  });

  it("제목 저장이 거절되면 원래 제목으로 돌아가고 칸 옆에 문장이 선다", async () => {
    const onUpdate = vi.fn().mockRejectedValue(new Error("업무 제목이 너무 깁니다"));
    renderDrawer(detail(), { onUpdate });
    await screen.findByLabelText("메타 정보");
    retitle("거절될 제목");
    await waitFor(() => expect(screen.getByLabelText("업무 제목").textContent).toBe("분기 보고서"));
    expect(screen.getByText("업무 제목이 너무 깁니다")).toBeTruthy();
  });
});

describe("읽기 전용 입구 (SPEC-007 §2.10.7)", () => {
  for (const [name, props, extra] of [
    ["관리 불가로 연 상세", { canManage: false }, {}],
    ["서버가 read_only 를 준 상세", {}, { access: "read_only" }],
    ["취소된 업무", {}, { state: "cancelled" }],
  ] as const) {
    it(`${name} — 제목·내용·날짜가 글자뿐이고 빈 날짜 행은 서지 않는다`, async () => {
      const { onUpdate } = renderDrawer(detail({ description: "적어 둔 내용", ...extra }), props);
      await screen.findByLabelText("메타 정보");
      expect(screen.queryByLabelText("업무 제목")).toBeNull();
      expect(screen.queryByRole("button", { name: "업무 내용 고치기" })).toBeNull();
      expect(screen.queryByRole("button", { name: /달력 열기/ })).toBeNull();
      expect(await labels()).not.toContain("시작 예정일");
      expect(await labels()).not.toContain("마감일");
      fireEvent.click(screen.getByText("적어 둔 내용"));
      expect(document.querySelector("textarea")).toBeNull();
      expect(onUpdate).not.toHaveBeenCalled();
    });
  }
});
