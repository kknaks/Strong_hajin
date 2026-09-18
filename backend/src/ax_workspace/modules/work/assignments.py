"""Manager-assigned Tasks: 명령이 성공하면 **활성 담당**이 그 자리에 선다 — 수락을 기다리지 않는다 (WORK-001 Phase 4).

과거에 만들어진 수락 대기 배정(`status='pending'`)은 그대로 남고 그것에 답하는 명령도 지우지 않는다.
신규 생성이 그 회차를 **만들지 않는다**는 것이 이번 계약이다.
"""
from __future__ import annotations


from ax_workspace.modules.work.task_results import TaskMutationResult

from datetime import date
from typing import Any, Protocol
from uuid import UUID
from ax_workspace.modules.work.task_results import TaskAssignmentResult
from ax_workspace.modules.work.assignment_commands import AssignmentAcceptCommand, AssignmentDeclineCommand
from ax_workspace.modules.work.task_creation import TaskAssignmentInput
from ax_workspace.modules.work.project_commands import ProjectWorkInput
from ax_workspace.modules.work.task_commands import TaskReassignInput

from ax_workspace.modules.organization_access.domain import Principal, TASK_ASSIGN, TASK_READ, TASK_SELF_MANAGE
from ax_workspace.modules.work.application import InvalidTaskTransition, TaskAccessDenied, TaskApplication, TaskError, TaskNotFound, validate_schedule, _iso
from ax_workspace.modules.work.errors import TaskAssignmentProposalExists


class TaskAssignmentRepository(Protocol):
    def create_assigned_task(
        self, assigner_id: str, assignee_id: str, title: str, *, description: str | None = None, start_date: date | None = None, due_date: date | None = None, causation_key: str | None = None, checklist: list[str] | None = None, references: list[UUID] | None = None, source_action_item_id: UUID | None = None, source_decision_item_id: UUID | None = None, source_submission_id: UUID | None = None, source_review_decision_id: UUID | None = None
    ) -> tuple[Any, Any]: ...
    def assignment(self, assignment_id: UUID, *, lock: bool = False) -> Any: ...
    def task_for(self, assignment: Any) -> Any: ...
    def pending_for(self, assignee_id: str) -> list[tuple[Any, Any]]: ...
    def assigned_by(self, assigner_id: str) -> list[tuple[Any, Any]]: ...
    def decide(self, assignment: Any, actor_id: str, decision: str, *, reason: str | None = None) -> Any: ...
    def cancel(self, assignment: Any, actor_id: str) -> None: ...
    def task_by_id(self, task_id: UUID, *, lock: bool = False) -> Any: ...
    def current_assignment_for(self, task_id: UUID, *, lock: bool = False) -> Any: ...
    def pending_assignment_for(self, task_id: UUID, *, lock: bool = False) -> Any: ...
    def assignment_rows_for(self, task_id: UUID) -> list[Any]: ...
    def reassign(self, task: Any, current: Any, assigner_id: str, assignee_id: str, reason: str | None) -> Any: ...
    def hand_to(self, task: Any, assigner_id: str, assignee_id: str) -> Any: ...
    def create_unheld_task(self, creator_id: str, title: str, **fields: Any) -> Any: ...


class TaskAssigneeDirectory(Protocol):
    def task_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_task_assignee(self, principal: Principal, assignee_id: str) -> bool: ...


class ProjectAssigneePort(Protocol):
    """프로젝트 축이 아는 것: 이 사람이 배정할 수 있는 프로젝트의 사람들."""

    def assignable_members(self, principal: Principal) -> list[dict[str, str]]: ...
    def may_assign_in(self, principal: Principal, project_id: UUID) -> bool: ...
    def readable_project_ids(self, principal: Principal) -> frozenset[str]: ...


class TaskAssignmentApplication:
    def __init__(
        self,
        repository: TaskAssignmentRepository,
        directory: TaskAssigneeDirectory,
        tasks: TaskApplication,
        projects: ProjectAssigneePort,
    ) -> None:
        self._repository = repository
        self._directory = directory
        # Assigning a part of something needs the Task module's own rules about what may be a parent.
        self._tasks = tasks
        self._projects = projects

    def candidates(self, principal: Principal) -> list[dict[str, str]]:
        self._require(principal, TASK_ASSIGN)
        return self._assignable(principal)

    def _assignable(self, principal: Principal) -> list[dict[str, str]]:
        """배정할 수 있는 사람 — 조직 축과 프로젝트 축을 여기 한 곳에서 합친다.

        목록과 판정이 같은 곳에서 나와야 한다. 읽기에서 두 곳이 각자 판정하다 조용히 어긋난 적이 있고, 그때는
        자기 팀 프로젝트에 일을 매달지 못하는 모양으로 드러났다.
        """
        rows = {row["id"]: row for row in self._directory.task_assignment_candidates(principal)}
        for row in self._projects.assignable_members(principal):
            rows.setdefault(row["id"], row)
        return [rows[member_id] for member_id in sorted(rows)]

    def _may_put_on(self, principal: Principal, assignee_id: str) -> bool:
        return any(row["id"] == assignee_id for row in self._assignable(principal))

    def assign(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        causation_key: str | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        source_action_item_id: UUID | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> TaskAssignmentResult:
        """Create a Task for someone else. 명령이 성공하면 **그 사람의 업무 목록에 즉시** 선다."""
        self._require(principal, TASK_ASSIGN)
        try:
            command = TaskAssignmentInput(title=title, assignee_id=assignee_id, description=description, start_date=start_date, due_date=due_date, checklist=checklist, reference_task_ids=reference_task_ids, parent_task_id=parent_task_id)
        except ValueError as error:
            raise TaskError(str(error)) from error
        title, assignee_id, description = command.title, command.assignee_id, command.description
        start_date, due_date = command.start_date, command.due_date
        checklist, reference_task_ids, parent_task_id = command.checklist, command.reference_task_ids, command.parent_task_id
        if assignee_id == str(principal.id):
            raise TaskError("use a self-owned task instead of assigning yourself")
        if not self._may_put_on(principal, assignee_id):
            raise TaskError("assignee is not within your assignment scope")
        # 배정은 즉시 활성 담당이 서므로(BASE-002 O-28) 드는 사람은 `assignee_id` 다.
        parent = self._tasks.parent_for(principal, parent_task_id, assignee_id=assignee_id) if parent_task_id is not None else None
        references = self._tasks._readable_tasks(principal, reference_task_ids)
        task, assignment = self._repository.create_assigned_task(
            str(principal.id), assignee_id, title,
            description=description, start_date=start_date, due_date=due_date, causation_key=causation_key,
            checklist=checklist,
            references=references,
            parent_task_id=parent.id if parent is not None else None,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
        )
        if parent is not None:
            self._tasks.record_subtask(principal, parent, task)
        return self._view(assignment, task, principal)

    def plan_project_work(
        self,
        principal: Principal,
        project_id: UUID,
        title: str,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
    ) -> TaskMutationResult:
        """프로젝트 계획에 일을 올린다. 사람은 아직 정하지 않는다.

        무슨 일이 있는지와 누가 하는지는 다른 질문이다. 계획을 먼저 펼치고 사람을 나중에 붙이는 것이 프로젝트가
        움직이는 방식이며, 배정 행이 하나도 없다는 것이 곧 `담당자 미정`이다.
        """
        self._require(principal, TASK_ASSIGN)
        if not self._projects.may_assign_in(principal, project_id):
            raise TaskError("이 프로젝트에 업무를 올릴 수 있는 자격이 없습니다")
        command = ProjectWorkInput(title=title, description=description, start_date=start_date, due_date=due_date)
        title, description, start_date, due_date = command.title, command.description, command.start_date, command.due_date
        if not title.strip():
            raise TaskError("title is required")
        validate_schedule(start_date, due_date)
        task = self._repository.create_unheld_task(
            str(principal.id),
            title.strip(),
            project_id=project_id,
            description=(description or "").strip() or None,
            start_date=start_date,
            due_date=due_date,
        )
        return self._tasks._view(task, principal)

    def reassign(self, principal: Principal, task_id: UUID, expected_version: int, assignee_id: str, reason: str | None = None) -> TaskAssignmentResult:
        """Put someone else on work that is already underway.

        Changing who holds the work is its own command, never a field on the Task edit form: it moves a relationship,
        and the person taking it on still gets to accept or decline.
        """
        self._require(principal, TASK_ASSIGN)
        # Take the Task row first: the version this command answers must not be read before someone else's move.
        task = self._repository.task_by_id(task_id, lock=True)
        if task is None:
            raise TaskNotFound("task was not found")
        # Assignment capability and a valid destination do not grant access to
        # the source task. Check its owner projection after taking the row lock.
        self._tasks.get(principal, task_id)
        command = TaskReassignInput(expected_version=expected_version, assignee_id=assignee_id, reason=reason)
        expected_version, assignee_id, reason = command.expected_version, command.assignee_id, command.reason
        current = self._repository.current_assignment_for(task_id, lock=True)
        waiting = self._repository.pending_assignment_for(task_id, lock=True)
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if waiting is not None:
            # 한 업무에 대기 제안은 하나다 (SPEC-003 §4 Validation · `WORK_ASSIGNMENT_PROPOSAL_EXISTS`).
            raise TaskAssignmentProposalExists("이미 응답을 기다리는 담당 변경 제안이 있습니다")
        if current is not None and assignee_id == current.assignee_id:
            raise TaskError("that person already holds this task")
        # Taking the work on yourself is not assigning to yourself: the candidate list is about who you may put on
        # someone else's work, and one may always take it back.
        if assignee_id != str(principal.id) and not self._may_put_on(principal, assignee_id):
            raise TaskError("assignee is not within your assignment scope")
        # 아직 아무도 들지 않은 일이면 옮기는 것이 아니라 처음 붙이는 것이다. 옮겨 올 자리가 없다는 이유로
        # 거절하면 프로젝트에만 있고 사람이 정해지지 않은 일에 담당자를 줄 방법이 없어진다.
        appended = (
            self._repository.hand_to(task, str(principal.id), assignee_id)
            if current is None
            else self._repository.reassign(task, current, str(principal.id), assignee_id, (reason or "").strip() or None)
        )
        return self._view(appended, task, principal)

    def assignment_receipt(self, principal: Principal, task_id: UUID) -> TaskAssignmentResult:
        """이미 만들어진 배정의 영수증. **돌려주기 전에 지금 그 업무를 읽을 수 있는지 다시 묻는다.**"""
        # 읽을 수 없으면 여기서 `TaskNotFound` 다 — 관계자 밖에는 존재도 알리지 않는다.
        self._tasks.get(principal, task_id)
        assignment = self._repository.current_assignment_for(task_id) or self._repository.pending_assignment_for(task_id)
        task = self._repository.task_by_id(task_id)
        if assignment is None or task is None:
            raise TaskNotFound("task assignment was not found")
        return self._view(assignment, task, principal)

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        """Assignments waiting for my acceptance (ERD work_inbox: 배정 수락)."""
        self._require(principal, TASK_READ)
        return [self._view(assignment, task, principal) for assignment, task in self._repository.pending_for(str(principal.id))]

    def sent(self, principal: Principal) -> list[TaskAssignmentResult]:
        self._require(principal, TASK_ASSIGN)
        return [self._view(assignment, task, principal) for assignment, task in self._repository.assigned_by(str(principal.id))]

    def accept(self, principal: Principal, assignment_id: UUID, *, expected_task_version: int | None = None) -> TaskAssignmentResult:
        self._require(principal, TASK_SELF_MANAGE)
        try:
            command = AssignmentAcceptCommand(assignment_id=assignment_id, expected_task_version=expected_task_version)
        except ValueError as error:
            raise TaskError(str(error)) from error
        assignment_id, expected_task_version = command.assignment_id, command.expected_task_version
        receipt = self._answer_receipt(principal, assignment_id, "active")
        if receipt is not None:
            return receipt
        assignment = self._pending_target(principal, assignment_id, expected_task_version, settled="active")
        if assignment is None:
            # 잠금 뒤에 이미 같은 답으로 닫혀 있었다 — 동시 재전송이다.
            return self._answer_receipt(principal, assignment_id, "active") or self._not_found()
        self._repository.decide(assignment, str(principal.id), "accept")
        return self._view(assignment, self._repository.task_for(assignment), principal)

    def _answer_receipt(self, principal: Principal, assignment_id: UUID, settled: str) -> TaskAssignmentResult | None:
        """**같은 사람의 같은 답을 다시 보낸 것인가** (SPEC-003 §3 S-16 · 「재전송이면 영수증」).

        담당 제안의 답은 행 자신이 갖고 있다: 수락은 `active`, 거절은 `declined`. 받는 사람이 같고
        그 행이 이미 그 답으로 닫혀 있으면 **두 번째 effect 없이** 현재를 돌려준다.

        이 판정이 회차 검사보다 **먼저** 와야 한다 — 수락은 Task 의 회차를 올리므로, 사람이 보던
        화면에서 그대로 다시 누른 요청은 「그때의 회차」를 싣고 온다. 그것을 stale 로 거절하면
        통신 재시도가 실패로 읽힌다. **다른 답**은 영수증이 아니다: 아래 `_pending_target` 이
        「지금 답할 회차가 아니다」로 가른다.

        돌려주기 전에 **지금의 열람 권한을 다시 검사한다** (K-2) — 잃었으면 존재를 숨긴다.
        """
        assignment = self._repository.assignment(assignment_id)
        if assignment is None or assignment.assignee_id != str(principal.id) or assignment.status != settled:
            return None
        if assignment.source_review_decision_id is None:
            # **그 사람이 실제로 답한 행이어야 영수증이다.** 관리자 배정은 답을 기다리지 않고 바로
            # `active` 로 서므로(BASE-002 O-28) 상태만 보면 「수락된 것」과 구별되지 않는다 —
            # 판단 행이 걸려 있는지가 그 차이다. 답한 적 없는 배정에 수락을 부르는 것은 재전송이
            # 아니라 **정의된 거절**이고, 거기서는 업무·담당·이력에 아무 변화가 없다.
            return None
        task = self._repository.task_for(assignment)
        if task is None:
            return None
        try:
            self._tasks.get(principal, task.id)
        except (TaskNotFound, TaskAccessDenied):
            raise TaskNotFound("task assignment was not found") from None
        return self._view(assignment, task, principal)

    def decline(self, principal: Principal, assignment_id: UUID, reason: str, *, expected_task_version: int | None = None) -> TaskAssignmentResult:
        self._require(principal, TASK_SELF_MANAGE)
        try:
            command = AssignmentDeclineCommand(assignment_id=assignment_id, expected_task_version=expected_task_version, reason=reason)
        except ValueError as error:
            raise TaskError(str(error)) from error
        assignment_id, expected_task_version = command.assignment_id, command.expected_task_version
        reason = command.reason
        if not reason.strip():
            raise TaskError("decline reason is required")
        receipt = self._answer_receipt(principal, assignment_id, "declined")
        if receipt is not None:
            return receipt
        assignment = self._pending_target(principal, assignment_id, expected_task_version, settled="declined")
        if assignment is None:
            return self._answer_receipt(principal, assignment_id, "declined") or self._not_found()
        self._repository.decide(assignment, str(principal.id), "reject", reason=reason.strip())
        return self._view(assignment, self._repository.task_for(assignment), principal)

    def cancel(self, principal: Principal, assignment_id: UUID) -> TaskAssignmentResult:
        """Withdraw a direct assignment before the assignee answers it.

        This is the requester's command, not a ReviewDecision by the assignee. Both paths lock the same assignment row,
        so acceptance and cancellation cannot both win when the clicks race.
        """
        self._require(principal, TASK_ASSIGN)
        assignment = self._repository.assignment(assignment_id)
        if assignment is None or assignment.assigned_by != str(principal.id) or assignment.assignment_kind != "direct":
            raise TaskNotFound("task assignment was not found")
        self._repository.task_by_id(assignment.task_id, lock=True)
        assignment = self._repository.assignment(assignment_id, lock=True)
        if assignment.status == "cancelled":
            return self._view(assignment, self._repository.task_for(assignment), principal)
        if assignment.status != "pending":
            raise TaskError("task assignment is no longer awaiting acceptance")
        self._repository.cancel(assignment, str(principal.id))
        return self._view(assignment, self._repository.task_for(assignment), principal)

    @staticmethod
    def _not_found() -> Any:
        raise TaskNotFound("task assignment was not found")

    def _pending_target(
        self,
        principal: Principal,
        assignment_id: UUID,
        expected_task_version: int | None = None,
        *,
        settled: str | None = None,
    ) -> Any:
        assignment = self._repository.assignment(assignment_id)
        if assignment is None:
            raise TaskNotFound("task assignment was not found")
        if assignment.assignee_id != str(principal.id):
            # **남의 답을 대신 낼 수 없고, 그 경계는 존재를 숨기는 404 다** — 보낸 사람에게도 같다
            # (SPEC-001 계승 · SPEC-003 Case Matrix `WORK_NOT_FOUND` 「없는 것과 못 읽는 것을 같은 말로
            # 답한다」). `WORK_ASSIGNMENT_RESPONDER_ONLY` 가 이 자리의 이름이지만, 그 코드가 요구하는
            # 것은 「수신자만 답한다」이지 **거절 사유를 더 알려 주는 것이 아니다.** 이미 좁은 쪽으로
            # 서 있는 기존 계약을 넓히지 않는다.
            raise TaskNotFound("task assignment was not found")
        # Reassignment also takes Task then assignment. Use the same order and
        # compare the version only after refreshing the locked Task.
        task = self._repository.task_by_id(assignment.task_id, lock=True)
        assignment = self._repository.assignment(assignment_id, lock=True)
        # **잠금 뒤에 다시 판정한다.** 같은 순간에 들어온 두 재전송은 잠금 **앞**에서 둘 다 아직
        # `pending` 인 행을 보므로 영수증 분기를 함께 빠져나간다 — 잠금에 걸렸다 풀려난 쪽이 그때
        # 비로소 「이미 닫혔다」를 본다. 그 자리에서 거절하면 정당한 재전송이 실패로 읽힌다.
        # 판단함 경유가 이 자리를 넘긴 방식과 같다(`…_replays_after_the_owner_lock`).
        if settled is not None and assignment.status == settled and assignment.source_review_decision_id is not None:
            return None
        if expected_task_version is not None and int(task.version) != expected_task_version:
            raise InvalidTaskTransition("task version is stale")
        if assignment.status != "pending":
            raise TaskError("task assignment is not awaiting acceptance")
        return assignment

    def assignments(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        """**현재 담당과 대기 제안을 각각** 낸다 (SPEC-003 §4 `GET /api/tasks/{id}/assignments`).

        둘을 한 값으로 합치지 않는다 — 담당 변경 대기 중에는 **둘 다 있고**, 화면이 기존 담당과 새 제안을
        함께 보여야 책임 공백이 없다는 사실이 읽힌다 (정책 V-18).
        """
        self._require(principal, TASK_READ)
        # 읽을 수 없는 업무면 여기서 끝난다 — 담당 이력은 업무를 읽는 사람만 읽는다.
        self._tasks.get(principal, task_id)
        task = self._repository.task_by_id(task_id)
        if task is None:
            raise TaskNotFound("task was not found")
        rows = self._repository.assignment_rows_for(task_id)
        current = next((row for row in rows if row.status == "active"), None)
        waiting = next((row for row in rows if row.status == "pending"), None)
        return {
            "task_id": str(task_id),
            "current": self._assignment_row(current) if current is not None else None,
            "pending": self._assignment_row(waiting) if waiting is not None else None,
            "history": [self._assignment_row(row) for row in rows],
        }

    @staticmethod
    def _assignment_row(assignment: Any) -> dict[str, Any]:
        return {
            "assignment_id": str(assignment.id),
            "assignment_kind": assignment.assignment_kind,
            "status": assignment.status,
            "assignee_id": assignment.assignee_id,
            "assigned_by": assignment.assigned_by,
            "decline_reason": assignment.decline_reason,
            "supersedes_assignment_id": str(assignment.supersedes_assignment_id) if assignment.supersedes_assignment_id else None,
            "created_at": _iso(assignment.created_at),
            "accepted_at": _iso(assignment.accepted_at),
            "declined_at": _iso(assignment.declined_at),
            "superseded_at": _iso(assignment.superseded_at),
        }

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    def _view(self, assignment: Any, task: Any, principal: Principal | None = None) -> TaskAssignmentResult:
        return {
            "assignment_id": str(assignment.id),
            "assignment_kind": assignment.assignment_kind,
            "status": assignment.status,
            "assignee_id": assignment.assignee_id,
            "assigned_by": assignment.assigned_by,
            "decline_reason": assignment.decline_reason,
            "created_at": _iso(assignment.created_at),
            "accepted_at": _iso(assignment.accepted_at),
            "declined_at": _iso(assignment.declined_at),
            "task": self._tasks._view(task, principal),
        }
