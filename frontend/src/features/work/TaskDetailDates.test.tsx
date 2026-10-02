import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
 * 업무 상세 사실 줄의 **날짜 넷**과 출처 줄 간격 (SPEC-007 §2.2 · WORK-009 2b-1 · E2E-4 · E2E-5 ①).
 *
 * 순서는 시작 예정일 · 실제 시작일 · 실제 종료일 · 마감일이고, 값이 없는 칸은 서지 않는다.
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
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={vi.fn()}
      onClose={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      onTransition={vi.fn().mockResolvedValue(true)}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
      {...props}
    />,
  );
}

/** 사실 줄의 칸들 — 「이름 값」 한 칸씩. */
async function facts(): Promise<string[]> {
  const meta = await screen.findByLabelText("업무 메타");
  return [...meta.querySelectorAll(".meta__facts > span")].map((span) => span.textContent ?? "");
}

beforeEach(() => {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([] as never);
  vi.mocked(api.listProjects).mockResolvedValue([] as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("사실 줄의 날짜 넷", () => {
  it("시작 전 업무 — 시작 예정일과 마감일만 서고 실제 두 값은 서지 않는다", async () => {
    const task = detail({ start_date: "2026-10-01", due_date: "2026-10-06" });
    renderDrawer(task);
    await waitFor(async () => expect(await facts()).toEqual(["담당 민아", "시작 예정일 2026/10/01", "마감일 2026/10/06"]));
  });

  it("진행 중 업무 — 실제 시작일이 시작 예정일 뒤, 마감일 앞에 선다", async () => {
    const task = detail({ state: "in_progress", start_date: "2026-10-01", due_date: "2026-10-06", started_at: "2026-10-02T01:00:00Z" });
    renderDrawer(task);
    await waitFor(async () =>
      expect(await facts()).toEqual(["담당 민아", "시작 예정일 2026/10/01", "실제 시작일 2026/10/02", "마감일 2026/10/06"]),
    );
  });

  it("완료 업무 — 넷이 시작 예정일 → 실제 시작일 → 실제 종료일 → 마감일 순이다", async () => {
    const task = detail({
      state: "done",
      start_date: "2026-10-01",
      due_date: "2026-10-06",
      started_at: "2026-10-02T01:00:00Z",
      completed_at: "2026-10-04T08:00:00Z",
    });
    renderDrawer(task);
    await waitFor(async () =>
      expect(await facts()).toEqual([
        "담당 민아",
        "시작 예정일 2026/10/01",
        "실제 시작일 2026/10/02",
        "실제 종료일 2026/10/04",
        "마감일 2026/10/06",
      ]),
    );
  });

  it("실제 두 값은 서울 날짜다 — UTC 15:00 이후 시각은 다음 날로 선다", async () => {
    const task = detail({ state: "done", started_at: "2026-10-05T15:30:00Z", completed_at: "2026-10-06T14:59:00Z" });
    renderDrawer(task);
    await waitFor(async () =>
      expect(await facts()).toEqual(["담당 민아", "실제 시작일 2026/10/06", "실제 종료일 2026/10/06"]),
    );
  });

  it("「편집」 — 시작 예정일·마감일이 입력칸이 되고 실제 두 값은 글자로 남는다", async () => {
    const task = detail({ state: "in_progress", start_date: "2026-10-01", due_date: "2026-10-06", started_at: "2026-10-02T01:00:00Z" });
    renderDrawer(task);
    fireEvent.click(await screen.findByRole("button", { name: "편집" }));
    await waitFor(async () => expect(await facts()).toEqual(["담당 민아", "실제 시작일 2026/10/02"]));
    const meta = screen.getByLabelText("업무 메타");
    const labels = [...meta.querySelectorAll(".meta__edit .date-field > label")].map((label) => label.textContent);
    expect(labels).toEqual(["시작 예정일", "마감일"]);
    // 입력칸에 보이는 날짜도 같은 형식이다 (SPEC-001 U-17 · OQ-Q ③).
    expect(meta.querySelector(".meta__edit")!.textContent).toContain("2026/10/06");
  });
});

describe("출처 줄", () => {
  /**
   * 간격 자체는 CSS(`.scax-td .meta__facts + .origin-chip`)가 준다 — jsdom 은 CSS 를 적용하지 않으므로
   * 그 선택자가 닿는 **구조**(사실 줄 바로 다음 형제)를 센다.
   */
  it("사실 줄 바로 아래 형제로 서고, 링크 문구는 원 AX 초안의 제목 그대로다", async () => {
    const task = detail({
      due_date: "2026-10-06",
      origin: { kind: "self_created", actor_role: null, actor: null, source: { type: "action_item", id: "ai-1", title: "분기 보고서" } },
    });
    renderDrawer(task, { onOpenSource: vi.fn() });
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.previousElementSibling?.classList.contains("meta__facts")).toBe(true);
    expect(chip.textContent).toContain("AX 제안에서 생성됨");
    expect(screen.getByRole("button", { name: "분기 보고서" })).toBeTruthy();
  });
});
