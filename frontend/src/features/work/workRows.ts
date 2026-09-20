import { dayDifference, isOverdue, type WorkChip } from "../../lib/labels";
import type { DerivedApproval, DerivedAssignment, DirectTask, TaskChild, TaskState, WorkRequest } from "../../lib/viewModels";

/**
 * 표 세 벌이 읽는 «행» 과 칩이 거는 «조건» — 화면 밖의 순수한 자리다 (WORK-002 Phase 7-B·7-C).
 *
 * 세 가지를 여기 모은 이유가 각각 있다.
 *
 * 1. 수락 전 요청은 수신함에서 응답한다. 내 업무에는 담당 Task만 담는다.
 * 2. **칩은 상태 나열이 아니라 파생 조건이다**(SPEC-003 §2.6). 그 판정을 한 함수로 두어야
 *    「칩 라벨의 건수 = 그 칩이 거는 필터의 건수」가 저절로 지켜진다.
 * 3. **완결 판정이 `state` 만 보지 않는다**(V-15·V-16). 요청 업무는 승인까지여야 완결이고,
 *    그 판정은 `derived.approval` 에서만 나온다.
 *
 * **여기서 권한을 추론하지 않는다.** 서버가 낸 값을 고르고 세기만 한다.
 */

/** 서버가 낸 파생 기다림. 없으면 `null` — 「모른다」이지 「없다」가 아니다. */
export function assignmentOf(task: DirectTask | null | undefined): DerivedAssignment | null {
  return task?.derived?.assignment ?? null;
}

export function approvalOf(task: DirectTask | null | undefined): DerivedApproval | null {
  return task?.derived?.approval ?? null;
}

/** 요청으로 생긴 업무인가. 완료의 «끝» 이 다르므로(승인이 최종 완료다) 행마다 갈라 읽는다. */
export function isRequestTask(task: DirectTask | null | undefined): boolean {
  return task?.origin?.kind === "work_request" || Boolean(task?.lineage?.source_work_request_id);
}

/**
 * 상위의 최종 완료를 통과시키는 「완결」 판정 (SPEC-003 §4 Data · V-15·V-16).
 *
 * 취소가 아니고 · `done` 이며 · **승인을 기다리고 있지 않다.**
 *
 * **읽는 것은 승인 «값» 하나다** — `awaiting_review`·`awaiting_revision` 이면 아직 끝이 아니고,
 * `approved` 이거나 이 축이 없는(`null`·부재) 업무는 `done` 으로 끝이다.
 *
 * **승인 축을 모르면 완결로 센다.** 전에는 「요청 업무인데 `derived` 가 통째로 없으면 미완결」이라는
 * 보수 분기를 두었는데, 그 분기는 **`TaskChild` 에서 죽어 있었다** — `TaskChild`(`viewModels.ts`)에는
 * `origin` 도 `lineage` 도 없어서 요청 축을 알 길이 자체가 없고, 그래서 주석이 약속한 동작과 코드가
 * 달랐다(검수 W-1). 없는 값에서 「요청 업무일 것이다」를 **발명하지 않는다.**
 *
 * 그래도 안전한 이유는 **이 함수가 원장이 아니기 때문**이다. 상위 완료를 막을지는 서버가 정하고
 * (`derived.blocking_children` · `child_progress` · 완료 명령의 409), 화면은 그 값을 **먼저** 쓴다
 * (`blockingChildrenOf`·`childProgressOf`). 이 셈은 서버가 그 값을 주지 않았을 때의 **표시용**이다.
 */
export function isChildSettled(child: TaskChild | DirectTask): boolean {
  if (child.state === "cancelled") return false;
  if (child.state !== "done") return false;
  const approval = child.derived?.approval ?? null;
  return approval !== "awaiting_review" && approval !== "awaiting_revision";
}

/**
 * 상위 완료를 막는 하위 — **서버가 낸 목록이 원장이다** (SPEC-003 §4 `derived.blocking_children`).
 *
 * 목록이 오지 않은 응답에서만 직속 하위를 화면이 직접 센다. 이때도 **취소는 막지 않는다**(V-16).
 */
export function blockingChildrenOf(task: DirectTask): Array<{ task_id: string; title: string; why: string }> {
  const given = task.derived?.blocking_children;
  if (given) return given;
  return (task.children ?? [])
    .filter((child) => !isChildCancelled(child) && !isChildSettled(child))
    .map((child) => ({
      task_id: child.task_id,
      title: child.title,
      why: child.state === "done" ? "awaiting_approval" : "unfinished",
    }));
}

/** 직속 하위의 셈 — 서버 값이 먼저다. 취소는 **완결과 따로** 센다 (V-16). */
export function childProgressOf(task: DirectTask): { done: number; blocking: number; cancelled: number; total: number } {
  const children = task.children ?? [];
  const cancelled = children.filter(isChildCancelled).length;
  const counted = children.filter((child) => !isChildCancelled(child));
  const local = {
    done: counted.filter(isChildSettled).length,
    blocking: counted.filter((child) => !isChildSettled(child)).length,
    cancelled,
    total: children.length,
  };
  const given = task.child_progress;
  if (!given) return local;
  return {
    done: given.done,
    blocking: given.blocking ?? local.blocking,
    cancelled: given.cancelled ?? cancelled,
    total: given.total,
  };
}

/** 취소된 하위는 검사에서 빠지되 목록과 로그에는 남는다 (V-16). */
export function isChildCancelled(child: TaskChild | DirectTask): boolean {
  return child.state === "cancelled";
}

/**
 * **시작을 막는 선행업무** (SPEC-001 U-14 · §4 `WORK_PREDECESSORS_UNFINISHED`).
 *
 * 끝나지 않은 선행이 있으면 `시작 전` 업무의 `[시작]` 과 (`시작 전` 에서의) `[완료]` 를 둘 다
 * 막는다 — 시작하지 않고 끝내는 길이 열려 있으면 그것이 시작 게이트의 우회로가 된다.
 *
 * **취소된 선행은 막지 않는다.** 그 일은 더 기다릴 것이 없다.
 * **미완 하위와 다른 축이다** — 하위는 «완료» 를, 선행은 «시작» 을 막는다. 한 자리에서 세면
 * 무엇을 먼저 해야 하는지가 사라진다(오류 코드도 서로 다르다).
 *
 * **볼 수 없는 선행은 여기 없다** — 서버가 제목을 빼고 건수만 내기 때문이다. 그 건수는
 * `hiddenPrecedingCountOf` 가 따로 읽고, 막는 판정에는 넣지 않는다: 화면이 셀 수 없는 것으로
 * 단추를 막으면 왜 막혔는지 말해 줄 수 없다. 최종 판정은 어차피 서버가 한다.
 */
export function blockingPredecessorsOf(task: DirectTask | null | undefined): Array<{ task_id: string; title: string }> {
  return (task?.predecessors ?? [])
    .filter((row) => row.title !== null && row.state !== "done" && row.state !== "cancelled")
    .map((row) => ({ task_id: row.task_id, title: row.title as string }));
}

/**
 * 볼 수 없는 선행의 건수 — **배열 안의 빈 자리를 센다**(서버가 `title`·`state` 를 비워 보낸다).
 *
 * 제목은 숨기고 건수는 낸다. 막는 판정에는 넣지 않는다: 화면이 이름을 말할 수 없는 것으로 단추를
 * 막으면 왜 막혔는지 알려 줄 수 없다. 최종 판정은 어차피 서버가 한다.
 */
export function hiddenPrecedingCountOf(task: DirectTask | null | undefined): number {
  return (task?.predecessors ?? []).filter((row) => row.title === null).length;
}

/** 볼 수 있는 선행만 — 상세의 줄이 이름으로 그리는 자리다. */
export function visiblePredecessorsOf(task: DirectTask | null | undefined): Array<{ task_id: string; title: string; state: TaskState }> {
  return (task?.predecessors ?? [])
    .filter((row) => row.title !== null && row.state !== null)
    .map((row) => ({ task_id: row.task_id, title: row.title as string, state: row.state as TaskState }));
}

/**
 * 이 업무의 `[시작]`·(`시작 전` 의) `[완료]` 가 선행 때문에 막혔나.
 *
 * **`시작 전` 에서만 본다** — `진행 중` 의 완료는 선행을 보지 않는다(막는 것은 시작이다).
 * 이미 시작한 뒤에 선행이 다시 열려도 되돌리지 않는다: 게이트는 시작 시점 판정이다.
 */
export function startBlockedByPredecessors(task: DirectTask | null | undefined): boolean {
  return task?.state === "open" && blockingPredecessorsOf(task).length > 0;
}

/** 기한이 지난 날수 — 서버 값이 먼저다. 없으면 기한과 오늘로 센다(표시만 바뀐다 · U-14). */
export function overdueDaysOf(task: DirectTask | null | undefined, today: string): number {
  if (!task) return 0;
  const given = task.derived?.overdue_days;
  if (typeof given === "number") return given > 0 ? given : 0;
  return isOverdue(task, today) ? dayDifference(task.due_date!, today) : 0;
}

/** 한 행 — 들고 있는 Task 이거나, 아직 수락하지 않은 요청이거나, 둘 다인 자리다. */
export type WorkRow = {
  /** React 키이자 이 행을 여는 손잡이. Task 가 있으면 그 id 다. */
  id: string;
  title: string;
  task: DirectTask | null;
  /** 이 행을 만든 요청. 수락·거절·철회·재요청이 여기서 나온다. */
  request: WorkRequest | null;
  dueDate: string | null;
  /** 아직 수락하지 않아서 담당이 서지 않은 행인가 (`derived.assignment`). */
  awaitingAcceptance: boolean;
  approval: DerivedApproval | null;
};

function requestOfTask(task: DirectTask, requests: WorkRequest[]): WorkRequest | null {
  const sourceId = task.lineage?.source_work_request_id ?? (task.origin?.source?.type === "work_request" ? task.origin.source.id : null);
  if (sourceId) return requests.find((row) => row.request_id === sourceId) ?? null;
  return requests.find((row) => row.task_id === task.task_id) ?? null;
}

/**
 * **요청자 «자리» 에 선 사람인가** — 제안·철회·재개가 이 판정 위에 선다 (SPEC-003 §5).
 *
 * 백엔드가 같은 것을 `_is_request_owner`(`application.py`)·`requests.py:797` 에서
 * **`requester_id` 또는 `promoted_by_member_id`** 로 판정한다. 그 둘째 항이 중요하다 —
 * **회의 승격 요청은 `requester_id` 가 `system:meeting`** 이고 누른 사람은 `promoted_by_member_id`
 * 에만 있다(BASE-002 O-31). 그래서 **`origin.actor` 하나로 판정하면 안 된다**:
 * `_origin_projection` 이 그 자리에 싣는 값은 `request_requester_id` 라서, 승격 요청에서는
 * 시스템 id 가 오고 **누른 본인이 자기 요청에서 제외된다.**
 *
 * 담당자 여부로 요청자를 «추정» 하지도 않는다 — 둘은 다른 자리이고, 한 사람이 둘 다일 수도 있다.
 * 값을 모르면 `false` 다: 최종 판정은 서버가 다시 하므로, 화면은 **없는 권한을 그리지 않는** 쪽으로 닫는다.
 */
export function isRequestOwner(request: WorkRequest | null | undefined, personaId: string | null | undefined): boolean {
  if (!request || !personaId) return false;
  return request.requester_id === personaId || request.promoted_by_member_id === personaId;
}

/**
 * **요청 «행» 의 요청자인가** — 재개가 이 판정 위에 선다. `isRequestOwner` 보다 좁다.
 *
 * 서버가 두 명령을 **다른 함수로** 판정하기 때문이다.
 * · 제안: `_is_request_owner` = `requester_id` **또는** `promoted_by_member_id`
 * · 재개: `_require_may_reopen` → `_requester_of` = **`requester_id` 하나뿐**
 *
 * 그래서 회의 승격 요청(`requester_id = system:meeting`)에서는 **누른 사람도 재개할 수 없다** —
 * 그 값이 사람 id 와 같아지는 일이 없기 때문이다. 둘을 한 값으로 묶어 그리면 승격자에게
 * **누를 때마다 403 인 단추**가 선다(재검수 N-1).
 *
 * **이것은 그 자리의 정책을 정하는 판정이 아니다.** 승격 요청의 확인자·재개자가 누구인지는
 * **OQ-206 미결**이고, 여기서는 지금 서버가 여는 만큼으로 노출을 좁힐 뿐이다 — 미결을 기본값으로
 * 확정하지 않는다. 서버가 그 자리를 열면 이 판정도 함께 넓힌다.
 */
export function isRequestRecordRequester(request: WorkRequest | null | undefined, personaId: string | null | undefined): boolean {
  if (!request || !personaId) return false;
  return request.requester_id === personaId;
}

/** 아직 응답을 기다리는 요청인가 — 요청 축의 여섯 값 중 앞의 둘이다 (SPEC-003 §4 State). */
export function isOpenRequest(request: WorkRequest): boolean {
  return request.state === "pending" || request.state === "negotiating";
}

/** 내 업무는 담당 Task만 담는다. 요청 정보는 수락 후에도 같은 Task에 연결한다. */
export function myWorkRows(tasks: DirectTask[], requestsToMe: WorkRequest[]): WorkRow[] {
  return tasks.flatMap((task) => {
    const request = requestOfTask(task, requestsToMe);
    if (request && isOpenRequest(request)) return [];
    return [{
      id: task.task_id,
      title: task.title,
      task,
      request,
      dueDate: task.due_date ?? null,
      awaitingAcceptance: assignmentOf(task) === "awaiting_acceptance",
      approval: approvalOf(task),
    }];
  });
}

/**
 * 칩 하나가 거는 조건 (SPEC-003 §2.6).
 *
 * **읽을 수 없는 것은 애초에 목록에 없다** — 서버가 낸 행만 여기에 오므로 건수에도 들어가지 않는다(U-15).
 */
export function matchesChip(row: WorkRow, chip: WorkChip, today: string): boolean {
  switch (chip) {
    case "all":
      return true;
    case "awaiting_acceptance":
      return row.awaitingAcceptance;
    case "overdue":
      return overdueDaysOf(row.task, today) > 0 || (!row.task && isOverdueDate(row.dueDate, today));
    case "open":
      return row.task?.state === "open" && !row.awaitingAcceptance;
    case "in_progress":
      return row.task?.state === "in_progress";
    case "not_started":
      return row.task ? row.task.state === "open" : true;
    case "awaiting_review":
      return row.approval === "awaiting_review";
    default:
      return true;
  }
}

function isOverdueDate(dueDate: string | null, today: string): boolean {
  return Boolean(dueDate) && dueDate! < today;
}

/** 칩 라벨에 붙는 건수 — **그 칩이 거는 필터의 건수** 그대로다. */
export function chipCounts(rows: WorkRow[], chips: ReadonlyArray<WorkChip>, today: string): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const chip of chips) counts[chip] = rows.filter((row) => matchesChip(row, chip, today)).length;
  return counts;
}

/** 「완료 업무」 탭에 서는 행인가 — 끝났거나 취소된 것이다. 취소는 완료가 아니지만 둘 다 «끝» 이다. */
export function isClosedRow(row: WorkRow): boolean {
  return row.task?.state === "done" || row.task?.state === "cancelled";
}
