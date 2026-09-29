import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
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
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  createDirectTask: vi.fn(),
  submitTaskCompletion: vi.fn(),
  getTasks: vi.fn(),
  // 업무 상세가 프로젝트 «이름»을 이 목록에서 맞춘다 (SPEC-007 §2.4.4).
  listProjects: vi.fn().mockResolvedValue([]),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
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
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";

const parent: DirectTask = { task_id: "task-1", title: "분기 마감", state: "in_progress", version: 3, block_reason: null };

const child = {
  task_id: "task-2",
  title: "매출 집계",
  state: "in_progress" as const,
  due_date: "2026-10-10",
  assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
};

function renderDrawer(detail: Record<string, unknown>, onOpenTask = vi.fn()) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...parent, checklist: [], references: [], ...detail } as never);
  const onError = vi.fn();
  const onChanged = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={onError}
      onNotice={vi.fn()}
      onOpenTask={onOpenTask}
      onTransition={vi.fn()}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={parent}
    />,
  );
  return { onError, onChanged, onOpenTask };
}

describe("하위 업무", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("lists the parts of the work with who holds each and where it stands", async () => {
    const { onOpenTask } = renderDrawer({ children: [child], child_progress: { done: 0, total: 1 } });
    const section = await screen.findByLabelText("하위 업무");
    // 구획은 상세를 읽기 전에 선다 — 셈과 행은 그 «뒤에» 온다.
    // 셈은 **완결·막힘을 각각** 낸다 — `2 / 5 · 완료 막음 3` 이 시안의 모양이다 (§2.4.1 · `:186`).
    expect(await within(section).findByText("0 / 1 · 완료 막음 1")).toBeTruthy();
    const row = within(section).getByRole("listitem");
    expect(row.textContent).toContain("매출 집계");
    expect(row.textContent).toContain("진행 중");
    expect(row.textContent).toContain("지호");
    // 줄에 내는 것은 «상태 · 담당» 둘이다 — 34px 한 줄에 기한까지 넣으면 제목이 잘린다 (시안 `:190`).
    expect(row.textContent).not.toContain("2026/10/10");

    fireEvent.click(within(row).getByRole("button", { name: "매출 집계 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-2");

    // Steps and subtasks are different things, in different places.
    expect(screen.getByLabelText("체크리스트")).toBeTruthy();
    expect(within(screen.getByLabelText("체크리스트")).queryByRole("button", { name: "추가" })).toBeTruthy();
    /* 체크리스트의 「추가」는 «단계»를 더하고 하위 칸의 단추는 «업무»를 만든다 — 다른 일이라
       문구도 다르다(「하위 업무 생성」). 한 화면에서 같은 낱말이 두 뜻으로 읽히지 않는다. */
    expect(within(screen.getByLabelText("체크리스트")).queryByRole("button", { name: "하위 업무 생성" })).toBeNull();
  });

  /**
   * **입구가 하나다** (`design-items.md` 항목 #2 확정 설계 · WORK-007 F-2).
   *
   * 예전에는 머리에 단추 둘이 섰다 — 「직접 작업 추가」(인라인 입력칸)와 「하위 요청 보내기」(모달).
   * 같은 일이 **모양 둘**로 갈려 있었고 인라인 쪽은 제목 한 칸뿐이라 기한·담당을 못 적었다.
   * 이제 「추가」 하나가 생성 모달을 열고, 그 안의 세그먼트가 **내 업무 / 요청 업무**를 고른다.
   */
  it("머리의 「하위 업무 생성」 하나가 모달을 열고, 상위가 이 업무로 채워진다", async () => {
    vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([{ id: "jiho", display_name: "지호" }] as never);
    renderDrawer({ children: [], child_progress: { done: 0, total: 0 } });
    const section = await screen.findByLabelText("하위 업무");
    expect(await within(section).findByText(/하위 업무가 없습니다/)).toBeTruthy();
    // 인라인 입력칸은 사라졌다 — 그 자리가 모달이다.
    expect(within(section).queryByLabelText("하위 업무 제목")).toBeNull();
    expect(within(section).queryByRole("button", { name: "하위 요청 보내기" })).toBeNull();

    fireEvent.click(within(section).getByRole("button", { name: "하위 업무 생성" }));

    /*
     * **두 갈래가 다 열린다** — 예전에는 `parentTaskId` 하나로 요청 갈래에 잠겼다.
     * 부모가 중심 업무가 아니어서 본인 갈래가 막히는 자리는 **서버가 판정한다**(V-8).
     */
    const modal = await screen.findByRole("dialog", { name: "새 업무 추가" });
    const branches = within(modal).getByRole("tablist", { name: "생성 유형" });
    expect(within(branches).getByRole("tab", { name: "내 업무" })).toBeTruthy();
    expect(within(branches).getByRole("tab", { name: "요청 업무" })).toBeTruthy();
    // 상위는 여는 쪽이 정했으므로 그 안에서 바꾸지 못한다.
    fireEvent.click(within(modal).getByRole("tab", { name: "업무 연결" }));
    expect((within(modal).getByLabelText("상위 업무") as HTMLButtonElement).disabled).toBe(true);
  });

  /**
   * **WORK-002 v2 에서 뒤집힌 줄.** 예전에는 「부분의 부분은 없다」며 상위가 있는 업무에 하위 구획을
   * 아예 그리지 않았다. v2 는 **저장 깊이에 제한을 두지 않고**(V-6) 화면만 두 단계다(F-1 · L-11) —
   * 어느 업무를 열든 그 직속 하위가 보이고, 더 깊은 것은 그 하위로 들어가 읽는다.
   */
  it("shows a part what it belongs to, and still offers its own direct parts", async () => {
    const { onOpenTask } = renderDrawer({
      parent: { task_id: "task-0", title: "분기 마감", state: "in_progress" },
      children: [child],
      child_progress: { done: 0, blocking: 1, cancelled: 0, total: 1 },
    });
    const chip = await screen.findByLabelText("상위 업무");
    expect(chip.textContent).toContain("분기 마감");
    fireEvent.click(within(chip).getByRole("button", { name: "분기 마감 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-0");

    const section = screen.getByLabelText("하위 업무");
    expect(within(section).getByText("매출 집계")).toBeTruthy();
  });

  /** 하위를 남에게 맡기는 길은 **요청 입구로만** 간다 — `POST /api/tasks` 의 수평 갈래는 상위를 거절한다(O-27). */
  it("sends a subtask to someone else through the request entrance, carrying the parent", async () => {
    vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([{ id: "jiho", display_name: "지호 (팀장)" }] as never);
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "req-1", title: "매출 집계", state: "pending", version: 1 } as never);
    renderDrawer({ children: [], child_progress: { done: 0, blocking: 0, cancelled: 0, total: 0 } });
    const section = await screen.findByLabelText("하위 업무");

    fireEvent.click(within(section).getByRole("button", { name: "하위 업무 생성" }));
    const opened = await screen.findByRole("dialog", { name: "새 업무 추가" });
    // 같은 「추가」에서 갈래를 고른다 — 요청 갈래를 고르면 머리도 따라 바뀐다.
    fireEvent.click(within(within(opened).getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    const modal = await screen.findByRole("dialog", { name: "새 업무 요청" });
    fireEvent.change(within(modal).getByLabelText("요청할 업무"), { target: { value: "매출 집계" } });
    await waitFor(() => expect(within(modal).getByLabelText("담당 후보")).toBeTruthy());
    fireEvent.click(within(modal).getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(within(modal).getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() =>
      expect(api.createWorkRequest).toHaveBeenCalledWith(
        "매출 집계",
        "jiho",
        expect.objectContaining({ parent_task_id: "task-1" }),
        expect.any(String),
      ),
    );
  });
});
