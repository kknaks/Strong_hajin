import { describe, expect, it } from "vitest";

import type { DirectTask, TaskChild, WorkRequest } from "../../lib/viewModels";
import { blockingChildrenOf, childProgressOf, chipCounts, isChildSettled, isRequestOwner, matchesChip, myWorkRows, overdueDaysOf } from "./workRows";

/**
 * 표 세 벌이 읽는 행과 칩이 거는 조건 — 화면 없이 판정만 본다 (WORK-002 Phase 7-B·7-C).
 *
 * 여기 있는 줄은 전부 **잘못 읽으면 조용히 틀리는** 자리다: 완결 판정이 한 칸만 어긋나도 막아야 할
 * 완료가 지나가고, 칩 건수가 목록과 갈리면 「읽을 수 없는 것은 세지 않는다」가 깨진다.
 */

const today = "2026-09-17";

const task = (over: Partial<DirectTask> & { task_id: string }): DirectTask => ({
  title: over.title ?? over.task_id,
  state: "open",
  version: 1,
  block_reason: null,
  ...over,
});

const request = (over: Partial<WorkRequest> & { request_id: string }): WorkRequest => ({
  title: over.title ?? over.request_id,
  state: "pending",
  version: 1,
  task_id: null,
  assignment_state: null,
  conditions: null,
  ...over,
});

describe("하위 완결 판정", () => {
  /**
   * **`derived` 의 «존재» 를 요청 업무 여부로 읽지 않는다.** v2 는 모든 Task 에 `derived` 를 달아
   * 보내므로, 그것을 「요청 업무다」로 읽으면 본인·배정 하위가 `approval=null` 인 채 영원히 미완결로
   * 선다 — 상위가 영영 완료되지 않는다.
   */
  it("본인 업무 하위는 done 이면 완결이다 — derived 가 붙어 있어도 승인을 요구하지 않는다", () => {
    const child: TaskChild = {
      task_id: "c1",
      title: "자료 정리",
      state: "done",
      derived: { assignment: null, approval: null, proposal: null, blocking_children: [], overdue_days: null },
    };
    expect(isChildSettled(child)).toBe(true);
  });

  it("승인을 기다리는 하위는 done 이어도 완결이 아니다", () => {
    const child: TaskChild = { task_id: "c2", title: "디자인", state: "done", derived: { approval: "awaiting_review" } };
    expect(isChildSettled(child)).toBe(false);
  });

  it("보완 요청을 받은 하위도 완결이 아니다", () => {
    expect(isChildSettled({ task_id: "c3", title: "디자인", state: "done", derived: { approval: "awaiting_revision" } })).toBe(false);
  });

  it("승인이 끝난 요청 하위는 완결이다", () => {
    expect(isChildSettled({ task_id: "c4", title: "디자인", state: "done", derived: { approval: "approved" } })).toBe(true);
  });

  it("취소된 하위는 완결이 아니다 — 대신 검사에서 빠진다", () => {
    expect(isChildSettled({ task_id: "c5", title: "취소된 것", state: "cancelled", cancel_reason: "request_rejected" })).toBe(false);
  });

  /**
   * **승인 축을 모르면 완결로 센다** (검수 W-1 로 정정).
   *
   * 전에는 「요청 업무인데 `derived` 가 없으면 미완결」이라는 보수 분기가 있었는데, 그 분기는
   * `TaskChild` 에서 **죽어 있었다** — 하위 투영에는 `origin` 도 `lineage` 도 없어서 요청 축을 알
   * 길이 없다. 주석만 그렇게 약속하고 코드는 다르게 동작했다. 없는 값에서 요청 업무를 **발명하지
   * 않는다**: 막을지는 서버의 `blocking_children`·`child_progress`·완료 409 가 정한다.
   */
  it("승인 축이 통째로 없는 응답은 완결로 센다 — 없는 값에서 요청 업무를 발명하지 않는다", () => {
    const child: TaskChild = { task_id: "c6", title: "옛 응답", state: "done" };
    expect(isChildSettled(child)).toBe(true);
    const asTask = task({ task_id: "c7", state: "done", origin: { kind: "work_request", actor_role: "요청자", actor: null, source: null } });
    expect(isChildSettled(asTask)).toBe(true);
  });
});

describe("요청자 자리 판정 (검수 F-1)", () => {
  /** 서버(`_is_request_owner` · `requests.py:797`)와 같은 두 항을 읽는다. */
  it("요청자 본인은 자기 자리다", () => {
    expect(isRequestOwner(request({ request_id: "r1", requester_id: "mina" }), "mina")).toBe(true);
  });

  it("담당자는 요청자가 아니다 — 담당 여부로 요청자를 추정하지 않는다", () => {
    expect(isRequestOwner(request({ request_id: "r1", requester_id: "mina", assignee_id: "jiho" }), "jiho")).toBe(false);
  });

  /**
   * **회의 승격이 이 판정의 핵심이다.** 요청자 자리에는 `system:meeting` 이 서고 누른 사람은
   * `promoted_by_member_id` 에만 있다(O-31). `origin.actor` 하나로 판정하면 — 그 값이
   * `request_requester_id` 이므로 — **누른 본인이 자기 요청에서 빠진다.**
   */
  it("승격 요청은 누른 사람이 요청자 자리다", () => {
    const promoted = request({ request_id: "r2", requester_id: "system:meeting", promoted_by_member_id: "mina", requester_kind: "system" });
    expect(isRequestOwner(promoted, "mina")).toBe(true);
    expect(isRequestOwner(promoted, "jiho")).toBe(false);
  });

  it("행이 없거나 보는 사람을 모르면 자리가 아니다 — 모르면 닫는다", () => {
    expect(isRequestOwner(null, "mina")).toBe(false);
    expect(isRequestOwner(request({ request_id: "r1", requester_id: "mina" }), null)).toBe(false);
  });
});

describe("하위 셈과 막는 하위", () => {
  it("취소는 완결과 따로 세고, 막는 하위는 이름으로 남는다", () => {
    const parent = task({
      task_id: "p1",
      children: [
        { task_id: "c1", title: "끝난 것", state: "done", derived: { approval: null } },
        { task_id: "c2", title: "아직 하는 것", state: "in_progress" },
        { task_id: "c3", title: "승인 대기", state: "done", derived: { approval: "awaiting_review" } },
        { task_id: "c4", title: "거절돼 취소", state: "cancelled", cancel_reason: "request_rejected" },
      ],
    });
    expect(childProgressOf(parent)).toEqual({ done: 1, blocking: 2, cancelled: 1, total: 4 });
    expect(blockingChildrenOf(parent)).toEqual([
      { task_id: "c2", title: "아직 하는 것", why: "unfinished" },
      { task_id: "c3", title: "승인 대기", why: "awaiting_approval" },
    ]);
  });

  /** **서버가 낸 목록이 원장이다** — 화면의 셈은 그것이 없을 때의 대체다. */
  it("서버가 막는 하위를 내면 그 목록을 그대로 쓴다", () => {
    const parent = task({
      task_id: "p2",
      children: [{ task_id: "c1", title: "보이는 것", state: "done", derived: { approval: null } }],
      derived: { blocking_children: [{ task_id: "hidden", title: "내가 못 읽는 하위", why: "unfinished" }] },
    });
    expect(blockingChildrenOf(parent)).toEqual([{ task_id: "hidden", title: "내가 못 읽는 하위", why: "unfinished" }]);
  });

  it("서버 child_progress 가 오면 그 값이 이긴다", () => {
    const parent = task({ task_id: "p3", children: [], child_progress: { done: 2, blocking: 1, cancelled: 3, total: 6 } });
    expect(childProgressOf(parent)).toEqual({ done: 2, blocking: 1, cancelled: 3, total: 6 });
  });
});

describe("「내 업무」의 행", () => {
  /** 수락 전 요청 업무는 `my_work` 에 서지 않는다 — 그래도 응답할 자리는 있어야 한다 (V-9·V-10). */
  it("아직 수락하지 않은 요청은 담당 목록에 서지 않는다", () => {
    const rows = myWorkRows([], [request({ request_id: "r1", title: "디자인 요청", task_id: "t1" })]);
    expect(rows).toHaveLength(0);
  });

  it("같은 업무를 두 원천이 가리키면 한 행으로 합친다", () => {
    const rows = myWorkRows(
      [task({ task_id: "t1", title: "디자인", lineage: { request_thread_id: null, source_work_request_id: "r1", source_decision_item_id: null, source_submission_id: null, source_review_decision_id: null, source_action_item_id: null, source_task_id: null } })],
      [request({ request_id: "r1", title: "디자인 요청", task_id: "t1", state: "accepted" })],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].request?.request_id).toBe("r1");
  });

  it("이미 판단이 끝난 요청은 대기 행으로 서지 않는다", () => {
    const rows = myWorkRows([], [request({ request_id: "r1", state: "accepted", task_id: "t1" })]);
    expect(rows).toHaveLength(0);
  });
});

describe("칩이 거는 조건", () => {
  const rows = myWorkRows(
    [
      task({ task_id: "t1", title: "시작 전", state: "open" }),
      task({ task_id: "t2", title: "진행 중", state: "in_progress", due_date: "2026-09-10" }),
      task({ task_id: "t3", title: "수락 대기", state: "open", derived: { assignment: "awaiting_acceptance" } }),
    ],
    [],
  );

  it("건수는 그 칩이 거는 필터의 건수 그대로다", () => {
    expect(chipCounts(rows, ["all", "awaiting_acceptance", "open", "in_progress", "overdue"], today)).toEqual({
      all: 3,
      awaiting_acceptance: 1,
      open: 1,
      in_progress: 1,
      overdue: 1,
    });
  });

  /** 「시작 전」 칩은 **수락 대기를 담지 않는다** — 둘은 같은 `open` 위에 서지만 다른 자리다 (U-1). */
  it("수락 대기 행은 「시작 전」에 섞이지 않는다", () => {
    const open = rows.filter((row) => matchesChip(row, "open", today));
    expect(open.map((row) => row.title)).toEqual(["시작 전"]);
  });
});

describe("기한 초과는 표시만 바꾼다", () => {
  it("서버가 낸 날수가 먼저다", () => {
    expect(overdueDaysOf(task({ task_id: "t1", due_date: "2026-09-01", derived: { overdue_days: 9 } }), today)).toBe(9);
  });

  it("서버 값이 없으면 기한과 오늘로 센다", () => {
    expect(overdueDaysOf(task({ task_id: "t1", state: "in_progress", due_date: "2026-09-10" }), today)).toBe(7);
  });

  it("끝난 업무는 기한이 지나도 지연으로 세지 않는다", () => {
    expect(overdueDaysOf(task({ task_id: "t1", state: "done", due_date: "2026-09-10" }), today)).toBe(0);
  });
});
