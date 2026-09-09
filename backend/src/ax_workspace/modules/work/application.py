"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import (
    PROJECT_READ,
    WORK_READ_ALL,
    ACTION_READ,
    Principal,
    TASK_ASSIGN,
    TASK_READ,
    TASK_SELF_MANAGE,
    WORK_REQUEST_READ,
)


class TaskState(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    #: Handed over and waiting for the person who asked to say whether it is what they wanted. Not done.
    COMPLETION_SUBMITTED = "completion_submitted"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskError(Exception):
    pass


class TaskNotFound(TaskError):
    pass


class InvalidTaskTransition(TaskError):
    pass


class TaskAccessDenied(TaskError):
    pass


class TaskRepository(Protocol):
    def create_self_task(
        self,
        owner_id: str,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        source_action_item_id: UUID | None = None,
    ) -> Any: ...
    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def task_by_id(self, task_id: UUID) -> Any | None: ...
    def tasks_for(self, owner_id: str, *, include_closed: bool = False) -> list[Any]: ...
    def tasks_held_by_members(self, member_ids: frozenset[str], *, include_closed: bool = False) -> list[Any]: ...
    def tasks_in_projects(self, project_ids: frozenset[str], *, include_closed: bool = False) -> list[Any]: ...
    def touch(self, task: Any) -> None: ...
    def checklist_for(self, task_id: UUID, *, include_archived: bool = False) -> list[Any]: ...
    def checklist_progress_for(self, task_ids: list[UUID]) -> dict[UUID, tuple[int, int]]: ...
    def origin_facts(self, tasks: list[Any]) -> dict[UUID, dict[str, Any]]: ...
    def versions_for(self, task_id: UUID) -> list[Any]: ...
    def delivery_item(self, task: Any) -> Any: ...
    def delivery_submissions(self, item: Any) -> list[Any]: ...
    def delivery_snapshot(self, submission: Any) -> dict[str, Any]: ...
    def delivery_decisions(self, submission_ids: list[UUID]) -> list[Any]: ...
    def open_delivery_round(self, task: Any, reporter_id: str, reviewer_id: str, snapshot: dict[str, Any]) -> Any: ...
    def record_delivery_decision(
        self, submission: Any, actor_id: str, decision: str, *, reason: str | None = None, expected_version: int
    ) -> Any: ...
    def references_for(self, task_id: UUID, *, include_released: bool = False) -> list[Any]: ...
    def children_of(self, task_id: UUID) -> list[Any]: ...
    def open_children_of(self, task_id: UUID) -> list[Any]: ...
    def reference(self, task_id: UUID, reference_id: UUID) -> Any: ...
    def add_reference(self, task_id: UUID, referenced_task_id: UUID, created_by: str) -> Any: ...
    def release_reference(self, reference: Any, actor_id: str) -> None: ...
    def activity_for(self, task_id: UUID) -> list[Any]: ...
    def member_display_name(self, member_id: str) -> str | None: ...
    def add_checklist_item(self, task_id: UUID, text: str, created_by: str) -> Any: ...
    def checklist_item(self, task_id: UUID, item_id: UUID, *, lock: bool = False) -> Any: ...
    def archive_checklist_item(self, item: Any, actor_id: str) -> None: ...
    def reorder_checklist(self, items: list[Any], ordered_ids: list[UUID]) -> list[Any]: ...
    def record_activity(self, task: Any, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None, reason: str | None = None) -> None: ...
    def touch(self, task: Any) -> None: ...


class ActionSourcePort(Protocol):
    """The authorized Action lookup a Task's origin needs. Passing anything else is a type error, not a 500."""

    def action(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def subject_label(self, action: Any) -> str: ...


class WorkRequestSourcePort(Protocol):
    """The authorized WorkRequest lookup a Task's origin needs."""

    def list_for(self, principal_id: str) -> list[Any]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...


class MemberScopePort(Protocol):
    """Which people sit inside a set of organization units. Nothing else about them."""

    def member_ids_in(self, units: frozenset[str]) -> frozenset[str]: ...


class ProjectScopePort(Protocol):
    """어느 프로젝트를 이 사람이 읽을 수 있는가. 판정은 프로젝트 모듈 한 곳에만 있다."""

    def readable_project_ids(self, principal: Principal) -> frozenset[str]: ...


class TaskApplication:
    def __init__(
        self,
        repository: TaskRepository,
        requests: WorkRequestSourcePort | None = None,
        actions: ActionSourcePort | None = None,
        attachments: Any = None,
        member_scope: MemberScopePort | None = None,
        projects: ProjectScopePort | None = None,
    ) -> None:
        self.repository = repository
        # 어느 프로젝트에 일을 매달 수 있는지는 프로젝트 모듈이 답한다. 여기서 다시 계산하지 않는다.
        self._projects = projects
        # Resolving an organization-wide read to the people it covers; never used to widen anything else.
        self._member_scope = member_scope
        # Reading a Task's origin may need the resource behind it, always through that module's own authorized lookup.
        self._requests = requests
        self._actions = actions
        # Delivery reports name outputs that are already bound to this Task; nothing new is uploaded by reporting.
        self._attachments = attachments

    def create_self(
        self,
        principal: Principal,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        source_action_item_id: UUID | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        if not title.strip():
            raise TaskError("title is required")
        validate_schedule(start_date, due_date)
        parent = self.parent_for(principal, parent_task_id)
        project = self.project_for(principal, project_id, parent)
        task = self.repository.create_self_task(
            str(principal.id),
            title.strip(),
            causation_key,
            source_action_item_id=source_action_item_id,
            description=_clean_text(description),
            start_date=start_date,
            due_date=due_date,
            checklist=clean_checklist(checklist),
            # Pointers written with the work belong to its first version, so they are frozen with it.
            references=self._readable_tasks(principal, reference_task_ids),
            parent_task_id=parent.id if parent is not None else None,
            project_id=project,
        )
        if parent is not None:
            self.record_subtask(principal, parent, task)
        return self._view(task)

    def project_for(self, principal: Principal, project_id: UUID | None, parent: Any = None) -> UUID | None:
        """어느 프로젝트의 일로 둘 것인가. 하위 업무는 묻지 않고 상위 업무를 따른다.

        프로젝트를 말하지 않는 것이 정상이고, 말한다면 그 프로젝트를 읽을 수 있는 사람이어야 한다 — 읽을 수 없는
        프로젝트에 일을 밀어 넣어 그 프로젝트를 아는 사람들에게 보이게 할 수는 없다.
        """
        if parent is not None:
            return getattr(parent, "project_id", None)
        if project_id is None:
            return None
        readable = self._projects.readable_project_ids(principal) if self._projects is not None else frozenset()
        if str(project_id) not in readable:
            raise TaskNotFound("project was not found")
        return project_id

    def parent_for(self, principal: Principal, parent_task_id: UUID | None) -> Any | None:
        """The work this one is part of: readable by this person, still open, and not already a part of something.

        One level only for now, so a child never becomes a parent. Nothing is its own parent, and work that is over
        takes no new parts.
        """
        if parent_task_id is None:
            return None
        if not self._may_read(principal, parent_task_id):
            raise TaskNotFound("task was not found")
        parent = self.repository.task_by_id(parent_task_id)
        if parent is None:
            raise TaskNotFound("task was not found")
        if getattr(parent, "parent_task_id", None) is not None:
            raise TaskError("하위 업무 아래에 다시 하위 업무를 둘 수 없습니다")
        if TaskState(parent.state) in {TaskState.DONE, TaskState.CANCELLED}:
            raise TaskError("이미 끝난 업무에는 하위 업무를 추가할 수 없습니다")
        return parent

    def record_subtask(self, principal: Principal, parent: Any, child: Any) -> None:
        """Breaking work down changes the parent too, so the parent moves on and says what was added."""
        self._moved(parent)
        self.repository.record_activity(
            parent, str(principal.id), "task.subtask_added", f"하위 업무 추가: {child.title[:80]}"
        )

    def _readable_tasks(self, principal: Principal, task_ids: list[UUID] | None) -> list[UUID]:
        """Work this person may already open. Anything else is refused rather than quietly dropped."""
        wanted: list[UUID] = []
        for task_id in task_ids or []:
            identifier = task_id if isinstance(task_id, UUID) else UUID(str(task_id))
            if identifier in wanted:
                continue
            self.repository.task(identifier, str(principal.id))
            wanted.append(identifier)
        return wanted

    def update(
        self,
        task_id: UUID,
        principal: Principal,
        expected_version: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Owner-only field edits (title, description, schedule); no approval gate and no state change."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if TaskState(task.state) is TaskState.CANCELLED:
            raise TaskError("cancelled tasks cannot be edited")
        unknown = set(changes) - {"title", "description", "start_date", "due_date", "project_id"}
        if unknown:
            raise TaskError(f"unsupported task fields: {sorted(unknown)}")
        if "project_id" in changes:
            if getattr(task, "parent_task_id", None) is not None:
                # 하위 업무는 자기 프로젝트를 따로 갖지 않는다. 상위 업무가 옮겨 가면 함께 간다.
                raise TaskError("하위 업무의 프로젝트는 상위 업무를 따릅니다")
            wanted = changes["project_id"]
            task.project_id = self.project_for(principal, UUID(str(wanted))) if wanted else None
            for child in self.repository.children_of(task.id):
                child.project_id = task.project_id
        if "title" in changes:
            title = str(changes["title"] or "").strip()
            if not title:
                raise TaskError("title is required")
            task.title = title
        if "description" in changes:
            task.description = _clean_text(changes["description"])
        start_date = changes.get("start_date", task.start_date)
        due_date = changes.get("due_date", task.due_date)
        validate_schedule(start_date, due_date)
        task.start_date = start_date
        task.due_date = due_date
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task, str(principal.id), "task.updated", f"업무 내용 수정: {task.title} ({', '.join(sorted(changes))})",
            before_ref=f"task:{task.id}@{expected_version}",
        )
        return self._view(task)

    def list_for(
        self,
        principal: Principal,
        *,
        include_closed: bool = False,
        include_organization: bool = False,
    ) -> list[dict[str, Any]]:
        """The list carries the checklist count, not its items: enough for a progress cue, cheap enough for a table.

        `내 업무` is what this person holds and stays that way. The organization's work is a different question, asked
        by a different surface, and answered only for someone whose read authority covers it.
        """
        self._require(principal, TASK_READ)
        tasks = self.repository.tasks_for(str(principal.id), include_closed=include_closed)
        held = {task.id for task in tasks}
        if include_organization:
            organization = self._organization_scope_members(principal)
            if organization:
                tasks = tasks + [
                    task
                    for task in self.repository.tasks_held_by_members(organization, include_closed=include_closed)
                    if task.id not in held
                ]
                held |= {task.id for task in tasks}
            # 그리고 이 사람이 함께 하는 프로젝트의 업무. 담당자가 다른 부서 사람이어도 같은 프로젝트면 보인다.
            projects = self._project_scope(principal)
            if projects:
                tasks = tasks + [
                    task
                    for task in self.repository.tasks_in_projects(projects, include_closed=include_closed)
                    if task.id not in held
                ]
        progress = self.repository.checklist_progress_for([task.id for task in tasks])
        origins = self._origin_projection(principal, tasks)
        assignees = self._assignee_projection(tasks)
        views = []
        for task in tasks:
            done, total = progress.get(task.id, (0, 0))
            views.append(
                {
                    **self._view(task),
                    "checklist_progress": {"done": done, "total": total},
                    "origin": origins.get(task.id),
                    "assignee": assignees.get(task.id),
                }
            )
        return views

    def get(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        """The holder's workspace, or a read-only view for someone related through the Task's source.

        A requester is not the assignee: they may see the work their request produced, but reading it is not holding
        it. The read-only view carries no checklist and grants no command; the mutating routes keep their own guard.
        """
        self._require(principal, TASK_READ)
        try:
            return {**self._with_checklist(self.repository.task(task_id, str(principal.id)), principal), "access": "owner"}
        except TaskNotFound:
            return {**self._related_view(principal, task_id), "access": "read_only"}

    def _related_view(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        """Read-only access for the two relationships that earn it: the requester, and the assigner.

        Each needs its own capability as well as the relationship — reading someone else's work is never implied by
        holding a neighbouring resource.
        """
        task = self.repository.task_by_id(task_id)
        if task is None:
            raise TaskNotFound("task was not found")
        related = False
        if task.source_work_request_id is not None:
            related = task.source_work_request_id in self._readable_request_ids(principal, {task.source_work_request_id})
        if not related and TASK_ASSIGN in principal.capabilities:
            facts = self.repository.origin_facts([task]).get(task.id, {})
            related = facts.get("assignment_kind") == "direct" and facts.get("assigned_by") == str(principal.id)
        if not related and self._may_read_beyond_holding(principal, task):
            related = True
        if not related and getattr(task, "parent_task_id", None) is not None:
            # Whoever holds the whole, or put someone on it, may read its parts. Reading the parent through a
            # relationship does not reach inside it — how the holder broke the work up is their workspace, the same
            # way their checklist and materials are.
            related = self._manages(principal, task.parent_task_id)
        if not related:
            raise TaskNotFound("task was not found")
        # 안을 열어 주는 것이 아니라, 이미 따로 열 수 있는 것을 목록에서만 감추지 않는 것이다. 같은 프로젝트의
        # 하위 업무는 그 자체로 읽히므로, 여기서 숨기면 추적이 되지 않으면서 접근만 남는다. 읽을 수 없는 부분은
        # 여전히 이름도 개수도 나오지 않는다.
        children = [] if getattr(task, "parent_task_id", None) is not None else self._readable_children(principal, task)
        return {
            **self._view(task),
            "origin": self._origin_projection(principal, [task]).get(task.id),
            "assignee": self._assignee_projection([task]).get(task.id),
            "parent": self._parent_summary(principal, task),
            "children": children,
            "child_progress": {
                "done": sum(1 for row in children if row["state"] in {TaskState.DONE, TaskState.CANCELLED}),
                "total": len(children),
            },
            # The person who asked for the work may follow where their request got to, without holding the work.
            "delivery": self.delivery_view(principal, task),
        }

    def _organization_scope_members(self, principal: Principal) -> frozenset[str]:
        """The people whose work this person may read because of where their read authority reaches."""
        if self._member_scope is None or WORK_READ_ALL not in principal.capabilities:
            return frozenset()
        return self._member_scope.member_ids_in(principal.scope_for(WORK_READ_ALL))

    def _project_scope(self, principal: Principal) -> frozenset[str]:
        """업무를 읽게 해 주는 프로젝트 범위 — 실제로 그 프로젝트에 배정된 것만이다.

        프로젝트를 볼 수 있다는 것과 그 안의 업무를 읽는다는 것은 다르다. 팀장은 자기 팀이 소유한 프로젝트를
        찾고 관리할 수 있지만, 그 안의 일을 읽으려면 자기도 그 프로젝트에 붙어야 한다.
        """
        if PROJECT_READ not in principal.capabilities:
            return frozenset()
        return principal.projects_for(PROJECT_READ)

    def _may_read_beyond_holding(self, principal: Principal, task: Any) -> bool:
        """자기 것이 아닌 업무를 읽는 두 가지 길 — 조직 범위, 그리고 프로젝트 범위.

        한 자리에서 함께 판정한다. 두 곳에서 각자 판정하면 한쪽만 고쳤을 때 조용히 새거나 조용히 막힌다.
        """
        projects = self._project_scope(principal)
        if projects and str(getattr(task, "project_id", None) or "") in projects:
            return True
        members = self._organization_scope_members(principal)
        if not members:
            return False
        holder = self._assignee_projection([task]).get(task.id) or {}
        return str(holder.get("member_id") or task.created_by_actor_id) in members


    # ---- subtasks: the work inside this work ----

    def _manages(self, principal: Principal, task_id: UUID) -> bool:
        """Holding the work, or having put someone on it — the two ways of being responsible for it."""
        try:
            self.repository.task(task_id, str(principal.id))
            return True
        except TaskNotFound:
            pass
        if TASK_ASSIGN not in principal.capabilities:
            return False
        parent = self.repository.task_by_id(task_id)
        if parent is None:
            return False
        facts = self.repository.origin_facts([parent]).get(parent.id, {})
        return facts.get("assignment_kind") == "direct" and facts.get("assigned_by") == str(principal.id)

    def _may_read(self, principal: Principal, task_id: UUID) -> bool:
        """Whether this person may open that work at all — as its holder, or through a relationship that earns it."""
        try:
            self.get(principal, task_id)
        except (TaskNotFound, TaskAccessDenied):
            return False
        return True

    def _hierarchy_view(self, principal: Principal, task: Any) -> dict[str, Any]:
        """What this work is part of, and what is part of it — each read through the permission it needs.

        A child names its parent so the person holding it knows what it belongs to, and no more than that. A parent
        lists the children this person may read; a count of work they cannot see would be a side channel.
        """
        children = [] if getattr(task, "parent_task_id", None) is not None else self._readable_children(principal, task)
        return {
            "parent": self._parent_summary(principal, task),
            "children": children,
            "child_progress": {
                "done": sum(1 for row in children if row["state"] in {TaskState.DONE, TaskState.CANCELLED}),
                "total": len(children),
            },
        }

    def _readable_children(self, principal: Principal, task: Any) -> list[dict[str, Any]]:
        rows = []
        for child in self.repository.children_of(task.id):
            # Reading the parent is what earns a place in this list; nothing is counted that cannot be named.
            if not self._may_read(principal, child.id):
                continue
            rows.append(
                {
                    "task_id": str(child.id),
                    "title": child.title,
                    "state": child.state,
                    "due_date": _iso(child.due_date),
                    "assignee": self._assignee_projection([child]).get(child.id),
                }
            )
        return rows

    def _parent_summary(self, principal: Principal, task: Any) -> dict[str, Any] | None:
        parent_id = getattr(task, "parent_task_id", None)
        if parent_id is None:
            return None
        parent = self.repository.task_by_id(parent_id)
        if parent is None:
            return None
        # Enough to know what this work belongs to. Reading the parent itself still needs its own permission.
        return {"task_id": str(parent.id), "title": parent.title, "state": parent.state}

    # ---- delivery: reporting what was handed over, and the answer the person who asked gives ----

    def _require_children_finished(self, task: Any) -> None:
        """Work is not finished while its parts are not, and the refusal names what is still open."""
        remaining = self.repository.open_children_of(task.id)
        if remaining:
            names = ", ".join(str(child.title) for child in remaining[:3])
            raise InvalidTaskTransition(f"끝나지 않은 하위 업무가 있습니다: {names}")

    def requires_completion_review(self, task: Any) -> bool:
        """Work someone else asked for is finished when they say so, not when the holder says so."""
        return getattr(task, "source_work_request_id", None) is not None

    def submit_completion(
        self,
        principal: Principal,
        task_id: UUID,
        expected_version: int,
        *,
        summary: str,
        output_material_ids: list[UUID] | None = None,
    ) -> dict[str, Any]:
        """Hand the work over: freeze what was delivered and put it in front of the person who asked for it."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        if not self.requires_completion_review(task):
            raise TaskError("이 업무는 완료 보고 없이 바로 완료 처리합니다")
        if int(task.version) != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if TaskState(task.state) not in {TaskState.IN_PROGRESS, TaskState.BLOCKED}:
            raise InvalidTaskTransition("완료 보고는 진행 중인 업무에서만 할 수 있습니다")
        clean_summary = " ".join(str(summary or "").split())
        if not clean_summary:
            raise TaskError("완료 보고에는 결과 요약이 필요합니다")
        reviewer_id = self._requester_of(task)
        if reviewer_id is None:
            raise TaskError("이 업무의 요청자를 찾을 수 없습니다")
        snapshot = self._delivery_snapshot(principal, task, clean_summary[:2000], output_material_ids or [])
        submission = self.repository.open_delivery_round(task, str(principal.id), reviewer_id, snapshot)
        task.state = TaskState.COMPLETION_SUBMITTED
        task.block_reason = None
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task, str(principal.id), "task.completion_submitted", f"완료 보고: {clean_summary[:80]}"
        )
        return {**self._view(task), "delivery": self.delivery_view(principal, task)}

    def accept_delivery(self, principal: Principal, task: Any, submission: Any, expected_version: int) -> None:
        """The person who asked says this is what they wanted. Only this closes the work."""
        if TaskState(task.state) is not TaskState.COMPLETION_SUBMITTED:
            raise InvalidTaskTransition("확인할 완료 보고가 없습니다")
        self._require_children_finished(task)
        self.repository.record_delivery_decision(
            submission, str(principal.id), "accept", expected_version=expected_version
        )
        task.state = TaskState.DONE
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(task, str(principal.id), "task.completion_accepted", f"결과 완료 인정: {task.title}")

    def request_delivery_changes(self, principal: Principal, task: Any, submission: Any, expected_version: int, reason: str) -> None:
        """Say what is still missing. The work goes on; the question stays open for the next report."""
        clean = " ".join(str(reason or "").split())
        if not clean:
            raise TaskError("보완 요청에는 사유가 필요합니다")
        if TaskState(task.state) is not TaskState.COMPLETION_SUBMITTED:
            raise InvalidTaskTransition("확인할 완료 보고가 없습니다")
        self.repository.record_delivery_decision(
            submission, str(principal.id), "negotiate", reason=clean[:1000], expected_version=expected_version
        )
        task.state = TaskState.IN_PROGRESS
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task, str(principal.id), "task.completion_changes_requested", f"보완 요청: {clean[:80]}", reason=clean[:1000]
        )

    def delivery_view(self, principal: Principal, task: Any) -> dict[str, Any] | None:
        """Where this work stands with the person who asked for it, for anyone who may read the Task."""
        item = self.repository.delivery_item(task)
        if item is None:
            return None
        submissions = self.repository.delivery_submissions(item)
        if not submissions:
            return None
        latest = submissions[-1]
        decisions = self.repository.delivery_decisions([row.id for row in submissions])
        answered = [row for row in decisions if row.submission_id == latest.id]
        last = decisions[-1] if decisions else None
        status = (
            "awaiting_review"
            if not answered
            else "resolved"
            if any(row.decision == "accept" for row in answered)
            else "awaiting_revision"
        )
        return {
            "action_item_id": str(item.id),
            "status": status,
            "rounds": len(submissions),
            "reported_by": latest.submitted_by,
            "reported_at": _iso(latest.submitted_at),
            "summary": self.repository.delivery_snapshot(latest).get("summary"),
            "last_reason": last.reason if last is not None and last.decision != "accept" else None,
        }

    def _delivery_snapshot(self, principal: Principal, task: Any, summary: str, output_material_ids: list[UUID]) -> dict[str, Any]:
        """What was handed over, as it was: the Task's own words, its steps, and the outputs named by identity."""
        wanted = {str(item) for item in output_material_ids}
        selected = {}
        for binding, attachment in self._attachments.bindings_for("task", str(task.id)) if self._attachments else []:
            identifier = str(attachment.id)
            if binding.unbound_at is not None or identifier not in wanted:
                continue
            if identifier in selected and selected[identifier][0].role == "output":
                continue
            selected[identifier] = (binding, attachment)
        outputs = [
            {
                "material_id": str(attachment.id),
                "binding_id": str(binding.id),
                "attachment_id": str(attachment.id),
                "name": attachment.name,
                "kind": binding.role,
                "integrity_ref": attachment.integrity_ref,
            }
            for binding, attachment in selected.values()
        ]
        if len(outputs) != len(wanted):
            raise TaskError("이 업무에 없는 산출물은 완료 보고에 담을 수 없습니다")
        return {
            "summary": summary,
            "task_version": int(task.version),
            "task_title": task.title,
            "reported_at": _iso(datetime.now(UTC)),
            "checklist": [
                {"item_id": str(item.id), "text": item.text, "done": bool(item.done)}
                for item in self.repository.checklist_for(task.id)
            ],
            "outputs": outputs,
        }

    def _requester_of(self, task: Any) -> str | None:
        if self._requests is None or getattr(task, "source_work_request_id", None) is None:
            return None
        request = self._requests.request(task.source_work_request_id)
        return str(request.requester_id) if request is not None else None

    def transition(
        self,
        task_id: UUID,
        principal: Principal,
        target: TaskState,
        reason: str | None = None,
        expected_version: int = 0,
    ) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        allowed = {
            TaskState.OPEN: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
            TaskState.IN_PROGRESS: {TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELLED},
            TaskState.BLOCKED: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
            # Waiting for the person who asked: only their answer moves it on, so no command does from here.
            TaskState.COMPLETION_SUBMITTED: {TaskState.CANCELLED},
            # A mistaken completion can be reopened; cancellation stays terminal.
            TaskState.DONE: {TaskState.IN_PROGRESS},
        }
        if target is TaskState.DONE and self.requires_completion_review(task):
            raise InvalidTaskTransition("이 업무는 요청자의 확인이 필요합니다. 완료 보고로 제출하세요")
        if target is TaskState.DONE:
            self._require_children_finished(task)
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if target not in allowed.get(TaskState(task.state), set()):
            raise InvalidTaskTransition("task state transition is not allowed")
        if target is TaskState.BLOCKED and not (reason or "").strip():
            raise InvalidTaskTransition("block reason is required")
        previous_state = task.state
        task.state = target
        task.block_reason = reason.strip() if target is TaskState.BLOCKED else None
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task,
            str(principal.id),
            "task.state_changed",
            f"업무 상태 {previous_state} → {target.value}: {task.title}",
            before_ref=f"task:{task.id}@{expected_version}:{previous_state}",
            reason=task.block_reason,
        )
        return self._view(task)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    def history(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        """How this Task got to where it is, for anyone who may read the Task itself."""
        self._readable(principal, task_id)
        return {
            "task_id": str(task_id),
            "versions": [
                {
                    "version": int(row.version),
                    "change_kind": row.change_kind,
                    "actor_id": row.actor_id,
                    "reason": row.reason,
                    "captured_at": row.captured_at.isoformat(),
                    "snapshot": dict(row.snapshot),
                }
                for row in self.repository.versions_for(task_id)
            ],
            "activity": self._activity(task_id),
        }

    def _activity(self, task_id: UUID) -> list[dict[str, Any]]:
        """What happened, newest first, in the words the ledger already froze — never re-derived from the snapshots."""
        rows = [
            {
                "event_kind": row.event_kind,
                "actor": self._actor(row.actor_id),
                "actor_kind": row.actor_kind,
                "summary": row.safe_summary,
                "reason": row.reason,
                "version": _version_of(row.after_ref),
                "causation": _causation(getattr(row, "causation_ref", None)),
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in self.repository.activity_for(task_id)
        ]
        rows.sort(key=lambda row: (row["occurred_at"], row["version"] or 0), reverse=True)
        return rows

    def history_diff(self, principal: Principal, task_id: UUID, before: int, after: int) -> dict[str, Any]:
        """What changed between two versions. Only what actually moved is named."""
        self._readable(principal, task_id)
        frozen = {int(row.version): dict(row.snapshot) for row in self.repository.versions_for(task_id)}
        if before not in frozen or after not in frozen:
            raise TaskError("that version does not exist on this task")
        return {"task_id": str(task_id), "from": before, "to": after, "changes": _snapshot_diff(frozen[before], frozen[after])}

    def _moved(self, task: Any) -> None:
        """A change to what the Task contains moves the Task itself, so history has a version to freeze it at."""
        task.version += 1
        self.repository.touch(task)

    def _readable(self, principal: Principal, task_id: UUID) -> None:
        """History is read by exactly the people who may read the Task; it opens no new door."""
        self.get(principal, task_id)

    def _assignee_projection(self, tasks: list[Any]) -> dict[UUID, dict[str, str] | None]:
        """The person holding each Task right now, read from its active assignment rather than from any caller's list."""
        facts = self.repository.origin_facts(tasks)
        # No fallback: if no assignment is open, nobody holds it, and inventing a holder would be a guess.
        return {task.id: self._actor(facts.get(task.id, {}).get("assignee_id")) for task in tasks}

    def _origin_projection(self, principal: Principal, tasks: list[Any]) -> dict[UUID, dict[str, Any]]:
        """Where each Task came from, and which role that actor actually played.

        Requester, assigner, assignee and administrator are different things. A Task created by accepting a request
        names the requester even though the acceptance created the row; a directly assigned Task names the assigner,
        never the assignee; a self-created Task names its creator. An administrator capability is not provenance.

        The source resource is included only when this principal may read it. When it may not, the actor label still
        stands — that is the Task's own fact — but the source is withheld rather than partially disclosed.
        """
        facts = self.repository.origin_facts(tasks)
        readable_requests = self._readable_request_ids(principal, {fact["source_work_request_id"] for fact in facts.values()})
        projections: dict[UUID, dict[str, Any]] = {}
        for task in tasks:
            fact = facts.get(task.id, {})
            request_id = fact.get("source_work_request_id")
            if request_id is not None:
                kind, actor_role, actor_id = "work_request", "요청자", fact.get("request_requester_id")
                source = (
                    {"type": "work_request", "id": str(request_id), "title": fact.get("request_title")}
                    if request_id in readable_requests
                    else None
                )
            elif task.source_action_item_id is not None:
                # AX prepared it and the holder approved it: the proposal is worth naming, the holder is not a
                # counterpart to themselves.
                kind, actor_role, actor_id = "self_created", None, None
                source = self._action_item_source(principal, task.source_action_item_id)
            elif fact.get("assignment_kind") == "direct":
                kind, actor_role, actor_id, source = "direct_assignment", "배정자", fact.get("assigned_by"), None
            else:
                kind, actor_role, actor_id, source = "self_created", None, None, None
            if actor_id is None and source is None:
                # Nobody asked for it and nobody assigned it. There is no origin to state, and inventing one would
                # put a person in a role they never played.
                continue
            projections[task.id] = {
                "kind": kind,
                "actor_role": actor_role,
                "actor": self._actor(actor_id),
                "source": source,
            }
        return projections

    def _readable_request_ids(self, principal: Principal, request_ids: set[Any]) -> set[Any]:
        """Which of these requests this principal may actually read.

        Both halves are required. Holding the Task, or even being its assignee, is not permission to read the request
        behind it: that resource belongs to the request module, so its own capability must be held as well as a
        relationship to the request.
        """
        wanted = {request_id for request_id in request_ids if request_id is not None}
        if not wanted or self._requests is None or WORK_REQUEST_READ not in principal.capabilities:
            return set()
        member_id = str(principal.id)
        return {request.id for request in self._requests.list_for(member_id) if request.id in wanted}

    def _action_item_source(self, principal: Principal, action_item_id: Any) -> dict[str, Any] | None:
        """The AX proposal a Task came from, when this principal holds the action capability and owns that proposal."""
        if self._actions is None or ACTION_READ not in principal.capabilities:
            return None
        action = self._actions.action(action_item_id, str(principal.id))
        if action is None:
            return None
        # The label names the work that was created, not the internal confirmation wording.
        return {"type": "action_item", "id": str(action.id), "title": self._actions.subject_label(action)}

    def _actor(self, member_id: Any) -> dict[str, str] | None:
        if not member_id:
            return None
        return {"member_id": str(member_id), "display_name": self.repository.member_display_name(str(member_id)) or str(member_id)}

    # ---- checklist: the steps inside one Task ----

    def add_checklist_item(
        self, principal: Principal, task_id: UUID, text: str, *, expected_task_version: int | None = None
    ) -> dict[str, Any]:
        """Only the person who holds the Task may add a step, and the text must say something."""
        task = self._holding(principal, task_id, expected_task_version)
        cleaned = " ".join(text.split())
        if not cleaned:
            raise TaskError("checklist item text is required")
        item = self.repository.add_checklist_item(task.id, cleaned[:300], str(principal.id))
        self._moved(task)
        self.repository.record_activity(task, str(principal.id), "task.checklist.added", f"체크리스트 추가: {cleaned[:80]}")
        return _checklist_view(item, task)

    def update_checklist_item(
        self,
        principal: Principal,
        task_id: UUID,
        item_id: UUID,
        *,
        text: str | None = None,
        done: bool | None = None,
        expected_version: int | None = None,
        expected_task_version: int | None = None,
    ) -> dict[str, Any]:
        """Checking a step records who did it and when; unchecking clears those facts rather than keeping a stale actor."""
        task = self._holding(principal, task_id, expected_task_version)
        item = self.repository.checklist_item(task.id, item_id, lock=True)
        if item is None:
            raise TaskNotFound("checklist item was not found")
        # The guard is on the step, not the Task: two people checking two different steps are not in conflict.
        if expected_version is not None and int(item.version) != expected_version:
            raise TaskError("checklist item version is stale")
        if text is not None:
            cleaned = " ".join(text.split())
            if not cleaned:
                raise TaskError("checklist item text is required")
            before = item.text
            item.text = cleaned[:300]
            if before != item.text:
                item.version += 1
                self._moved(task)
                self.repository.record_activity(
                    task, str(principal.id), "task.checklist.edited", f"체크리스트 수정: {before[:40]} → {item.text[:40]}"
                )
        if done is not None and done != item.done:
            item.done = done
            item.completed_by = str(principal.id) if done else None
            item.completed_at = datetime.now(UTC) if done else None
            item.version += 1
            self._moved(task)
            self.repository.record_activity(
                task, str(principal.id), "task.checklist.checked" if done else "task.checklist.unchecked",
                f"체크리스트 {'완료' if done else '해제'}: {item.text[:80]}",
            )
        item.updated_at = datetime.now(UTC)
        return _checklist_view(item, task)

    def archive_checklist_item(
        self,
        principal: Principal,
        task_id: UUID,
        item_id: UUID,
        *,
        expected_version: int | None = None,
        expected_task_version: int | None = None,
    ) -> dict[str, Any]:
        """Take a step off the list without erasing that it was ever there."""
        task = self._holding(principal, task_id, expected_task_version)
        item = self.repository.checklist_item(task.id, item_id, lock=True)
        if item is None:
            raise TaskNotFound("checklist item was not found")
        if expected_version is not None and int(item.version) != expected_version:
            raise TaskError("checklist item version is stale")
        self.repository.archive_checklist_item(item, str(principal.id))
        self._moved(task)
        self.repository.record_activity(task, str(principal.id), "task.checklist.archived", f"체크리스트 정리: {item.text[:80]}")
        return _checklist_view(item, task)

    def reorder_checklist(
        self, principal: Principal, task_id: UUID, item_ids: list[UUID], *, expected_task_version: int | None = None
    ) -> dict[str, Any]:
        """Put the steps in the order the work happens. The whole order is rewritten, never one step nudged."""
        task = self._holding(principal, task_id, expected_task_version)
        items = self.repository.checklist_for(task.id)
        wanted = list(item_ids)
        if len(wanted) != len(set(wanted)) or {item.id for item in items} != set(wanted):
            raise TaskError("the new order must list every step on this checklist exactly once")
        ordered = self.repository.reorder_checklist(items, wanted)
        self._moved(task)
        self.repository.record_activity(
            task, str(principal.id), "task.checklist.reordered", f"체크리스트 순서 변경: {len(ordered)}단계"
        )
        return {"task_version": int(task.version), "checklist": [_checklist_view(item) for item in ordered]}

    def _holding(self, principal: Principal, task_id: UUID, expected_task_version: int | None) -> Any:
        """The Task this person may change, locked, with the version they answered checked before anything moves."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        if expected_task_version is not None and int(task.version) != expected_task_version:
            raise TaskError("task version is stale")
        return task

    def _with_checklist(self, task: Any, principal: Principal) -> dict[str, Any]:
        items = [_checklist_view(item) for item in self.repository.checklist_for(task.id)]
        return {
            **self._view(task),
            "checklist": items,
            "checklist_progress": {"done": sum(1 for item in items if item["done"]), "total": len(items)},
            "references": self.references(principal, task),
            "delivery": self.delivery_view(principal, task),
            **self._hierarchy_view(principal, task),
            "origin": self._origin_projection(principal, [task]).get(task.id),
            "assignee": self._assignee_projection([task]).get(task.id),
        }

    # ---- references: earlier work this Task points at ----

    def references(self, principal: Principal, task: Any) -> list[dict[str, Any]]:
        """Pointers this Task holds, each resolved now through the same read the referenced work itself requires.

        The pointer is a fact of this Task, so it stays visible; what it points at is shown only to someone who may
        already open that work. Nobody gains access by being pointed at.
        """
        return [
            {
                "reference_id": str(row.id),
                "created_by": row.created_by,
                "created_at": _iso(row.created_at),
                "task": self._referenced_view(principal, row.referenced_task_id),
            }
            for row in self.repository.references_for(task.id)
        ]

    def _referenced_view(self, principal: Principal, referenced_task_id: UUID) -> dict[str, Any] | None:
        try:
            referenced = self.repository.task(referenced_task_id, str(principal.id))
        except TaskNotFound:
            return None
        return {
            "task_id": str(referenced.id),
            "title": referenced.title,
            "state": referenced.state,
            "due_date": _iso(referenced.due_date),
            "assignee": self._assignee_projection([referenced]).get(referenced.id),
        }

    def add_reference(self, principal: Principal, task_id: UUID, referenced_task_id: UUID) -> dict[str, Any]:
        """Point at work that came before. Only at work this person may already read, and never at itself."""
        task = self._holding(principal, task_id, None)
        if referenced_task_id == task.id:
            raise TaskError("a task cannot refer to itself")
        # Reading it here is the permission check: work you cannot open is work you cannot point at.
        referenced = self.repository.task(referenced_task_id, str(principal.id))
        if any(row.referenced_task_id == referenced.id for row in self.repository.references_for(task.id)):
            raise TaskError("this task already refers to that work")
        record = self.repository.add_reference(task.id, referenced.id, str(principal.id))
        self._moved(task)
        self.repository.record_activity(
            task, str(principal.id), "task.reference_added", f"참고 업무 연결: {referenced.title[:80]}"
        )
        return {
            "reference_id": str(record.id),
            "created_by": record.created_by,
            "created_at": _iso(record.created_at),
            "task": self._referenced_view(principal, referenced.id),
            "task_version": int(task.version),
        }

    def release_reference(self, principal: Principal, task_id: UUID, reference_id: UUID) -> dict[str, Any]:
        """Stop pointing at it. The row closes rather than disappearing, so history still shows it was there."""
        task = self._holding(principal, task_id, None)
        record = self.repository.reference(task.id, reference_id)
        if record is None:
            raise TaskNotFound("reference was not found")
        referenced = self._referenced_view(principal, record.referenced_task_id)
        self.repository.release_reference(record, str(principal.id))
        self._moved(task)
        self.repository.record_activity(
            task, str(principal.id), "task.reference_released",
            f"참고 업무 해제: {(referenced or {}).get('title', '볼 수 없는 업무')[:80]}",
        )
        return {"reference_id": str(record.id), "task_version": int(task.version)}

    @staticmethod
    def _view(task: Any) -> dict[str, Any]:
        return {
            "task_id": str(task.id),
            "title": task.title,
            "state": task.state,
            "version": task.version,
            "block_reason": task.block_reason,
            "description": getattr(task, "description", None),
            "start_date": _iso(getattr(task, "start_date", None)),
            "due_date": _iso(getattr(task, "due_date", None)),
            "created_at": _iso(getattr(task, "created_at", None)),
            "updated_at": _iso(getattr(task, "updated_at", None)),
            "organization_unit_id": getattr(task, "organization_unit_id", None),
            # 어느 프로젝트의 일인가. 비어 있는 것이 정상이다.
            "project_id": _str(getattr(task, "project_id", None)),
            "origin_kind": getattr(task, "origin_kind", "direct"),
            "visibility": getattr(task, "visibility", "scope_default"),
            "assignment": _assignment_view(getattr(task, "assignments", None)),
            "lineage": {
                "request_thread_id": _str(getattr(task, "request_thread_id", None)),
                "source_work_request_id": _str(getattr(task, "source_work_request_id", None)),
                "source_decision_item_id": _str(getattr(task, "source_decision_item_id", None)),
                "source_submission_id": _str(getattr(task, "source_submission_id", None)),
                "source_review_decision_id": _str(getattr(task, "source_review_decision_id", None)),
                "source_action_item_id": _str(getattr(task, "source_action_item_id", None)),
                "source_task_id": _str(getattr(task, "source_task_id", None)),
            },
        }


def _assignment_view(assignments: Any) -> dict[str, Any] | None:
    if not assignments:
        return None
    current = assignments[-1]
    return {
        "assignment_id": str(current.id),
        "kind": current.assignment_kind,
        "status": current.status,
        "assigned_by": current.assigned_by,
        "accepted_at": _iso(current.accepted_at),
    }


#: A checklist written with the work itself. Bounded so a creation payload cannot become a data dump.
MAX_INITIAL_STEPS = 50


def clean_checklist(texts: Any) -> list[str]:
    """The steps someone actually wrote: blank lines are not steps, and the order is theirs."""
    if not texts:
        return []
    if isinstance(texts, str) or not isinstance(texts, (list, tuple)):
        raise TaskError("checklist must be a list of steps")
    cleaned = []
    for text in texts:
        step = " ".join(str(text).split())
        if step:
            cleaned.append(step[:300])
    if len(cleaned) > MAX_INITIAL_STEPS:
        raise TaskError(f"a new task can start with at most {MAX_INITIAL_STEPS} steps")
    return cleaned


def validate_schedule(start_date: date | None, due_date: date | None) -> None:
    if start_date is not None and due_date is not None and start_date > due_date:
        raise TaskError("start date cannot be later than the due date")


#: Snapshot fields compared as plain values; lists of things get their own comparison.
_DIFFABLE_FIELDS = ("title", "description", "state", "block_reason", "start_date", "due_date")


def _causation(causation_ref: str | None) -> dict[str, str] | None:
    """What carried a change here, as kind and id. Never a claim that something other than a person acted."""
    if not causation_ref or ":" not in causation_ref:
        return None
    kind, _, identifier = causation_ref.partition(":")
    return {"kind": kind, "id": identifier}


def _version_of(after_ref: str | None) -> int | None:
    """Ledger lines point at the version they produced (`task:<id>@<n>`), so a reader can open that snapshot."""
    if not after_ref or "@" not in after_ref:
        return None
    tail = after_ref.rsplit("@", 1)[1]
    return int(tail) if tail.isdigit() else None


def _snapshot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in _DIFFABLE_FIELDS:
        if before.get(field) != after.get(field):
            changes[field] = {"before": before.get(field), "after": after.get(field)}
    for field, key in (("checklist", "text"), ("materials", "attachment_id")):
        was = [row[key] for row in before.get(field) or []]
        now = [row[key] for row in after.get(field) or []]
        added = [item for item in now if item not in was]
        removed = [item for item in was if item not in now]
        if added or removed:
            changes[field] = {"added": added, "removed": removed}
    if (before.get("assignment") or {}).get("assignee_id") != (after.get("assignment") or {}).get("assignee_id"):
        changes["assignee"] = {
            "before": (before.get("assignment") or {}).get("assignee_id"),
            "after": (after.get("assignment") or {}).get("assignee_id"),
        }
    return changes


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _checklist_view(item: Any, task: Any = None) -> dict[str, Any]:
    """A step, and — when a change produced it — the Task version that change moved it to."""
    return {
        **({"task_version": int(task.version)} if task is not None else {}),
        "item_id": str(item.id),
        "text": item.text,
        "position": int(item.position),
        "done": bool(item.done),
        "state": item.state,
        "version": int(item.version),
        "created_by": item.created_by,
        "completed_by": item.completed_by,
        "completed_at": _iso(getattr(item, "completed_at", None)),
    }
