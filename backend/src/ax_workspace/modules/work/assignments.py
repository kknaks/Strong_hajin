"""Manager-assigned Tasks: a TaskAssignment stays pending until the assignee accepts it (ERD TASK_ASSIGNMENT + acceptance ActionItem)."""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal, TASK_ASSIGN, TASK_READ, TASK_SELF_MANAGE
from ax_workspace.modules.work.application import InvalidTaskTransition, TaskAccessDenied, TaskApplication, TaskError, TaskNotFound, validate_schedule, clean_checklist, _clean_text, _iso


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
    def active_assignment_for(self, task_id: UUID, *, lock: bool = False) -> Any: ...
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
        tasks: Any = None,
        projects: ProjectAssigneePort | None = None,
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
        for row in self._projects.assignable_members(principal) if self._projects is not None else []:
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
    ) -> dict[str, Any]:
        """Create a Task for someone else. It enters their My Work only after they accept the assignment."""
        self._require(principal, TASK_ASSIGN)
        if not title.strip():
            raise TaskError("title is required")
        if assignee_id == str(principal.id):
            raise TaskError("use a self-owned task instead of assigning yourself")
        if not self._may_put_on(principal, assignee_id):
            raise TaskError("assignee is not within your assignment scope")
        validate_schedule(start_date, due_date)
        parent = self._tasks.parent_for(principal, parent_task_id) if parent_task_id is not None else None
        references = self._tasks._readable_tasks(principal, reference_task_ids) if self._tasks is not None else []
        task, assignment = self._repository.create_assigned_task(
            str(principal.id), assignee_id, title.strip(),
            description=_clean_text(description), start_date=start_date, due_date=due_date, causation_key=causation_key,
            checklist=clean_checklist(checklist),
            references=references,
            parent_task_id=parent.id if parent is not None else None,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
        )
        if parent is not None:
            self._tasks.record_subtask(principal, parent, task)
        return self._view(assignment, task)

    def plan_project_work(
        self,
        principal: Principal,
        project_id: UUID,
        title: str,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
    ) -> dict[str, Any]:
        """프로젝트 계획에 일을 올린다. 사람은 아직 정하지 않는다.

        무슨 일이 있는지와 누가 하는지는 다른 질문이다. 계획을 먼저 펼치고 사람을 나중에 붙이는 것이 프로젝트가
        움직이는 방식이며, 배정 행이 하나도 없다는 것이 곧 `담당자 미정`이다.
        """
        self._require(principal, TASK_ASSIGN)
        if self._projects is None or not self._projects.may_assign_in(principal, project_id):
            raise TaskError("이 프로젝트에 업무를 올릴 수 있는 자격이 없습니다")
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
        return TaskApplication._view(task)

    def reassign(self, principal: Principal, task_id: UUID, expected_version: int, assignee_id: str, reason: str | None = None) -> dict[str, Any]:
        """Put someone else on work that is already underway.

        Changing who holds the work is its own command, never a field on the Task edit form: it moves a relationship,
        and the person taking it on still gets to accept or decline.
        """
        self._require(principal, TASK_ASSIGN)
        # Take the Task row first: the version this command answers must not be read before someone else's move.
        task = self._repository.task_by_id(task_id, lock=True)
        if task is None:
            raise TaskNotFound("task was not found")
        current = self._repository.active_assignment_for(task_id, lock=True)
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
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
        return self._view(appended, task)

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        """Assignments waiting for my acceptance (ERD work_inbox: 배정 수락)."""
        self._require(principal, TASK_READ)
        return [self._view(assignment, task) for assignment, task in self._repository.pending_for(str(principal.id))]

    def sent(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, TASK_ASSIGN)
        return [self._view(assignment, task) for assignment, task in self._repository.assigned_by(str(principal.id))]

    def accept(self, principal: Principal, assignment_id: UUID) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        assignment = self._pending_target(principal, assignment_id)
        self._repository.decide(assignment, str(principal.id), "accept")
        return self._view(assignment, self._repository.task_for(assignment))

    def decline(self, principal: Principal, assignment_id: UUID, reason: str) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        if not reason.strip():
            raise TaskError("decline reason is required")
        assignment = self._pending_target(principal, assignment_id)
        self._repository.decide(assignment, str(principal.id), "reject", reason=reason.strip())
        return self._view(assignment, self._repository.task_for(assignment))

    def cancel(self, principal: Principal, assignment_id: UUID) -> dict[str, Any]:
        """Withdraw a direct assignment before the assignee answers it.

        This is the requester's command, not a ReviewDecision by the assignee. Both paths lock the same assignment row,
        so acceptance and cancellation cannot both win when the clicks race.
        """
        self._require(principal, TASK_ASSIGN)
        assignment = self._repository.assignment(assignment_id, lock=True)
        if assignment is None or assignment.assigned_by != str(principal.id) or assignment.assignment_kind != "direct":
            raise TaskNotFound("task assignment was not found")
        if assignment.status == "cancelled":
            return self._view(assignment, self._repository.task_for(assignment))
        if assignment.status != "pending":
            raise TaskError("task assignment is no longer awaiting acceptance")
        self._repository.cancel(assignment, str(principal.id))
        return self._view(assignment, self._repository.task_for(assignment))

    def _pending_target(self, principal: Principal, assignment_id: UUID) -> Any:
        assignment = self._repository.assignment(assignment_id, lock=True)
        if assignment is None or assignment.assignee_id != str(principal.id):
            raise TaskNotFound("task assignment was not found")
        if assignment.status != "pending":
            raise TaskError("task assignment is not awaiting acceptance")
        return assignment

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(assignment: Any, task: Any) -> dict[str, Any]:
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
            "task": TaskApplication._view(task),
        }
