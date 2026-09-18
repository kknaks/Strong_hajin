import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  getTask: vi.fn(),
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
  createDirectTask: vi.fn(),
  submitTaskCompletion: vi.fn(),
  getTasks: vi.fn(),
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
  listProjects: vi.fn(),
}));

import * as api from "../../lib/api";
import { TaskDetailDrawer } from "./WorkModals";

/**
 * v2 가 «상세 화면» 에 새로 여는 자리들 (WORK-002 Phase 7-C · SPEC-003 §2.4 · U-7·U-8·U-9).
 *
 * 시안에 업무 상세가 **아예 없어서** 이 자리들은 전부 미설계였다. 그래서 한 줄씩 회귀로 못박는다 —
 * 「그릴 자리가 없었다」가 「그 계약이 없다」로 굳는 것이 이 work 가 막으려는 것이다.
 */

const requestTask: DirectTask = {
  task_id: "task-1",
  title: "디자인 시안",
  state: "in_progress",
  version: 4,
  block_reason: null,
  due_date: "2026-09-20",
  description: "첫 안을 만든다",
  assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
  origin: { kind: "work_request", actor_role: "requester", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: null },
  derived: { assignment: null, approval: null, proposal: null, blocking_children: [], overdue_days: null },
};

function renderDrawer(task: DirectTask, extras: Record<string, unknown> = {}, props: Record<string, unknown> = {}) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [], references: [], ...extras } as never);
  const onError = vi.fn();
  const onNotice = vi.fn();
  const onTransition = vi.fn();
  const onChanged = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={onError}
      onNotice={onNotice}
      onOpenTask={vi.fn()}
      onTransition={onTransition}
      onUpdate={vi.fn()}
      ownerName="지호"
      personaId="jiho"
      personas={[
        { id: "mina", display_name: "민아 (구성원)" },
        { id: "jiho", display_name: "지호 (팀장)" },
        { id: "sora", display_name: "소라 (법무)" },
      ]}
      task={task}
      {...props}
    />,
  );
  return { onError, onNotice, onTransition, onChanged };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("담당 변경 대기 (U-8 · V-18)", () => {
  it("기존 담당과 새 제안을 각각 읽는다 — 중간에 담당 없는 구간이 화면에 생기지 않는다", async () => {
    vi.mocked(api.getTaskAssignments).mockResolvedValue({
      task_id: "task-1",
      current: { assignment_id: "a1", assignment_kind: "request_effect", status: "active", assignee_id: "jiho", assigned_by: "mina", decline_reason: null, created_at: "", accepted_at: "", declined_at: null },
      pending: { assignment_id: "a2", assignment_kind: "direct", status: "pending", assignee_id: "sora", assigned_by: "mina", decline_reason: null, created_at: "", accepted_at: null, declined_at: null },
      history: [],
    } as never);
    renderDrawer(requestTask);

    const section = await screen.findByLabelText("담당 변경 대기");
    expect(section.textContent).toContain("지호");
    expect(section.textContent).toContain("소라");
  });

  /** 이 조회를 아직 내지 않는 서버에서는 **「대기 없음」으로 단정하지 않는다** — 구획 자체를 그리지 않는다. */
  it("담당 조회가 실패하면 그 구획을 그리지 않는다", async () => {
    vi.mocked(api.getTaskAssignments).mockRejectedValue(new Error("404"));
    renderDrawer(requestTask);
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByLabelText("담당 변경 대기")).toBeNull();
  });
});

describe("완료를 막는 하위 (U-7 · I-7)", () => {
  it("막는 하위를 이름으로 보여 주고, 왜 막는지 가른다", async () => {
    renderDrawer({
      ...requestTask,
      derived: {
        ...requestTask.derived,
        blocking_children: [
          { task_id: "c1", title: "자료 수집", why: "unfinished" },
          { task_id: "c2", title: "법무 검토", why: "awaiting_approval" },
        ],
      },
    });
    const section = await screen.findByLabelText("완료를 막는 하위");
    expect(within(section).getByRole("button", { name: "자료 수집 열기" })).toBeTruthy();
    expect(section.textContent).toContain("아직 끝나지 않음");
    expect(section.textContent).toContain("요청자 확인 대기");
  });

  /**
   * **보이는 하위가 0 이어도 완료 단추를 지우지 않는다.** 서버의 게이트는 내가 못 읽는 하위까지 세므로,
   * 화면이 `blocking=0` 을 「완료해도 된다」로 읽으면 없는 허가를 스스로 발급하는 것이 된다.
   */
  it("막는 하위가 하나도 안 보여도 완료 명령을 화면이 막지 않는다", async () => {
    renderDrawer({ ...requestTask, derived: { ...requestTask.derived, blocking_children: [] } });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByLabelText("완료를 막는 하위")).toBeNull();
    expect(screen.getByRole("button", { name: "완료 보고" })).toBeTruthy();
  });
});

describe("재개는 봉투와 자리가 함께 정한다 (검수 R-1 · F-1)", () => {
  it("승인 대기 중인 done 에는 재개가 서지 않는다 — 지금은 상대의 차례다", async () => {
    renderDrawer(
      { ...requestTask, state: "done", derived: { ...requestTask.derived, approval: "awaiting_review" } },
      {},
      { viewerIsRequester: true, viewerIsRecordRequester: true },
    );
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "재개" })).toBeNull();
  });

  /** 요청 업무를 다시 열 수 있는 사람은 **요청자**다 (`_require_may_reopen`). */
  it("승인이 끝난 done 에서 요청자는 재개를 부를 수 있고, 그 명령은 reopen 이다", async () => {
    vi.mocked(api.reopenTask).mockResolvedValue({ ...requestTask, state: "in_progress", version: 5 } as never);
    renderDrawer(
      { ...requestTask, state: "done", derived: { ...requestTask.derived, approval: "approved" } },
      {},
      { viewerIsRequester: true, viewerIsRecordRequester: true },
    );
    await screen.findByLabelText("업무 상세");

    fireEvent.click(screen.getByRole("button", { name: "재개" }));
    const prompt = await screen.findByRole("dialog", { name: "재개 사유" });
    // 사유는 선택이다 — 빈 채로도 보낼 수 있다.
    fireEvent.click(within(prompt).getByRole("button", { name: "재개" }));

    await waitFor(() => expect(api.reopenTask).toHaveBeenCalledWith("task-1", 4, undefined));
  });

  it("같은 업무에서 담당자에게는 재개가 서지 않는다 — 눌러도 403 인 자리다", async () => {
    renderDrawer({ ...requestTask, state: "done", derived: { ...requestTask.derived, approval: "approved" } });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "재개" })).toBeNull();
  });

  /**
   * **재검수 N-1.** 서버가 제안과 재개를 다른 함수로 판정한다 —
   * 제안은 `_is_request_owner`(`requester_id` 또는 `promoted_by_member_id`), 재개는
   * `_requester_of`(**`requester_id` 하나**)다. 회의 승격 요청은 `requester_id` 가
   * `system:meeting` 이라 **누른 사람도 재개할 수 없다.** 두 값을 묶어 그리면 그 사람에게
   * 늘 403 인 단추가 선다.
   *
   * 이 회귀는 **OQ-206(승격 요청의 확인자·재개자)을 정하지 않는다** — 지금 서버가 여는 만큼만 그린다.
   */
  it("승격 요청의 누른 사람에게 제안은 서지만 재개는 서지 않는다", async () => {
    const promoted: DirectTask = {
      ...requestTask,
      state: "done",
      derived: { ...requestTask.derived, approval: "approved" },
      origin: {
        kind: "work_request",
        actor_role: "요청자",
        actor: { member_id: "system:meeting", display_name: "회의" },
        source: null,
      },
    };
    // 제안 자리는 열려 있고(`promoted_by_member_id`), 재개 자리는 닫혀 있다(`requester_id`).
    renderDrawer(promoted, {}, { personaId: "mina", viewerIsRequester: true, viewerIsRecordRequester: false });
    await screen.findByLabelText("업무 상세");

    expect(screen.queryByRole("button", { name: "재개" })).toBeNull();

    cleanup();
    // 같은 사람이 같은 업무에서 «제안» 은 부를 수 있다 — 끝나지 않은 업무에서 그 자리를 확인한다.
    renderDrawer({ ...promoted, state: "in_progress", derived: { ...requestTask.derived } }, {}, { personaId: "mina", viewerIsRequester: true, viewerIsRecordRequester: false });
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "취소 제안" })).toBeTruthy();
  });

  /** 일반 요청은 그대로다 — 좁힌 것은 승격 갈래뿐이고 요청자의 재개를 잃지 않았다. */
  it("일반 요청의 요청자는 재개를 그대로 부른다", async () => {
    renderDrawer(
      { ...requestTask, state: "done", derived: { ...requestTask.derived, approval: "approved" } },
      {},
      { personaId: "mina", viewerIsRequester: true, viewerIsRecordRequester: true },
    );
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "재개" })).toBeTruthy();
  });

  /** 본인·배정 업무는 반대다 — **드는 사람**이 다시 연다. */
  it("본인 업무는 드는 사람이 재개한다", async () => {
    const own: DirectTask = { ...requestTask, state: "done", origin: null, delivery: null, derived: { ...requestTask.derived, approval: null } };
    renderDrawer(own);
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "재개" })).toBeTruthy();
  });
});

/**
 * **자리마다 다른 단추** (검수 F-1).
 *
 * 세션 역량(`canManageOwnTasks`)은 「이 사람이 업무를 다룰 수 있나」이지 **「이 업무에서 내 자리가
 * 무엇인가」가 아니다.** 그 둘을 같은 것으로 쓰면 담당자에게 요청자의 단추가 서고, 누를 때마다 403 이다.
 */
describe("명령 노출은 «이 업무에서의 자리» 가 정한다 (검수 F-1)", () => {
  const accepted = { ...requestTask };

  it("요청자에게는 제안 둘이 서고 직접 취소는 없다", async () => {
    renderDrawer(accepted, {}, { personaId: "mina", viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "취소 제안" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "조건 변경 제안" })).toBeTruthy();
    // 수락된 요청 업무의 직접 취소는 서버가 막는다 (V-19).
    expect(screen.queryByRole("button", { name: "업무 취소" })).toBeNull();
  });

  it("담당자에게는 제안이 서지 않는다 — 그 명령은 요청자만 부른다", async () => {
    renderDrawer(accepted, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "취소 제안" })).toBeNull();
    expect(screen.queryByRole("button", { name: "조건 변경 제안" })).toBeNull();
    // 담당자가 부를 수 있는 것은 그대로 남는다 — 자리가 좁아진 것이지 기능이 사라진 것이 아니다.
    expect(screen.getByRole("button", { name: "완료 보고" })).toBeTruthy();
  });

  it("제3자에게는 어느 명령도 서지 않는다", async () => {
    renderDrawer(accepted, {}, { personaId: "sora" });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "취소 제안" })).toBeNull();
    expect(screen.queryByRole("button", { name: "업무 취소" })).toBeNull();
  });

  /**
   * **수락 전 요청 업무는 아무도 들지 않는다** — 담당이 `null` 인 것이 그 모습이다. 그 상태에서
   * 전이 명령은 서버가 «드는 사람» 에게만 여므로 누구에게도 404 다 (`work_tasks.task()` 의 `_held_by`).
   */
  it("수락 전 요청 업무에는 수신자에게도 직접 취소가 서지 않는다", async () => {
    const pending: DirectTask = {
      ...requestTask,
      state: "open",
      assignee: null,
      derived: { ...requestTask.derived, assignment: "awaiting_acceptance" },
    };
    renderDrawer(pending, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "업무 취소" })).toBeNull();
  });

  it("본인 업무와 배정 업무의 직접 취소는 지금까지대로 선다", async () => {
    const own: DirectTask = { ...requestTask, origin: null, delivery: null, assignee: { member_id: "jiho", display_name: "지호 (팀장)" } };
    renderDrawer(own, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "업무 취소" })).toBeTruthy();

    cleanup();
    const assigned: DirectTask = {
      ...requestTask,
      origin: { kind: "direct_assignment", actor_role: "배정자", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: null },
      delivery: null,
      assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
    };
    renderDrawer(assigned, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "업무 취소" })).toBeTruthy();
  });

  /** `personaId` 를 넘기지 않는 화면(오늘·캘린더)에서 있던 길을 닫지 않는다. */
  it("보는 사람을 모르는 화면에서는 본인 업무의 취소가 그대로 선다", async () => {
    const own: DirectTask = { ...requestTask, origin: null, delivery: null };
    renderDrawer(own, {}, { personaId: undefined });
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "업무 취소" })).toBeTruthy();
  });
});

describe("수락 뒤의 제안 (U-9 · V-19·V-20)", () => {
  it("수락된 요청 업무에는 직접 취소 대신 취소 제안이 선다", async () => {
    renderDrawer(requestTask, {}, { viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "업무 취소" })).toBeNull();
    expect(screen.getByRole("button", { name: "취소 제안" })).toBeTruthy();
  });

  it("취소 제안은 사유를 싣고, 회차를 함께 보낸다", async () => {
    vi.mocked(api.createTaskProposal).mockResolvedValue({ task_id: "task-1", proposal: { proposal_id: "p1", kind: "cancellation", state: "pending", proposed_by: "mina", reason: "범위가 없어졌습니다", created_at: "" }, task_version: 5 } as never);
    renderDrawer(requestTask, {}, { viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");

    fireEvent.click(screen.getByRole("button", { name: "취소 제안" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 제안" });
    fireEvent.change(within(prompt).getByLabelText("제안 사유"), { target: { value: "범위가 없어졌습니다" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "제안 보내기" }));

    await waitFor(() =>
      expect(api.createTaskProposal).toHaveBeenCalledWith("task-1", 4, { kind: "cancellation", reason: "범위가 없어졌습니다", payload: undefined }),
    );
  });

  /**
   * **조건 변경은 «무엇을 바꾸는지» 를 실제로 실어 보낸다.** 사유만 보내는 제안은 담당자가 무엇에
   * 동의하는지 모른 채 눌러야 하는 제안이라, 계약(`payload`)이 있는데도 비워 보내면 틀린 것이다.
   */
  it("조건 변경 제안은 바꿀 값을 payload 로 싣는다 — 아무것도 안 바꾸면 보낼 수 없다", async () => {
    vi.mocked(api.createTaskProposal).mockResolvedValue({ task_id: "task-1", proposal: { proposal_id: "p2", kind: "terms_change", state: "pending", proposed_by: "mina", reason: null, created_at: "" }, task_version: 5 } as never);
    renderDrawer(requestTask, {}, { viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");

    fireEvent.click(screen.getByRole("button", { name: "조건 변경 제안" }));
    const prompt = await screen.findByRole("dialog", { name: "조건 변경 제안" });
    // 고친 것이 없으면 보낼 수 없다
    expect((within(prompt).getByRole("button", { name: "제안 보내기" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(within(prompt).getByLabelText("요청 내용"), { target: { value: "첫 안 두 벌을 만든다" } });
    fireEvent.change(within(prompt).getByLabelText("제안 사유"), { target: { value: "범위가 늘었습니다" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "제안 보내기" }));

    await waitFor(() =>
      expect(api.createTaskProposal).toHaveBeenCalledWith("task-1", 4, {
        kind: "terms_change",
        reason: "범위가 늘었습니다",
        payload: { description: "첫 안 두 벌을 만든다" },
      }),
    );
  });

  /**
   * **서버가 적용하는 칸은 셋뿐이다** — `title`·`description`·`due_date`(ISO 문자열 또는 `null`).
   * 화면이 그 밖의 칸을 제안하면 사람은 바뀔 줄 알고 눌렀는데 아무 일도 안 일어난다.
   */
  it("조건 변경 payload 는 title · description · due_date 셋만 싣고, 기한 비우기는 null 로 간다", async () => {
    vi.mocked(api.createTaskProposal).mockResolvedValue({ task_id: "task-1", proposal: { proposal_id: "p5", kind: "terms_change", state: "pending", proposed_by: "mina", reason: null, created_at: "" }, task_version: 5 } as never);
    renderDrawer(requestTask, {}, { viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");

    fireEvent.click(screen.getByRole("button", { name: "조건 변경 제안" }));
    const prompt = await screen.findByRole("dialog", { name: "조건 변경 제안" });
    fireEvent.change(within(prompt).getByLabelText("업무 명"), { target: { value: "디자인 시안 두 벌" } });
    // 기한을 «비우는» 것도 조건 변경이다 — 그 뜻은 `null` 이지 빈 문자열이 아니다.
    fireEvent.click(within(prompt).getByRole("button", { name: `${"기한"} ${"달력 열기"}` }));
    fireEvent.click(await screen.findByRole("button", { name: "지우기" }));
    fireEvent.click(within(prompt).getByRole("button", { name: "제안 보내기" }));

    await waitFor(() => expect(api.createTaskProposal).toHaveBeenCalled());
    const [, , body] = vi.mocked(api.createTaskProposal).mock.calls[0];
    // 고치지 않은 칸은 실리지 않는다 — 안 바꾼 값을 되쓰면 남의 편집을 덮는다.
    expect(Object.keys(body.payload ?? {})).toEqual(["title", "due_date"]);
    expect(body.payload).toEqual({ title: "디자인 시안 두 벌", due_date: null });
  });

  /** 취소 제안은 **사유가 필수이고 payload 가 없다** — 두 종류가 같은 입구를 쓰되 다른 값을 낸다. */
  it("취소 제안은 payload 를 싣지 않고, 사유 없이 보낼 수 없다", async () => {
    vi.mocked(api.createTaskProposal).mockResolvedValue({ task_id: "task-1", proposal: { proposal_id: "p6", kind: "cancellation", state: "pending", proposed_by: "mina", reason: "중단합니다", created_at: "" }, task_version: 5 } as never);
    renderDrawer(requestTask, {}, { viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");

    fireEvent.click(screen.getByRole("button", { name: "취소 제안" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 제안" });
    expect((within(prompt).getByRole("button", { name: "제안 보내기" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(within(prompt).getByLabelText("제안 사유"), { target: { value: "중단합니다" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "제안 보내기" }));

    await waitFor(() => expect(api.createTaskProposal).toHaveBeenCalled());
    const [, , body] = vi.mocked(api.createTaskProposal).mock.calls[0];
    expect(body.payload).toBeUndefined();
    expect(body.reason).toBe("중단합니다");
  });

  it("담당자는 대기 제안에 동의할 수 있고, 무엇을 바꾸는지가 함께 읽힌다", async () => {
    vi.mocked(api.getTaskProposals).mockResolvedValue({
      task_id: "task-1",
      pending: [{ proposal_id: "p3", kind: "terms_change", state: "pending", proposed_by: "mina", responder_id: "jiho", reason: "기한을 늦춥니다", payload: { due_date: "2026-09-30" }, created_at: "" }],
      history: [],
    } as never);
    vi.mocked(api.respondTaskProposal).mockResolvedValue({ task_id: "task-1", proposal: { proposal_id: "p3", kind: "terms_change", state: "agreed", proposed_by: "mina", reason: null, created_at: "" }, task_version: 5 } as never);
    renderDrawer(requestTask);

    const section = await screen.findByLabelText("응답 대기 제안");
    expect(section.textContent).toContain("기한");
    expect(section.textContent).toContain("2026/09/30");

    fireEvent.click(within(section).getByRole("button", { name: "동의" }));
    const prompt = await screen.findByRole("dialog", { name: "제안 동의" });
    fireEvent.click(within(prompt).getByRole("button", { name: "동의" }));

    await waitFor(() => expect(api.respondTaskProposal).toHaveBeenCalledWith("task-1", "p3", 4, { agree: true, reason: undefined }));
  });

  /** 제안만으로는 아무것도 바뀌지 않는다 — 동의하기 전에는 원래 조건이 그대로다 (D-6). */
  it("대기 제안이 있는 동안에는 새 제안을 열지 않는다", async () => {
    vi.mocked(api.getTaskProposals).mockResolvedValue({
      task_id: "task-1",
      pending: [{ proposal_id: "p4", kind: "cancellation", state: "pending", proposed_by: "mina", responder_id: "jiho", reason: null, created_at: "" }],
      history: [],
    } as never);
    renderDrawer(requestTask, {}, { viewerIsRequester: true });
    await screen.findByLabelText("응답 대기 제안");
    expect(screen.queryByRole("button", { name: "취소 제안" })).toBeNull();
    expect(screen.queryByRole("button", { name: "조건 변경 제안" })).toBeNull();
  });
});

/**
 * **직접 취소는 사유가 필수다** — SPEC-003 §4 Validation · SPEC-001 · `WORK_REASON_REQUIRED`.
 *
 * WORK-002 전절 구현에서 빠져 있던 **이미 확정된 계약**이다(새 정책이 아니다). 이 자리는 예전에
 * 확인 모달이라 누르면 곧바로 취소됐고 사유가 서버로 가지 않았다. 권한·허용 대상은 그대로다.
 *
 * BE 계약(실물 확정): `POST /api/tasks/{id}/cancel` 은 `{expected_version, reason}` 이고
 * **reason 이 필수**다 — 빠지거나 공백뿐이면 422. 사람이 쓴 문장은 진행 기록에 남고,
 * `cancel_reason` 은 사유 «텍스트» 가 아니라 취소의 **종류**(`direct` 등)다.
 */
describe("직접 취소 — 사유를 받아 보낸다", () => {
  const ownTask: DirectTask = { ...requestTask, origin: null, delivery: null, derived: { ...requestTask.derived } };

  it("취소를 누르면 곧바로 취소하지 않고 사유 입력 자리가 열린다", async () => {
    const { onTransition } = renderDrawer(ownTask, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");

    fireEvent.click(screen.getByRole("button", { name: "업무 취소" }));
    expect(await screen.findByRole("dialog", { name: "취소 사유" })).toBeTruthy();
    // 자리가 열렸을 뿐 아직 아무 명령도 가지 않았다.
    expect(onTransition).not.toHaveBeenCalled();
  });

  it("공백뿐인 사유는 보내지 않는다 — 서버가 422 로 막는 것을 화면이 먼저 막는다", async () => {
    const { onTransition } = renderDrawer(ownTask, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");
    fireEvent.click(screen.getByRole("button", { name: "업무 취소" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 사유" });

    const confirm = within(prompt).getByRole("button", { name: "업무 취소" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(within(prompt).getByLabelText("취소 사유"), { target: { value: "   " } });
    expect(confirm.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(onTransition).not.toHaveBeenCalled();
  });

  it("사람이 쓴 사유와 «원» 회차를 그대로 보낸다 — 문구를 지어내지 않는다", async () => {
    const { onTransition } = renderDrawer(ownTask, {}, { personaId: "jiho" });
    await screen.findByLabelText("업무 상세");
    fireEvent.click(screen.getByRole("button", { name: "업무 취소" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 사유" });

    fireEvent.change(within(prompt).getByLabelText("취소 사유"), { target: { value: " 분기 계획에서 빠졌습니다 " } });
    fireEvent.click(within(prompt).getByRole("button", { name: "업무 취소" }));

    await waitFor(() => expect(onTransition).toHaveBeenCalledTimes(1));
    const [sentTask, action, reason] = vi.mocked(onTransition).mock.calls[0];
    expect(action).toBe("cancel");
    expect(reason).toBe("분기 계획에서 빠졌습니다");
    expect(sentTask.version).toBe(4);
  });

  it("성공하면 입력 자리가 닫힌다", async () => {
    const { onTransition } = renderDrawer(ownTask, {}, { personaId: "jiho" });
    vi.mocked(onTransition).mockResolvedValue(true as never);
    await screen.findByLabelText("업무 상세");
    fireEvent.click(screen.getByRole("button", { name: "업무 취소" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 사유" });
    fireEvent.change(within(prompt).getByLabelText("취소 사유"), { target: { value: "범위가 없어졌습니다" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "업무 취소" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "취소 사유" })).toBeNull());
  });

  /** 서버가 거절하면(예: 수락된 요청 Task 의 409) **쓴 문장을 잃지 않는다.** */
  it("서버가 거절하면 자리를 닫지 않고 쓴 사유를 그대로 둔다", async () => {
    const { onTransition } = renderDrawer(ownTask, {}, { personaId: "jiho" });
    vi.mocked(onTransition).mockResolvedValue(false as never);
    await screen.findByLabelText("업무 상세");
    fireEvent.click(screen.getByRole("button", { name: "업무 취소" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 사유" });
    fireEvent.change(within(prompt).getByLabelText("취소 사유"), { target: { value: "범위가 없어졌습니다" } });
    fireEvent.click(within(prompt).getByRole("button", { name: "업무 취소" }));

    await waitFor(() => expect(onTransition).toHaveBeenCalled());
    expect(screen.getByRole("dialog", { name: "취소 사유" })).toBeTruthy();
    expect((within(prompt).getByLabelText("취소 사유") as HTMLInputElement).value).toBe("범위가 없어졌습니다");
  });

  /** **연타가 두 번째 명령이 되지 않는다** — 보내는 동안 단추가 잠긴다. */
  it("여러 번 눌러도 명령은 한 번만 나간다", async () => {
    let settle: (value: boolean) => void = () => {};
    const { onTransition } = renderDrawer(ownTask, {}, { personaId: "jiho" });
    vi.mocked(onTransition).mockReturnValue(new Promise<boolean>((resolve) => { settle = resolve; }) as never);
    await screen.findByLabelText("업무 상세");
    fireEvent.click(screen.getByRole("button", { name: "업무 취소" }));
    const prompt = await screen.findByRole("dialog", { name: "취소 사유" });
    fireEvent.change(within(prompt).getByLabelText("취소 사유"), { target: { value: "범위가 없어졌습니다" } });

    const confirm = within(prompt).getByRole("button", { name: "업무 취소" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(onTransition).toHaveBeenCalledTimes(1));
    settle(true);
  });

  /** 수락된 요청 업무에는 이 자리가 아예 없다 — 그쪽은 제안–동의로만 간다(V-19 · 409). */
  it("수락된 요청 업무에는 직접 취소 자리가 없고 취소 제안이 그 자리다", async () => {
    /* 대기 제안이 «없다» 는 것이 이 줄의 전제다 — 앞선 테스트가 남긴 구현에 기대지 않고 못박는다
       (`vi.clearAllMocks()` 는 호출 기록만 지우고 구현은 남긴다). */
    vi.mocked(api.getTaskProposals).mockResolvedValue({ task_id: "task-1", pending: [], history: [] } as never);
    renderDrawer(requestTask, {}, { personaId: "mina", viewerIsRequester: true });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "업무 취소" })).toBeNull();
    expect(screen.getByRole("button", { name: "취소 제안" })).toBeTruthy();
  });
});

describe("완료 보고의 끝 (U-5)", () => {
  /** 제출이 성공하면 밖으로는 `done` + `awaiting_review` 다 — `completion_submitted` 는 계약에 없다. */
  it("승인 대기 중에는 부를 명령이 없다고 말한다", async () => {
    renderDrawer({ ...requestTask, state: "done", derived: { ...requestTask.derived, approval: "awaiting_review" } });
    const banner = await screen.findByLabelText("완료 확인 대기");
    expect(banner.textContent).toContain("민아");
    expect(screen.queryByRole("button", { name: "완료 보고" })).toBeNull();
    expect(screen.queryByRole("button", { name: "완료 처리" })).toBeNull();
  });
});

describe("시작하지 않고 끝나는 일 (7-C · SPEC-001 §4 State)", () => {
  it("open 에서도 상세 상단에 [완료]가 선다 — [시작]과 함께", async () => {
    const own: DirectTask = { ...requestTask, state: "open", origin: null, derived: { ...requestTask.derived } };
    renderDrawer(own);
    await screen.findByLabelText("업무 상세");
    expect(screen.getByRole("button", { name: "시작" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "완료 처리" })).toBeTruthy();
  });
});
