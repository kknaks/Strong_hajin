"""이미 들어와 있는 조직 위에, 그 조직이 실제로 하는 일과 같은 성격의 업무를 만든다.

The organization is real; this is not. Its purpose is to give the product something to be exercised against — AX
answering over work, a request travelling to a judgement, a meeting becoming a follow-up, a graph with enough in it
to read. Nothing here claims to be a record of anything that happened.

Every row goes through the product's own application command, as the person who would have done it. Nothing is
inserted behind the ledger's back, so actor, version, activity history and authorization are the real ones. That is
the point: a scenario built with raw inserts would exercise the tables and prove nothing about the system.

It is idempotent by title within one seed run: running it twice finds the work already there rather than making it
twice. `--clear` is deliberately not offered — the product has no bulk delete, and inventing one for a fixture would
be a way around the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal


@dataclass(slots=True)
class ScenarioResult:
    created: dict[str, int] = field(default_factory=dict)
    reused: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def track(self, kind: str, *, made: bool) -> None:
        target = self.created if made else self.reused
        target[kind] = target.get(kind, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {"created": dict(sorted(self.created.items())), "reused": dict(sorted(self.reused.items())), "skipped": self.skipped}


@dataclass(frozen=True, slots=True)
class Work:
    """업무 한 줄.

    스스로 든 일, 프로젝트에 쌓이는 일, 부분으로 갈라지는 일을 세 가지 모양으로 두지 않는다. 셋의 차이는
    `project`가 있느냐와 `parent`가 있느냐뿐이고, 나머지는 전부 같은 업무다. 한 표로 두면 사람이 편집할 때도
    그 차이만 보면 된다.

    `project`는 dataset이 만든 프로젝트의 외부 key이고, `parent`는 이 표 안에서 앞서 나온 업무의 key다.
    """

    key: str
    owner: str
    title: str
    description: str = ""
    starts_in: int | None = None
    days: int | None = None
    state: str = "open"
    project: str = ""
    parent: str = ""
    checklist: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Ask:
    """요청 하나. 받은 사람이 판단해야 업무가 된다 — 여기서는 만들기만 하고 판단은 남겨 둔다."""

    requester: str
    assignee: str
    title: str
    description: str
    due_in: int
    checklist: tuple[str, ...] = ()
    accept: bool = False
    #: 읽고 논의하되 판단하지 않는 사람들.
    cc: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Handout:
    """배정 하나. 요청과 다르다 — 위에서 내려오는 것이고, 받은 사람은 수락으로 자기 업무로 들인다."""

    assigner: str
    assignee: str
    title: str
    description: str
    starts_in: int
    days: int
    checklist: tuple[str, ...] = ()
    accept: bool = False


@dataclass(frozen=True, slots=True)
class Gathering:
    """회의 하나와 그 자리에서 적힌 것."""

    owner: str
    unit: str
    title: str
    starts_in: int
    minutes: int
    attendees: tuple[str, ...]
    note: str
    visibility: str = "public"


def build(application: Any, plan: "ScenarioPlan", *, today: date | None = None) -> ScenarioResult:
    """Run one plan against a live application. Each step is that person's own authorized command."""
    result = ScenarioResult()
    day = today or datetime.now(UTC).date()

    def acting(member_id: str) -> Principal | None:
        try:
            return application.authenticated_principal(member_id)
        except Exception:
            # 계획이 이 조직에 없는 사람을 가리키면 조용히 넘어가지 않고 남긴다.
            result.skipped.append(f"{member_id} · 이 조직에서 찾을 수 없거나 재직 중이 아닙니다")
            return None

    def existing(principal: Principal, title: str) -> dict[str, Any] | None:
        """이미 있는가. 두 번 돌려도 같은 업무를 두 번 만들지 않는다."""
        for task in application.list_tasks(principal, include_closed=True):
            if str(task.get("title")) == title:
                return task
        return None

    #: 이 실행에서 만들어졌거나 이미 있던 업무를, 계획이 쓴 key로 다시 찾을 수 있게 들고 간다.
    made: dict[str, dict[str, Any]] = {}
    for item in plan.work:
        principal = acting(item.owner)
        if principal is None:
            continue
        found = existing(principal, item.title)
        if found is not None:
            made[item.key] = found
            result.track("tasks", made=False)
            continue
        project_id = None
        if item.project:
            readable = [row for row in application.list_projects(principal) if row.get("external_key") == item.project]
            if not readable:
                result.skipped.append(f"업무 '{item.title}' · 프로젝트({item.project})를 찾을 수 없습니다")
                continue
            project_id = UUID(str(readable[0]["project_id"]))
        parent_id = None
        if item.parent:
            parent = made.get(item.parent)
            if parent is None:
                result.skipped.append(f"업무 '{item.title}' · 상위 업무({item.parent})가 앞에 없습니다")
                continue
            parent_id = UUID(str(parent["task_id"]))
        task = application.create_self_task(
            principal,
            item.title,
            description=item.description or None,
            start_date=day + timedelta(days=item.starts_in) if item.starts_in is not None else None,
            due_date=(
                day + timedelta(days=item.starts_in + item.days)
                if item.starts_in is not None and item.days is not None
                else None
            ),
            checklist=list(item.checklist) or None,
            project_id=project_id,
            parent_task_id=parent_id,
        )
        made[item.key] = task
        result.track("subtasks" if parent_id is not None else "tasks", made=True)
        _walk_to(application, principal, task, item.state, result)

    for item in plan.asks:
        requester = acting(item.requester)
        if requester is None:
            continue
        if any(str(row.get("title")) == item.title for row in application.list_work_requests(requester)):
            result.track("requests", made=False)
            continue
        try:
            request = application.create_work_request(
                requester,
                item.title,
                item.assignee,
                description=item.description,
                due_date=day + timedelta(days=item.due_in),
                checklist=list(item.checklist) or None,
                cc_member_ids=list(item.cc) or None,
            )
        except Exception as error:
            result.skipped.append(f"요청 '{item.title}' · {error}")
            continue
        result.track("requests", made=True)
        if not item.accept:
            continue
        assignee = acting(item.assignee)
        if assignee is None:
            continue
        from uuid import UUID

        try:
            application.accept_work_request(assignee, UUID(str(request["request_id"])), int(request["version"]))
            result.track("accepted_requests", made=True)
        except Exception as error:
            result.skipped.append(f"요청 수락 '{item.title}' · {error}")

    for item in plan.handouts:
        assigner = acting(item.assigner)
        if assigner is None:
            continue
        # 수락 전 배정은 받는 사람의 업무 목록에 아직 없다. 보낸 쪽 목록에서 찾아야 두 번 만들지 않는다.
        if any(str(row["task"]["title"]) == item.title for row in application.sent_task_assignments(assigner)):
            result.track("assignments", made=False)
            continue
        try:
            task = application.assign_task(
                assigner,
                item.title,
                item.assignee,
                description=item.description,
                start_date=day + timedelta(days=item.starts_in),
                due_date=day + timedelta(days=item.starts_in + item.days),
                checklist=list(item.checklist) or None,
            )
        except Exception as error:
            result.skipped.append(f"배정 '{item.title}' · {error}")
            continue
        result.track("assignments", made=True)
        if not item.accept:
            continue
        assignee = acting(item.assignee)
        if assignee is None:
            continue
        from uuid import UUID

        try:
            application.accept_task_assignment(assignee, UUID(str(task["assignment_id"])))
            result.track("accepted_assignments", made=True)
        except Exception as error:
            result.skipped.append(f"배정 수락 '{item.title}' · {error}")

    for item in plan.gatherings:
        principal = acting(item.owner)
        if principal is None:
            continue
        if any(str(row.get("title")) == item.title for row in application.list_meetings(principal)):
            result.track("meetings", made=False)
            continue
        started = datetime.combine(day + timedelta(days=item.starts_in), datetime.min.time(), tzinfo=UTC).replace(hour=1)
        try:
            meeting = application.create_meeting(
                principal,
                organization_id=item.unit,
                title=item.title,
                starts_at=started,
                ends_at=started + timedelta(minutes=item.minutes),
                visibility=item.visibility,
                attendee_ids=[member for member in item.attendees if member != item.owner],
            )
        except Exception as error:
            result.skipped.append(f"회의 '{item.title}' · {error}")
            continue
        result.track("meetings", made=True)
        from uuid import UUID

        try:
            note = application.create_meeting_note(principal, UUID(str(meeting["meeting_id"])), item.note)
            application.finalize_meeting_note(principal, UUID(str(meeting["meeting_id"])), int(note["version"]))
            result.track("meeting_notes", made=True)
        except Exception as error:
            result.skipped.append(f"회의록 '{item.title}' · {error}")

    return result


def _walk_to(application: Any, principal: Principal, task: dict[str, Any], target: str, result: ScenarioResult) -> None:
    """상태는 옮겨 적는 것이 아니라 그 사람이 실제로 옮긴다. 이력도 그렇게 남는다."""
    if target == "open":
        return
    from uuid import UUID

    from ax_workspace.modules.work.application import TaskState

    path = {"in_progress": ["in_progress"], "done": ["in_progress", "done"], "blocked": ["in_progress", "blocked"]}.get(target, [])
    task_id = UUID(str(task["task_id"]))
    version = int(task["version"])
    for step in path:
        try:
            moved = application.transition_task(task_id, principal, TaskState(step), expected_version=version)
            version = int(moved["version"])
        except Exception as error:
            result.skipped.append(f"업무 '{task['title']}' → {step} · {error}")
            return


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    work: tuple[Work, ...] = ()
    asks: tuple[Ask, ...] = ()
    handouts: tuple[Handout, ...] = ()
    gatherings: tuple[Gathering, ...] = ()
