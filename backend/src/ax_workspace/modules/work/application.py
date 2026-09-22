"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

from ax_workspace.modules.work.task_results import TaskCompletionResult, TaskReferenceResult, TaskReferenceReleaseResult

from ax_workspace.modules.work.task_results import TaskDetailResult, TaskHistoryDiffResult, TaskHistoryResult, TaskListEntry
from ax_workspace.modules.work.task_results import (
    CalendarScheduleEntry,
    CalendarTaskRow,
    TaskDateMutationResult,
    TaskScheduleReleaseView,
    TaskScheduleView,
)

from ax_workspace.modules.work.errors import (
    CalendarRangeInvalid,
    InvalidTaskTransition,
    TaskApproverInvalid,
    TaskApproverLocked,
    TaskPredecessorCycle,
    TaskPredecessorDuplicate,
    TaskPredecessorProjectMismatch,
    TaskPredecessorProjectRequired,
    TaskPredecessorSelf,
    TaskPredecessorsUnfinished,
    TaskProjectLockedByPredecessors,
    TaskProposalNotPending,
    TaskProposalResponderOnly,
    TaskAccessDenied,
    TaskCancelRequiresAgreement,
    TaskChildrenUnfinished,
    TaskDirectNesting,
    TaskError,
    TaskNotFound,
    TaskParentClosed,
    TaskParentCycle,
    TaskParentUnassigned,
    TaskReopenForbidden,
    TaskReopenParentDone,
    TaskScheduleDayTaken,
    TaskScheduleForbidden,
    TaskScheduleInvalidRange,
    TaskScheduleOutOfRange,
    TaskScheduleOverlap,
    TaskScheduleTaskClosed,
    TaskScheduleTaskUnscheduled,
    TaskScheduleVersionConflict,
)
from ax_workspace.modules.time_blocks import TimeBlockRepository, office_span
from ax_workspace.modules.work.schedule import (
    TaskSpan,
    is_valid_time_range,
    is_within_span,
    release_reason_for,
    task_span,
)
from ax_workspace.modules.work.task_values import validate_schedule
# **상태 투영·기한 경과일은 순수 모듈이 갖는다** (SPEC-005 §4). 프로젝트 상세가 같은 판정을
# 지나야 하는데 `work/projects.py` 는 이 파일을 import 하지 않는다 — 규칙을 두 벌로 쓰지 않는다.
from ax_workspace.modules.work.task_projection import (
    external_state,
    overdue_days,
    today_for_tasks,
)

from datetime import UTC, date, datetime, time
from typing import Any, Protocol
from uuid import UUID
from ax_workspace.modules.work.task_creation import TaskCreateInput
from ax_workspace.modules.work.parties import may_read, party_of
from ax_workspace.modules.work.task_results import ChecklistMutationResult, ChecklistOrderResult, TaskMutationResult, TaskAssignmentView
from ax_workspace.modules.work.checklist_commands import ChecklistAddInput, ChecklistUpdateInput, ChecklistArchiveInput, ChecklistOrderInput
from ax_workspace.modules.work.task_commands import TaskEditFields, TaskCompletionInput, TaskReferenceCommand, TaskReferenceReleaseCommand

from ax_workspace.modules.work.checklist import ChecklistItem, UpdateChecklistItem, update_checklist_item
from ax_workspace.modules.work.lifecycle import (
    ChangeTaskState,
    Task,
    TaskCompletionContext,
    TaskPredecessorGate,
    TaskState,
    transition_task,
)
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



#: 닫힌 것이 없다. **날짜가 안 바뀐 명령도 이 묶음을 낸다** — 「말할 것이 없다」를
#: 화면이 읽을 수 있어야 한다 (증보 K3).
_NO_SCHEDULE_RELEASE: "TaskScheduleReleaseView" = {"released_count": 0, "reason": None}


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
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> Any: ...
    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def task_by_id(self, task_id: UUID, *, lock: bool = False) -> Any | None: ...
    def tasks_for(self, owner_id: str, *, include_closed: bool = False) -> list[Any]: ...
    def tasks_held_by_members(self, member_ids: frozenset[str], *, include_closed: bool = False) -> list[Any]: ...
    def tasks_in_projects(self, project_ids: frozenset[str], *, include_closed: bool = False) -> list[Any]: ...
    def checklist_for(self, task_id: UUID, *, include_archived: bool = False) -> list[Any]: ...
    def cc_member_ids(self, task_id: UUID) -> list[str]: ...
    def predecessors_for(self, task_ids: list[UUID]) -> dict[UUID, list[UUID]]: ...
    def active_predecessor_ids(self, task_id: UUID) -> list[UUID]: ...
    def predecessor_edges(self, task_ids: list[UUID]) -> dict[UUID, list[UUID]]: ...
    def replace_predecessors(self, task_id: UUID, wanted: list[UUID], actor_id: str) -> None: ...
    def cc_members_for(self, task_ids: list[UUID]) -> dict[UUID, list[str]]: ...
    def tasks_cc_for(self, member_id: str, *, include_closed: bool = False) -> list[Any]: ...
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
    #: 자손 **전부**. 직속만 도는 `children_of` 와 다른 질문이다 (SPEC-005 §4 · D-19).
    def descendants_of(self, task_id: UUID) -> list[Any]: ...
    def open_children_of(self, task_id: UUID) -> list[Any]: ...
    def tasks_requested_by(self, requester_id: str, *, include_closed: bool = False) -> list[Any]: ...
    def descendant_ids_of(self, task_ids: list[UUID]) -> set[UUID]: ...
    def tasks_by_ids(self, task_ids: set[UUID], *, include_closed: bool = False) -> list[Any]: ...
    def approval_rounds_for(self, task_ids: list[UUID]) -> dict[UUID, dict[str, Any]]: ...
    def children_map(self, task_ids: list[UUID]) -> dict[UUID, list[Any]]: ...
    def pending_proposal_kinds(self, task_ids: list[UUID]) -> dict[UUID, str]: ...
    def proposals_for(self, task_id: UUID) -> list[Any]: ...
    def proposal(self, task_id: UUID, proposal_id: UUID, *, lock: bool = False) -> Any: ...
    def open_proposal(self, task: Any, kind: str, proposed_by: str, payload: dict[str, Any] | None, reason: str | None) -> Any: ...
    def settle_proposal(self, proposal: Any, responder_id: str, state: str) -> None: ...
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


class TaskScheduleRepository(Protocol):
    """배정의 조회·쓰기. **조회는 기본으로 살아 있는 행만** 낸다 (`platform/task_schedules.py`)."""

    def active_on(self, task_id: UUID, on_date: date, *, lock: bool = False) -> Any | None: ...
    def schedule(self, schedule_id: UUID, *, lock: bool = False) -> Any | None: ...
    def in_range(self, task_ids: list[UUID], span_from: date, span_to: date) -> dict[UUID, list[Any]]: ...
    def active_for(self, task_id: UUID) -> list[Any]: ...
    def create(self, task_id: UUID, on_date: date, starts_at: time, ends_at: time) -> Any: ...
    def retime(self, schedule: Any, starts_at: time, ends_at: time) -> Any: ...
    def release(self, schedule: Any, reason: str) -> None: ...


class ActionSourcePort(Protocol):
    """The authorized Action lookup a Task's origin needs. Passing anything else is a type error, not a 500."""

    def action(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def subject_label(self, action: Any) -> str: ...


class WorkRequestSourcePort(Protocol):
    """The authorized WorkRequest lookup a Task's origin needs."""

    def list_for(self, principal_id: str) -> list[Any]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def settle_by_agreement(self, request_id: UUID, actor_id: str, *, reason: str | None = None) -> None: ...


class MemberDirectoryPort(Protocol):
    """「이 사람이 지금 활동 중인 구성원인가」 — 참조자를 받을 때 묻는 **유일한** 질문이다.

    요청 쪽 `WorkRequestAssigneeDirectory.is_active_member` 와 같은 판정이다. 내 업무의 참조자만
    다른 기준으로 거르면 같은 이름이 한쪽에서는 서고 한쪽에서는 서지 않는다.
    """

    def is_active_member(self, principal: Principal, member_id: str) -> bool: ...


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
        directory: MemberDirectoryPort | None = None,
        schedules: TaskScheduleRepository | None = None,
        time_blocks: TimeBlockRepository | None = None,
    ) -> None:
        self.repository = repository
        # 시간 배정 — **업무의 자식**이라 자기 가시성 규칙을 갖지 않는다 (SPEC-004 §4 Data Contract).
        self._schedules = schedules
        # 겹침을 읽는 **문 하나**. 두 표를 함께 조회한다 — 회의 쪽도 같은 문을 쓴다 (증보 K22).
        self._time_blocks = time_blocks
        # 참조자로 적힌 사람이 실제로 있는 구성원인가. 요청이 쓰는 것과 같은 명부다.
        self._directory = directory
        # **선행 요약이 자기 자신을 다시 부르는 것을 막는 빗장** (`predecessor_views` 참조).
        self._resolving_predecessor_access = False
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
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        project_id: UUID | None = None,
        cc_member_ids: list[str] | None = None,
        preceding_task_ids: list[UUID] | None = None,
        approver_id: str | None = None,
    ) -> TaskMutationResult:
        self._require(principal, TASK_SELF_MANAGE)
        try:
            command = TaskCreateInput(title=title, description=description, start_date=start_date, due_date=due_date, checklist=checklist, reference_task_ids=reference_task_ids, parent_task_id=parent_task_id, project_id=project_id, cc_member_ids=cc_member_ids, preceding_task_ids=preceding_task_ids, approver_id=approver_id).for_owner(str(principal.id))
        except ValueError as error:
            raise TaskError(str(error)) from error
        title, description, start_date, due_date = command.title, command.description, command.start_date, command.due_date
        checklist, reference_task_ids = command.checklist, command.reference_task_ids
        parent_task_id, project_id = command.parent_task_id, command.project_id
        cc_member_ids = self._active_cc_members(principal, command.cc_member_ids)
        # 본인 업무라 **만드는 사람이 곧 드는 사람**이다 — 중심 업무 판정이 그 값을 읽는다.
        parent = self.parent_for(principal, parent_task_id, assignee_id=str(principal.id))
        project = self.project_for(principal, project_id, parent)
        # **아직 없는 업무라 자기 자신도 순환도 성립할 수 없다** — `task_id=None` 이 그 사실이다.
        # 프로젝트는 상위를 따라 정해진 뒤의 값으로 본다: 하위가 상위 프로젝트를 물려받는 그 값이
        # 선행의 「같은 프로젝트」 기준이어야 둘이 어긋나지 않는다.
        predecessors = self._resolve_predecessors(
            principal, command.preceding_task_ids, project_id=project, task_id=None
        )
        # 담당자는 본인이므로 **본인을 승인자로 둘 수 없다** — 자기 일을 자기가 확인하는 자리를 만들지 않는다.
        approver = self._valid_approver(principal, command.approver_id, assignee_id=str(principal.id))
        task = self.repository.create_self_task(
            str(principal.id),
            title,
            causation_key,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
            description=description,
            start_date=start_date,
            due_date=due_date,
            checklist=checklist,
            # Pointers written with the work belong to its first version, so they are frozen with it.
            references=self._readable_tasks(principal, reference_task_ids),
            parent_task_id=parent.id if parent is not None else None,
            project_id=project,
            # 참조자는 **읽기와 논의만** 연다 (`modules/work/parties.py`). 담당도 수행도 옮기지 않는다.
            cc_member_ids=cc_member_ids,
            approver_id=approver,
        )
        if predecessors:
            # **검사와 저장이 한 transaction** 이다 (§5 동시성) — 조립 층이 이 호출 전체를 한 session 에 싣는다.
            self.repository.replace_predecessors(task.id, predecessors, str(principal.id))
        if parent is not None:
            self.record_subtask(principal, parent, task)
        return self._view(task, principal, cc_member_ids=cc_member_ids)

    def valid_approver(self, principal: Principal, approver_id: str | None, *, assignee_id: str | None) -> str | None:
        """요청 발송이 부르는 **얇은 문** (`RequestParentPort`). 판정은 아래 한 곳 그대로다.

        요청 갈래도 같은 규칙을 지나야 하므로 규칙을 복제하지 않는다 — 갈래가 정하는 것은
        **누구를 담당으로 놓고 묻는가** 하나뿐이다.
        """
        return self._valid_approver(principal, approver_id, assignee_id=assignee_id)

    def _valid_approver(self, principal: Principal, approver_id: str | None, *, assignee_id: str | None) -> str | None:
        """승인자(화면 라벨 「결재자」)로 설 수 있는 사람인가 — **0..1 · 재직 중 · 담당자 본인 불가**
        (SPEC-001 §4 Validation · DEC-001 D-9).

        0..1 은 필드가 하나라는 사실이 이미 답한다. 재직 여부는 참조자와 **같은 명부**에 묻는다 —
        한쪽만 다른 기준으로 거르면 같은 이름이 한 칸에서는 서고 다른 칸에서는 서지 않는다.
        """
        wanted = (approver_id or "").strip() or None
        if wanted is None:
            return None
        if assignee_id is not None and wanted == str(assignee_id):
            raise TaskApproverInvalid("담당자 본인은 승인자가 될 수 없습니다")
        if self._directory is not None and not self._directory.is_active_member(principal, wanted):
            raise TaskApproverInvalid("승인자를 다시 선택해 주세요")
        return wanted

    def _active_cc_members(self, principal: Principal, member_ids: list[str]) -> list[str]:
        """참조자로 세울 사람들. **없는 이름은 조용히 버리지 않고 거절한다** — 요청 쪽과 같은 규칙이다.

        조용히 버리면 보낸 사람 화면에서 참조자가 사라진 것을 아무도 모르고, 그 사람은 자기가 읽을 수
        있다고 믿는 업무를 영원히 못 연다.
        """
        if not member_ids:
            return []
        if self._directory is None:  # pragma: no cover - 조립 층이 언제나 명부를 붙인다
            return list(member_ids)
        for member_id in member_ids:
            if not self._directory.is_active_member(principal, member_id):
                raise TaskError(f"참조자 {member_id} 는 활동 중인 구성원이 아닙니다")
        return list(member_ids)

    def creation_receipt(self, principal: Principal, task_id: UUID) -> TaskMutationResult:
        """이미 만들어진 업무의 영수증 — **생성이 냈던 것과 같은 투영**이다.

        돌려주기 전에 `get` 으로 지금 읽을 수 있는지 다시 묻는다. 잃었으면 존재를 숨긴다.
        """
        self.get(principal, task_id)
        task = self.repository.task_by_id(task_id)
        if task is None:  # pragma: no cover - `get` 이 먼저 거른다
            raise TaskNotFound("task was not found")
        return self._view(task, principal)

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

    def parent_for(
        self,
        principal: Principal,
        parent_task_id: UUID | None,
        *,
        assignee_id: str | None = None,
        child_task_id: UUID | None = None,
    ) -> Any | None:
        """이 일이 **무엇의 일부인가.** 읽을 수 있고, 아직 끝나지 않았고, 순환하지 않아야 한다.

        **저장 깊이에 제한이 없다** (정책 V-6). 예전에는 「하위 아래에 하위를 둘 수 없다」로 한 단계에서
        끊었고, 그래서 받은 일을 다시 나눌 수가 없었다 — 일이 실제로 갈라지는 모양과 어긋났다.
        화면이 두 단계만 보이는 것은 **표시**의 일이고 저장과 무관하다 (정책 L-11).

        대신 **중심 업무 판정**이 그 자리를 대신한다 (SPEC-003 §5): 같은 사람의 직접 작업을 무한히
        겹치는 것만 막는다. `assignee_id` 는 **만들어질 하위를 들 사람**이고, 그 사람이 상위를 든 사람과
        같으면 「직접 작업」이다.
        """
        if parent_task_id is None:
            return None
        if not self._may_read(principal, parent_task_id):
            raise TaskNotFound("task was not found")
        parent = self.repository.task_by_id(parent_task_id)
        if parent is None:
            raise TaskNotFound("task was not found")
        if child_task_id is not None and (
            child_task_id == parent.id or child_task_id in self._ancestor_ids(parent)
        ):
            # 자기 자신이나 자기 조상을 부모로 두면 트리가 닫힌 고리가 된다 (작업계획서 §4.5).
            raise TaskParentCycle("자기 자신이나 상위 업무를 하위로 둘 수 없습니다")
        if TaskState(parent.state) in {TaskState.DONE, TaskState.CANCELLED}:
            raise TaskParentClosed("이미 끝난 업무에는 하위 업무를 추가할 수 없습니다")
        self._require_may_hold_children(parent, assignee_id)
        return parent

    def _requested_ancestor(self, principal: Principal, task: Any) -> bool:
        """이 업무의 조상 중에 **내가 요청자인 업무**가 있는가 (정책 V-21).

        **요청자 본인만이다** — 승격 요청이면 누른 사람도 그 자리에 선다(BASE-002 O-31, 요청자 전용
        조작이 이미 그렇게 판정한다). 수신자·참조자(cc)는 여기 들어가지 않는다: V-21 이 새로 여는 것은
        「요청자는 자신이 요청한 Task 와 그 하위 트리 전체를 읽는다」이고, **한 겹짜리 cc 읽기가 하위
        전체와 그 자료까지 자동으로 포함한다는 계약은 없다.** 부모 요청 자체의 기존 cc 읽기는 그대로
        살아 있다 — 그 사실 하나로 새 하위를 열지 않을 뿐이다.

        다른 권한(조직 범위·프로젝트·직접 배정)으로 읽히는 하위는 이 함수와 무관하게 그대로 읽힌다.

        자기 자신은 위에서 이미 봤으므로 여기서는 조상만 걷는다. `_ancestor_ids()` 는 본 것을 다시 보지
        않으므로 원장이 어긋나 고리가 생겨 있어도 멈춘다.
        """
        if self._requests is None or WORK_REQUEST_READ not in principal.capabilities:
            return False
        member_id = str(principal.id)
        for task_id in self._ancestor_ids(task):
            node = self.repository.task_by_id(task_id)
            request_id = getattr(node, "source_work_request_id", None) if node is not None else None
            if request_id is None:
                continue
            request = self._requests.request(request_id)
            if request is None:
                continue
            if member_id in {
                str(request.requester_id),
                str(getattr(request, "promoted_by_member_id", None) or ""),
            }:
                return True
        return False

    def _ancestor_ids(self, task: Any) -> set[UUID]:
        """이 업무 위로 이어진 상위 전부. 깊이 제한이 없으므로 **본 것을 다시 보면 멈춘다** — 원장이
        어긋나 고리가 생겨 있어도 여기서 무한히 걷지 않는다."""
        seen: set[UUID] = set()
        current = getattr(task, "parent_task_id", None)
        while current is not None and current not in seen:
            seen.add(current)
            node = self.repository.task_by_id(current)
            current = getattr(node, "parent_task_id", None) if node is not None else None
        return seen

    def _holder_of(self, task: Any) -> str | None:
        """지금 그 업무를 든 사람. 수락 대기·담당 없는 업무에서는 `None` 이고, 그것이 사실이다."""
        return (self._assignee_projection([task]).get(task.id) or {}).get("member_id")

    def _is_central_task(self, task: Any) -> bool:
        """**중심 업무인가** — 부모가 없거나, **부모를 든 사람이 이 업무를 든 사람과 다르면** 그렇다.

        「부모가 없으면 중심 업무」로 판정하지 않는다 (정책 V-7 · P-3). 다른 사람이 수락한 요청 업무는
        그 사람의 새 중심 업무이고, 그래서 자기 직속 하위와 하위 요청을 가질 수 있다.
        """
        parent_id = getattr(task, "parent_task_id", None)
        if parent_id is None:
            return True
        parent = self.repository.task_by_id(parent_id)
        if parent is None:
            return True
        return self._holder_of(parent) != self._holder_of(task)

    def _require_may_hold_children(self, parent: Any, assignee_id: str | None) -> None:
        """그 상위 아래에 이 사람의 일을 둘 수 있는가.

        두 거절이 여기서 나오고 **둘 다 새 규칙이 아니다** — V-7·V-8 과 「활성 담당자가 만든다」에서
        그대로 나온다 (SPEC-003 §4 Validation).
        """
        holder = self._holder_of(parent)
        if holder is None:
            # 아직 아무도 들지 않은 일 아래에는 하위를 만들 수 없다. 중심 업무 판정이 **부모를 든 사람**을
            # 읽는데 그 값이 없고, 하위를 만들 수 있는 사람도 그 활성 담당자이기 때문이다. 수락하면 열린다.
            raise TaskParentUnassigned("아직 수락되지 않은 업무에는 하위 업무를 만들 수 없습니다. 수락 후에 다시 시도하세요")
        if assignee_id is not None and assignee_id == holder and not self._is_central_task(parent):
            # 같은 사람의 직접 작업 아래 직접 작업 — 중심 업무의 직속으로만 둘 수 있다 (정책 V-8).
            raise TaskDirectNesting("직접 작업은 중심 업무의 바로 아래에만 둘 수 있습니다")

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
    ) -> TaskDateMutationResult:
        """Owner-only field edits (title, description, schedule); no approval gate and no state change."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if TaskState(task.state) is TaskState.CANCELLED:
            raise TaskError("cancelled tasks cannot be edited")
        try:
            changes = TaskEditFields.model_validate(changes).changes()
        except ValueError as error:
            raise TaskError(str(error)) from error
        if "project_id" in changes:
            if getattr(task, "parent_task_id", None) is not None:
                # 하위 업무는 자기 프로젝트를 따로 갖지 않는다. 상위 업무가 옮겨 가면 함께 간다.
                raise TaskError("하위 업무의 프로젝트는 상위 업무를 따릅니다")
            # **남은 선행이 있으면 프로젝트를 바꿀 수 없다** (SPEC-001 §4 Validation · U-13).
            # 선행이 「같은 프로젝트 안」이라는 불변을 프로젝트 쪽에서 깨는 길이라 여기서 막는다.
            # 선행 배열을 같은 명령으로 비우는 길은 열려 있다 — 아래에서 먼저 비운 뒤 다시 부르면 된다.
            self._require_project_unlocked(task)
            # **자손 전체가 따라간다 — 자식만이 아니다** (SPEC-005 §4 · D-19 · BASE-004 어긋남 ③).
            # 직속만 옮기면 **손자가 옛 프로젝트에 남고**, 선행은 같은 프로젝트 안에서만 성립하므로
            # 저장소가 강제하는 불변식이 데이터 안에서 깨진다.
            descendants = self.repository.descendants_of(task.id)
            for descendant in descendants:
                # **하위가 상위를 따라 옮겨 가는 경로에도 같은 규칙이 걸린다.** 여기를 비워 두면
                # 상위를 옮기는 것만으로 하위의 선행이 다른 프로젝트로 끌려간다.
                # **게이트를 전부 먼저 건 뒤에 옮긴다** — 걸으면서 옮기면 자손 하나가 잠겨 있을 때
                # **부분 이동**이 남는다. 거절이면 아무것도 움직이지 않는다.
                self._require_project_unlocked(descendant)
            wanted = changes["project_id"]
            task.project_id = self.project_for(principal, UUID(str(wanted))) if wanted else None
            for descendant in descendants:
                descendant.project_id = task.project_id
        if "approver_id" in changes:
            # **`승인 대기` 뒤에는 아무도 못 바꾼다** (§5 권한). 이미 그 사람 앞에 판단이 놓였다.
            if self._approval_state(task) == "awaiting_review":
                raise TaskApproverLocked("승인 대기 중에는 승인자를 바꿀 수 없습니다")
            task.approver_id = self._valid_approver(
                principal, changes["approver_id"], assignee_id=self._holder_of(task)
            )
        if "preceding_task_ids" in changes:
            # **배열 전체 교체**다 (SPEC-001 §4). 프로젝트 기준은 이 명령이 끝난 뒤의 값이어야 하므로
            # 위에서 이미 반영된 `task.project_id` 를 읽는다 — 한 명령이 둘을 함께 바꿀 수 있다.
            wanted_predecessors = self._resolve_predecessors(
                principal,
                [UUID(str(item)) for item in changes["preceding_task_ids"]],
                project_id=getattr(task, "project_id", None),
                task_id=task.id,
            )
            # **검사와 저장이 한 덩어리다** — 이 호출과 위의 순환 검사가 같은 transaction 안이다.
            self.repository.replace_predecessors(task.id, wanted_predecessors, str(principal.id))
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
        # **D1 의 첫 자리** (SPEC-004 §5). 날짜가 실제로 바뀐 경우에만 배정을 검증해 밖인 것을 닫는다 —
        # 새 날짜를 먼저 싣고 나서 묻는다. **닫기와 이 수정이 한 transaction** 이다.
        dates_before = (task.start_date, task.due_date)
        task.start_date = start_date
        task.due_date = due_date
        released = self._schedule_release_for(task, dates_before)
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task, str(principal.id), "task.updated", f"업무 내용 수정: {task.title} ({', '.join(sorted(changes))})",
            before_ref=f"task:{task.id}@{expected_version}",
        )
        return {**self._view(task, principal), "schedule_release": released}

    def _require_project_unlocked(self, task: Any) -> None:
        """선행이 남아 있으면 그 업무의 프로젝트는 잠겨 있다 (`WORK_PROJECT_LOCKED_BY_PREDECESSORS`).

        **닫힌 관계는 세지 않는다** — 뗀 선행은 행으로 남지만 잠그지 않는다.
        """
        if self.repository.active_predecessor_ids(task.id):
            raise TaskProjectLockedByPredecessors(
                f"선행업무를 먼저 비워야 프로젝트를 바꿀 수 있습니다: {task.title}"
            )

    # ---- D1 — 업무의 날짜가 바뀌는 **세 자리**가 함께 부르는 자리 (SPEC-004 §5 · 증보 K3) ----

    def _release_schedules_outside(self, task: Any) -> TaskScheduleReleaseView:
        """새 기간 밖의 배정을 닫는다. **부르는 쪽이 「날짜가 바뀌었다」를 이미 판정한 뒤**다.

        **공통 지점 `repository.touch()` 에 걸지 않는다** — 날짜와 무관한 변경에도 아홉 번 불리므로
        거기 걸면 「날짜가 바뀌었나」를 이 함수가 스스로 판정해야 하고 **틀리면 조용히 안 돈다** (§J).
        그래서 세 자리가 **명시적으로** 부른다.

        **검증과 저장이 한 transaction 에 있다** — 조립 층이 명령 하나를 한 session 에 싣는다.
        나뉘면 「배정은 닫혔는데 업무 날짜는 안 바뀐」 상태가 **복구 경로 없이** 남는다.

        **사유는 둘뿐이다.** 날짜를 전부 지웠으면 `task_dates_cleared`, 기간은 있는데 그 밖이면
        `out_of_range` 다 — 한쪽만 지운 것은 남은 한쪽을 그 날 하루로 읽으므로 **여전히 기간이 있고**
        `out_of_range` 로 간다 (증보 K7). 그래서 셋째 사유가 생기지 않는다.
        """
        span = task_span(task.start_date, task.due_date)
        schedules = self._schedule_repository()
        doomed = [row for row in schedules.active_for(task.id) if not is_within_span(span, row.on_date)]
        if not doomed:
            return _NO_SCHEDULE_RELEASE
        reason = release_reason_for(span)
        for row in doomed:
            schedules.release(row, reason)
        return {"released_count": len(doomed), "reason": reason}

    def _schedule_release_for(
        self, task: Any, before: tuple[date | None, date | None]
    ) -> TaskScheduleReleaseView:
        """**날짜가 실제로 바뀌었을 때만** 검증을 돌린다.

        안 바뀌었으면 저장소를 건드리지 않고 `0` 건을 낸다 — **0 을 내는 것도 계약이다** (증보 K3).
        """
        if (task.start_date, task.due_date) == before:
            return _NO_SCHEDULE_RELEASE
        return self._release_schedules_outside(task)

    # ---- 시간 배정 — 날짜 단위로 사는 업무 위에 얹히는 시간 축 (SPEC-004) ----

    def _schedule_repository(self) -> TaskScheduleRepository:
        if self._schedules is None:  # pragma: no cover - 조립이 빠진 경우에만 닿는다
            raise TaskError("시간 배정 저장소가 조립되지 않았습니다")
        return self._schedules

    def _time_block_repository(self) -> TimeBlockRepository:
        if self._time_blocks is None:  # pragma: no cover - 조립이 빠진 경우에만 닿는다
            raise TaskError("시간 블록 조회가 조립되지 않았습니다")
        return self._time_blocks

    def _require_free_time(
        self, principal: Principal, on_date: date, starts_at: time, ends_at: time, *, ignoring: UUID | None = None
    ) -> None:
        """그 시간이 **비어 있나** — 배정 생성·시각 변경이 함께 지나는 **한 자리** (증보 K22).

        **나의 시간만 본다.** 남의 일정은 애초에 조회 대상이 아니다 — 회의 쪽의 「주최자 + 참석자
        전원」은 Phase BE-4 이고, 같은 문(`overlapping_blocks`)에 `member_ids` 를 더 넣는 것으로 된다.

        **반열림 `[시작, 끝)`** 이라 경계가 닿는 것은 통과한다 — 11:00 에 끝나는 일정이 있어도
        11:00 시작 배정이 선다. 그 판정은 이 함수가 아니라 **문 안에 있다**: 두 자리에 있으면
        두 규칙이 된다.

        `ignoring` 은 **고치는 중인 그 배정 자신**이다. 빼지 않으면 10:00–11:00 을 10:30–11:30 으로
        옮기는 것이 자기와 겹쳐 거절된다.

        **검사와 저장이 한 트랜잭션에 있다** — 조립 층이 명령 하나를 한 session 에 싣는다. 그래도
        **틈이 남는다**: 두 표에 걸쳐 있어 제약을 걸 수 없으므로 동시 요청 둘이 각각 통과할 수 있다.
        **「하루 한 칸」과 같은 세기의 보장이 아니다** (SPEC §2.9).
        """
        blocks = self._time_block_repository().overlapping_blocks(
            frozenset({str(principal.id)}),
            office_span(on_date, starts_at, ends_at),
            ignore_schedule_id=ignoring,
        )
        if blocks:
            raise TaskScheduleOverlap("이미 다른 일정이 있는 시간입니다")

    def _assignable_task(self, principal: Principal, task_id: UUID, *, lock: bool = False) -> Any:
        """배정 쓰기가 지나는 문 — 판정은 **그 업무의 활성 담당 관계**다 (증보 K5).

        `task.self_manage` 봉투로 판정하지 않는다. 그것은 **전역 권한 비트 하나**라 업무별 담당 관계와
        무관하고, 봉투만 검사하면 **읽을 수 있는 남의 업무에도 배정이 생긴다**.

        **읽을 수 있으나 담당이 아니면 403, 읽을 수 없으면 404** 다 (증보 K6). 읽을 수 있다는 것은
        존재를 이미 안다는 뜻이라, 그것을 404 로 숨기면 「내 화면에 떠 있는 업무인데 없다고 한다」가 된다.

        **`TASK_SELF_MANAGE` 문은 그 앞에 그대로 선다** (증보 K16). K5 의 「판정은 담당 관계이고
        봉투가 아니다」는 **봉투로 판정하지 말라**는 뜻이지 **봉투를 걷으라**는 뜻이 아니다 —
        역량 문과 관계 검사는 대체재가 아니라 **겹겹**이고, 내 업무를 내가 다루는 다른 명령
        (`create_self`·`update`·`transition`)이 전부 같은 문을 지난다.
        """
        self._require(principal, TASK_SELF_MANAGE)
        try:
            return self.repository.task(task_id, str(principal.id), lock=lock)
        except TaskNotFound:
            if self.may_read_task(principal, task_id):
                raise TaskScheduleForbidden("내가 맡은 업무에만 시간을 배정할 수 있습니다") from None
            raise

    def create_schedule(
        self, principal: Principal, task_id: UUID, *, on_date: date, starts_at: time, ends_at: time
    ) -> TaskScheduleView:
        """배정 생성 — **생성 전용이다** (증보 K10). `expected_version` 을 받지 않는다.

        **그 날에 살아 있는 배정이 이미 있으면 거절한다** — 덮어쓰지 않는다. 그 날의 시각을 바꾸는 것은
        `retime_schedule` 의 일이다. 회차를 「그 날에 배정이 있을 때만 필수」로 두면 **잃은 갱신이
        열린다**: 서버가 「몰라서 뺐다」와 「알고 뺐다」를 구분할 수 없어 불변식이 화면에 의존하게 된다.

        **멱등 키는 이 자리에 없다** — 같은 키의 재전송은 영수증이고, 그 판정은 생성 원장을 가진
        `TaskCreationApplication` 이 이 명령보다 **먼저** 한다 (증보 K12).
        """
        task = self._assignable_task(principal, task_id, lock=True)
        if TaskState(task.state) in {TaskState.DONE, TaskState.CANCELLED}:
            raise TaskScheduleTaskClosed("끝난 업무에는 시간을 배정할 수 없습니다")
        if not is_valid_time_range(starts_at, ends_at):
            raise TaskScheduleInvalidRange("종료 시각은 시작 시각보다 뒤여야 합니다")
        span = task_span(task.start_date, task.due_date)
        if span is None:
            raise TaskScheduleTaskUnscheduled("먼저 업무 기간을 정해 주세요. 기간이 있어야 시간을 배정할 수 있습니다")
        if not span.covers(on_date):
            # **정규화 구간을 적는다** (증보 K11·K14) — 뒤집힌 업무면 `start_date`~`due_date` 가 아니다.
            raise TaskScheduleOutOfRange(
                f"이 업무의 기간({span.span_from.isoformat()}~{span.span_to.isoformat()}) 안에만 "
                "시간을 배정할 수 있습니다"
            )
        schedules = self._schedule_repository()
        if schedules.active_on(task.id, on_date) is not None:
            # 사람에게 이유를 말하기 위한 질문이다. **동시 두 명령을 가르는 것은 부분 unique** 다.
            raise TaskScheduleDayTaken("이 날에는 이미 시간 배정이 있습니다")
        # **겹침은 마지막 409 다** — 영수증은 이 명령보다 먼저 지났으므로(증보 K12) 재전송이 자기 자신과
        # 겹쳐 거절되는 일이 없다. 뺄 자기 자신도 없다: 같은 (업무, 날) 은 위 줄이 이미 막았다.
        self._require_free_time(principal, on_date, starts_at, ends_at)
        return _schedule_view(schedules.create(task.id, on_date, starts_at, ends_at))

    def retime_schedule(
        self, principal: Principal, schedule_id: UUID, *, expected_version: int, starts_at: time, ends_at: time
    ) -> TaskScheduleView:
        """시각 변경 — **같은 날 재배정도 여기로 온다** (증보 K1·K10).

        **날짜는 못 바꾼다** (§2.3 R6). **회차는 무조건 필수**이고 그 주인은 **이 배정 자신**이다
        (증보 K8) — 업무 회차를 올리면 다른 화면의 낙관적 잠금이 멋대로 깨진다.
        **닫힌 배정은 존재를 숨긴다.**
        """
        schedules = self._schedule_repository()
        schedule = schedules.schedule(schedule_id, lock=True)
        if schedule is None:
            raise TaskNotFound("task schedule was not found")
        self._assignable_task(principal, schedule.task_id)
        if schedule.version != expected_version:
            raise TaskScheduleVersionConflict("다른 곳에서 먼저 바뀌었습니다")
        if not is_valid_time_range(starts_at, ends_at):
            raise TaskScheduleInvalidRange("종료 시각은 시작 시각보다 뒤여야 합니다")
        # **날짜는 그대로**이므로 같은 날의 다른 일정과만 겹칠 수 있다 — 그리고 **자기 자신은 뺀다.**
        self._require_free_time(principal, schedule.on_date, starts_at, ends_at, ignoring=schedule.id)
        return _schedule_view(schedules.retime(schedule, starts_at, ends_at))

    def schedule_receipt(self, principal: Principal, task_id: UUID, on_date: date) -> TaskScheduleView:
        """같은 멱등 키의 재전송이 돌려받는 **영수증** — 두 번째 effect 없이 지금의 배정을 낸다.

        **돌려주기 전에 지금의 권한을 다시 검사한다** (SPEC-003 K-2 계승). 하루 한 칸이므로
        (업무, 날) 하나가 배정 하나를 가리킨다 — 그 사이에 닫혔다면 **없는 것으로 답한다**.
        """
        self._assignable_task(principal, task_id)
        schedule = self._schedule_repository().active_on(task_id, on_date)
        if schedule is None:
            raise TaskNotFound("task schedule was not found")
        return _schedule_view(schedule)

    def calendar_tasks(self, principal: Principal, span_from: date, span_to: date) -> list[CalendarTaskRow]:
        """합본 조회의 업무 절반 — 축은 `my_work` 다 (§2.7 · 증보 K13).

        **기존 조회를 재사용한다.** 새 질의를 직접 쓰면 「이 사람이 그 업무를 열 수 있는가는 한 자리에서만
        답한다」가 깨진다. `my_work` 는 `include_closed=False` 로 내부 `DONE`·`CANCELLED` 를 이미
        거르고 **`COMPLETION_SUBMITTED` 는 남긴다** — 완료 보고를 냈지만 아직 승인 전인 일은
        캘린더에 서 있어야 한다 (§C).

        **기간으로 업무를 거르지 않는다** — 좌측 레일이 기간 없는 업무도 들어야 날짜부터 정할 수 있다
        (R2). 기간이 거르는 것은 `schedules[]` 뿐이다.

        **역량 문과 관계 검사는 겹겹이다** (증보 K16). 행 집합을 좁히는 것은 활성 담당 관계지만,
        `TASK_READ` 문은 그것과 별개로 지난다 — 같은 파일의 다른 읽기 표면 열둘이 그 문을 지나고,
        여기만 빼 두면 **그 역량이 없는 역할이 생기는 순간 이 표면만 샌다.**
        """
        self._require(principal, TASK_READ)
        if span_from > span_to:
            raise CalendarRangeInvalid("조회 기간의 시작이 끝보다 뒤일 수 없습니다")
        tasks = self.repository.tasks_for(str(principal.id), include_closed=False)
        grouped = self._schedule_repository().in_range([task.id for task in tasks], span_from, span_to)
        # **승인 회차는 한 번에 묻는다** (증보 K19). 줄마다 물으면 좌측 레일 한 판이 업무 수만큼
        # 질의를 낸다 — 목록 표면이 쓰는 `approval_rounds_for` 를 그대로 재사용한다.
        # **부모 것만 묻는다.** 목록 쪽은 「막는 하위」까지 세려고 하위 id 를 함께 싣지만,
        # 캘린더 행이 내는 것은 **그 업무 자신의 승인 여부 한 값**이다.
        rounds = self.repository.approval_rounds_for([task.id for task in tasks])
        rows: list[CalendarTaskRow] = []
        for task in tasks:
            span = task_span(task.start_date, task.due_date)
            rows.append(
                {
                    "kind": "task",
                    "task_id": str(task.id),
                    "title": task.title,
                    "state": _external_state(task.state),
                    # **판정은 목록·상세와 같은 규칙이다** — 같은 함수를 지난다. 어휘도 저장소 것 그대로다.
                    "approval": self._approval_from_rounds(task, rounds.get(task.id)),
                    "start_date": _iso(task.start_date),
                    "due_date": _iso(task.due_date),
                    "span_from": _span_end(span, "span_from"),
                    "span_to": _span_end(span, "span_to"),
                    "version": task.version,
                    "schedules": [_schedule_entry(row) for row in grouped.get(task.id, [])],
                }
            )
        return rows

    def my_work(self, principal: Principal, *, include_closed: bool = False) -> list[TaskListEntry]:
        """**지금 이 사람이 활성 담당으로 들고 있는 것만.**

        요청 관계로 읽는 업무는 여기 서지 않는다 — 내가 남에게 부탁한 일은 **읽을 수 있는 일**이지
        **내가 하는 일**이 아니다. 그 둘을 한 목록에 담으면 「내 업무」가 내 일이 아닌 것으로 채워진다.
        """
        return self._list(principal, include_closed=include_closed, include_organization=False)

    def readable_tasks(self, principal: Principal, *, include_closed: bool = False) -> list[TaskListEntry]:
        """이 사람이 **읽을 수 있는** 업무 전부 — 조직 범위·프로젝트 범위, 그리고 요청 관계.

        자료 검색·그래프·권한 판정이 이 답을 쓴다. 「내 업무」와 다른 질문이다.
        """
        return self._list(
            principal,
            include_closed=include_closed,
            include_organization=True,
            include_requested=True,
            include_cc=True,
        )

    def _list(
        self,
        principal: Principal,
        *,
        include_closed: bool = False,
        include_organization: bool = False,
        include_requested: bool = False,
        include_cc: bool = False,
    ) -> list[TaskListEntry]:
        """The list carries the checklist count, not its items: enough for a progress cue, cheap enough for a table.

        `내 업무` is what this person holds and stays that way. The organization's work is a different question, asked
        by a different surface, and answered only for someone whose read authority covers it.
        """
        self._require(principal, TASK_READ)
        tasks = self.repository.tasks_for(str(principal.id), include_closed=include_closed)
        held = {task.id for task in tasks}
        # **읽기의 세 번째 길 — 요청 관계** (정책 V-21). 내가 부탁한 일과 **그 아래 전부**를 읽는다.
        # 깊이 제한이 없다: 받은 사람이 다시 나눈 것까지 따라가 확인할 수 있어야 한다.
        # 이 길이 여기 있어야 하는 이유는 하나 더 있다 — 자료 쪽이 묻는 `may_read_task()` 가 이 목록으로
        # 답한다. 상세만 열어 주고 목록에 넣지 않으면 「상세는 보이는데 파일은 못 연다」가 된다.
        # **목록과 상세가 같은 문에서 열린다.** 이 길은 요청 관계로 여는 것이므로 요청을 읽을 역량이
        # 있어야 한다 — 없으면 목록에는 제목·id 가 나오는데 상세는 404 인 경계 불일치가 생긴다
        # (`_requested_ancestor` 가 같은 역량을 본다). 조직 범위·프로젝트·활성 담당으로 읽는 길은
        # 이 역량과 무관하게 그대로 산다.
        requested = (
            self.repository.tasks_requested_by(str(principal.id), include_closed=include_closed)
            if include_requested and WORK_REQUEST_READ in principal.capabilities
            else []
        )
        roots = [task.id for task in requested]
        if roots:
            subtree = self.repository.descendant_ids_of(roots) - held - set(roots)
            tasks = tasks + [task for task in requested if task.id not in held]
            held |= {task.id for task in requested}
            if subtree:
                tasks = tasks + [
                    task
                    for task in self.repository.tasks_by_ids(subtree, include_closed=include_closed)
                    if task.id not in held
                ]
                held |= {task.id for task in tasks}
        # **읽기의 네 번째 길 — 참조자(cc).** 목록과 상세가 같은 문에서 열려야 한다: 상세만 열어 주면
        # 「상세는 보이는데 그 업무의 자료는 못 연다」가 된다 (`may_read_task()` 가 이 목록으로 답한다).
        #
        # 참조는 **한 겹이다** — 하위 트리를 함께 열지 않는다. 요청자의 V-21 과 달리 cc 는 그 업무 하나를
        # 참조로 받은 것이고, 받은 사람이 그 아래를 어떻게 나눴는지는 그 사람의 작업 공간이다. 그리고
        # 「내 업무」에는 서지 않는다 — 참조로 받은 일은 **읽을 수 있는 일**이지 내가 하는 일이 아니다
        # (요청 관계와 같은 결이고, `my_work()` 가 이 플래그를 켜지 않는 이유다).
        if include_cc:
            for task in self.repository.tasks_cc_for(str(principal.id), include_closed=include_closed):
                if task.id in held:
                    continue
                tasks = tasks + [task]
                held.add(task.id)
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
        # 목록 한 줄마다 같은 질의를 반복하지 않는다 — 파생 표시를 한 번에 계산해 나눠 싣는다.
        derived = self._derived_for(tasks, readable_ids={task.id for task in tasks})
        cc = self.repository.cc_members_for([task.id for task in tasks])
        preceding = self.repository.predecessors_for([task.id for task in tasks])
        views = []
        for task in tasks:
            done, total = progress.get(task.id, (0, 0))
            views.append(
                {
                    **self._view(
                        task,
                        derived=derived.get(task.id),
                        cc_member_ids=cc.get(task.id, []),
                        preceding_task_ids=preceding.get(task.id, []),
                    ),
                    "checklist_progress": {"done": done, "total": total},
                    "origin": origins.get(task.id),
                    "assignee": assignees.get(task.id),
                }
            )
        return views

    def get(self, principal: Principal, task_id: UUID) -> TaskDetailResult:
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
            # **참조자(cc) 는 그 업무를 읽는다** — 자기 이름이 적힌 그 한 건만이다. 자리가 여는 것은
            # 읽기와 논의뿐이고, 수정·시작·완료는 그대로 막힌다 (`modules/work/parties.py`).
            related = may_read(self._party_of(principal, task))
        if not related:
            # **요청자는 자기가 부탁한 업무의 하위 트리 전체를 읽는다 — 깊이 제한이 없다** (정책 V-21).
            # 받은 사람이 그 일을 다시 나눴을 때 요청자가 「필요하면 그 업무로 들어가 확인한다」가 원문이고,
            # 한 겹만 인정하면 상세는 열리는데 그 아래가 통째로 사라진다. 조상 중 하나라도 내가 요청한
            # 업무면 그 아래는 그 요청의 일부다. **읽기만 넓어지고 수정·시작·완료는 그대로 막힌다.**
            related = self._requested_ancestor(principal, task)
        if not related:
            raise TaskNotFound("task was not found")
        return {
            **self._view(task, principal),
            "origin": self._origin_projection(principal, [task]).get(task.id),
            "assignee": self._assignee_projection([task]).get(task.id),
            **self._hierarchy_view(principal, task),
            # The person who asked for the work may follow where their request got to, without holding the work.
            "delivery": self.delivery_view(principal, task),
            "predecessors": self._predecessor_summary(principal, task),
            # **프로젝트에 붙은 사람이면 남의 업무에서도 체크리스트를 읽는다** (SPEC-005 §4 · D-29).
            #
            # **접근 값을 셋으로 늘리지 않고 싣는 조건만 따로 둔다** (§4 ⓑ). 그 값은 이미 밖으로 나가는
            # 계약이라 값을 늘리면 읽는 곳 전부가 함께 움직이고, 이 변경이 필요한 것보다 넓어진다.
            # 그래서 **`read_only` 인데 항목이 실리는 조합**이 생기고 **그것이 정상**이다 —
            # 접근 값은 이제 **쓰기 범위**만 뜻한다.
            #
            # **읽기만 넓어진다**: 더하고 체크하고 지우고 순서를 바꾸는 가드(`_holding`)는 그대로
            # 활성 담당자다. **프로젝트 밖에서 조직 축으로 읽는 갈래는 전과 같이 항목이 없다** —
            # 그 갈래의 미터는 프로젝트 상세 `tasks[]` 의 집계가 그린다.
            **(self._checklist_fields(task) if self._on_that_tasks_project(principal, task) else {}),
        }

    # ---- derived: 서버가 만드는 파생 표시 (SPEC-003 §4 Data) ----

    def _derived_for(
        self,
        tasks: list[Any],
        facts: dict[UUID, dict[str, Any]] | None = None,
        *,
        principal: Principal | None = None,
        readable_ids: set[UUID] | None = None,
    ) -> dict[UUID, dict[str, Any]]:
        """한 벌의 업무에 실릴 `derived` 묶음 — **어느 값도 `state` 에서 읽지 않는다.**

        각 값은 자기 원장에서 나온다: 기다림은 담당 행에서, 확인은 판단 행에서, 제안은 제안 표에서,
        막는 하위는 하위의 완결 판정에서. `state` 를 되짚으면 「완료 보고 제출」과 「최종 완료」가
        같은 글자를 쓰는 순간 둘을 가를 수 없게 된다.

        `reply`·`status_note` 는 **키만 있고 값은 `null` 이다.** SPEC-001 에서 이어받은 이름이지만
        그 원장(문의·회신 대기·상태 메모)이 이 코드에 아직 없다 — 없는 것을 있다고 내지 않는다.
        값을 내는 것은 후속 구현이다.
        """
        if not tasks:
            return {}
        ids = [task.id for task in tasks]
        facts = facts if facts is not None else self.repository.origin_facts(tasks)
        children = self.repository.children_map(ids)
        shown = {
            task.id: self._shown_children(children.get(task.id, ()), principal=principal, readable_ids=readable_ids)
            for task in tasks
        }
        # **회차는 부모와 하위를 함께 묻는다.** 부모 것만 물으면 하위의 승인 행이 비고, 승인까지 끝난
        # 요청 하위가 영원히 「승인 전」으로 읽혀 상위를 막는 것처럼 보인다.
        rounds = self.repository.approval_rounds_for(
            ids + [child.id for rows in shown.values() for child in rows]
        )
        proposals = self.repository.pending_proposal_kinds(ids)
        today = today_for_tasks()
        derived: dict[UUID, dict[str, Any]] = {}
        for task in tasks:
            fact = facts.get(task.id, {})
            derived[task.id] = {
                "assignment": _assignment_wait(fact),
                "approval": self._approval_from_rounds(task, rounds.get(task.id)),
                "proposal": proposals.get(task.id),
                "blocking_children": [
                    {"task_id": str(child.id), "title": str(child.title), "why": why}
                    for child, why in (
                        (child, self._settlement_gap_with(child, rounds.get(child.id)))
                        for child in shown[task.id]
                    )
                    if why is not None
                ],
                "reply": None,
                "status_note": None,
                "overdue_days": _overdue_days(getattr(task, "due_date", None), task.state, today),
            }
        return derived

    def _shown_children(
        self, children: Any, *, principal: Principal | None, readable_ids: set[UUID] | None
    ) -> list[Any]:
        """투영에 실을 하위 — **읽을 수 없는 것은 이름에도 건수에도 없다** (SPEC-001 계승 · UX-U15).

        **막는 것을 세는 일과 보여 주는 일은 다르다.** 완료를 막는 검사(`_require_children_finished`)는
        읽을 수 없는 하위도 **전부** 센다 — 못 보는 부분이 안 끝났는데 상위가 끝나면 그게 거짓이다.
        여기는 화면에 실릴 목록이라 권한이 닿는 것만 남긴다.

        목록 응답에서는 `readable_ids`(그 응답이 이미 판정한 읽기 가능 집합)로 거른다 — 줄마다 다시
        묻지 않는다. 단건 조회에서는 `principal` 로 하나씩 묻는다.
        """
        rows = list(children)
        if readable_ids is not None:
            return [child for child in rows if child.id in readable_ids]
        if principal is not None:
            return [child for child in rows if self.may_read_task(principal, child.id)]
        return rows

    def _approval_from_rounds(self, task: Any, round_facts: dict[str, Any] | None) -> str | None:
        """회차 사실에서 완료 확인 상태를 읽는다. 판정은 `_approval_state` 와 **같은 규칙**이다."""
        if not round_facts or not round_facts.get("rounds"):
            return None
        decisions = round_facts.get("decisions") or ()
        if not decisions:
            return "awaiting_review"
        accepted = [decided_at for decision, decided_at in decisions if decision == "accept"]
        if not accepted:
            return "awaiting_revision"
        reopened_at = _as_utc(getattr(task, "reopened_at", None))
        if reopened_at is not None and all(_as_utc(decided_at) <= reopened_at for decided_at in accepted):
            return "awaiting_review" if TaskState(task.state) is TaskState.DONE else None
        return "approved"

    def _settlement_gap_with(self, child: Any, round_facts: dict[str, Any] | None) -> str | None:
        """`_settlement_gap` 과 **같은 판정**을, 미리 읽어 둔 회차 사실로 답한다.

        `state` 비교는 **밖에서 보이는 값**으로 한다 — 내부 `completion_submitted` 를 그대로 비교하면
        완료 보고를 낸 요청 하위가 「아직 안 끝남」으로 읽히고, 막는 이유가 틀린 채로 화면에 나간다.
        """
        if TaskState(child.state) is TaskState.CANCELLED:
            return None
        if _external_state(child.state) != TaskState.DONE.value:
            return "unfinished"
        if not self.requires_completion_review(child):
            return None
        return None if self._approval_from_rounds(child, round_facts) == "approved" else "awaiting_approval"

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

    def _on_that_tasks_project(self, principal: Principal, task: Any) -> bool:
        """**그 업무의 프로젝트에 이 사람이 붙어 있나** — 읽기를 여는 두 길 중 «프로젝트» 쪽 하나다.

        **리드와 참여자를 가르지 않는다** (SPEC-005 §4 · D-29): 붙었다는 사실 하나가 답이고,
        그 사실은 이미 grant 의 프로젝트 축에 있다 — **새 질의를 만들지 않는다.**

        **조직 축과 갈라 둔다.** 읽기를 여는 길은 둘인데 **체크리스트를 여는 길은 프로젝트 하나**라
        (D-29), 둘을 한 값으로 묶어 두면 「프로젝트 밖에서 조직 축으로 읽는 사람」에게도 항목이
        새어 나간다.
        """
        projects = self._project_scope(principal)
        return bool(projects) and str(getattr(task, "project_id", None) or "") in projects

    def _may_read_beyond_holding(self, principal: Principal, task: Any) -> bool:
        """자기 것이 아닌 업무를 읽는 두 가지 길 — 조직 범위, 그리고 프로젝트 범위.

        한 자리에서 함께 판정한다. 두 곳에서 각자 판정하면 한쪽만 고쳤을 때 조용히 새거나 조용히 막힌다.
        """
        if self._on_that_tasks_project(principal, task):
            return True
        members = self._organization_scope_members(principal)
        if not members:
            return False
        holder = self._assignee_projection([task]).get(task.id) or {}
        return str(holder.get("member_id") or task.created_by_actor_id) in members


    # ---- subtasks: the work inside this work ----

    # ---- 선행업무: 검사와 저장이 한 덩어리 ----------------------------------------

    def _resolve_predecessors(
        self,
        principal: Principal,
        wanted: list[UUID],
        *,
        project_id: Any,
        task_id: UUID | None,
    ) -> list[UUID]:
        """선행으로 설 수 있는 배열인가. **다섯 거절을 각각 다른 오류로 가른다** (SPEC-001 §4 Case Matrix).

        가르는 순서가 계약이다 (WORK-003 § Internal Interface Contract): 자기 자신 → 중복 →
        프로젝트 → 순환. 앞의 것이 더 값 자체의 문제라, 한 요청에 여럿이 걸려 있어도 사람이 먼저
        고쳐야 하는 것부터 말한다.

        **읽을 수 없는 업무는 선행이 될 수 없다** — 없는 것과 같은 말(`TaskNotFound`)로 답한다.
        정상 경로에서는 후보가 같은 프로젝트의 업무라 이 갈래가 드물다.

        `task_id` 가 `None` 이면 **아직 없는 업무**다(생성). 자기 자신도 순환도 성립할 수 없으므로
        그 둘을 묻지 않는다 — 없는 id 를 지어내 비교하지 않는다.
        """
        if not wanted:
            return []
        if project_id is None:
            # 선행은 **같은 프로젝트 안에서만** 선다. 프로젝트가 없으면 그 말 자체가 성립하지 않는다.
            raise TaskPredecessorProjectRequired("선행업무를 지정하려면 프로젝트를 먼저 선택해 주세요")
        seen: set[UUID] = set()
        for candidate in wanted:
            if task_id is not None and candidate == task_id:
                raise TaskPredecessorSelf("자기 자신을 선행으로 둘 수 없습니다")
            if candidate in seen:
                raise TaskPredecessorDuplicate("이미 선행으로 지정된 업무입니다")
            seen.add(candidate)
            if not self._may_read(principal, candidate):
                raise TaskNotFound("task was not found")
            node = self.repository.task_by_id(candidate)
            if node is None:  # pragma: no cover - `_may_read` 가 먼저 거른다
                raise TaskNotFound("task was not found")
            if str(getattr(node, "project_id", None) or "") != str(project_id):
                raise TaskPredecessorProjectMismatch(
                    "같은 프로젝트의 업무만 선행으로 지정할 수 있습니다"
                )
        if task_id is not None:
            self._require_no_predecessor_cycle(task_id, list(seen))
        return list(wanted)

    def resolve_predecessors(self, principal: Principal, wanted: list[UUID], *, project_id: Any) -> list[UUID]:
        """**아직 없는 업무**를 위한 선행 판정 — 요청 발송이 부르는 자리다 (`RequestParentPort`).

        같은 판정을 요청 모듈이 다시 쓰지 않게 하는 얇은 문이다: 프로젝트 일치·읽기 권한은 여기 한 곳에
        있고, 자기 자신과 순환은 업무가 없으므로 성립하지 않는다.
        """
        return self._resolve_predecessors(principal, list(wanted), project_id=project_id, task_id=None)

    def _require_no_predecessor_cycle(self, task_id: UUID, wanted: list[UUID]) -> None:
        """이 업무를 기다리는 선행을 선행으로 삼으면 둘이 서로를 기다린다.

        **데이터베이스가 답할 수 없는 하나다** — 활성 변을 따라 걷는 일이라 여기서 답하고, 부르는
        쪽이 **저장과 같은 transaction** 에 있다 (§5 동시성). 검사한 뒤 저장 전에 남이 다른 변을
        더해 순환이 생기는 틈을 남기지 않기 위해서다.

        **본 것을 다시 보지 않는다** — 원장이 이미 어긋나 고리가 있어도 여기서 무한히 걷지 않는다.
        """
        frontier = [candidate for candidate in wanted]
        seen: set[UUID] = set()
        while frontier:
            batch = [node for node in frontier if node not in seen]
            if not batch:
                return
            seen.update(batch)
            if task_id in seen:
                raise TaskPredecessorCycle("선행 관계가 서로를 기다리게 됩니다")
            edges = self.repository.predecessor_edges(batch)
            frontier = [node for nodes in edges.values() for node in nodes]

    def _predecessor_gate(self, task: Any, principal: Principal | None = None) -> Any:
        """이 업무의 시작을 막는 선행이 있는가, 그리고 **말해도 되는 이름**은 무엇인가.

        **취소된 선행은 막지 않는다** (SPEC-001 U-14) — 남은 선행이 전부 완료거나 취소면 열린다.
        막을지는 **선행 전부**로 정하고, 이름은 이 사람이 읽을 수 있는 것만 낸다 — 미완 하위 거절이
        이미 그 모양이다. 읽을 수 있는 것이 하나도 없으면 이름 없이 막는다.
        """
        blocking = self._blocking_predecessors(task)
        if not blocking:
            return TaskPredecessorGate()
        titles = tuple(
            str(node.title)
            for node in blocking
            if principal is None or self.may_read_task(principal, node.id)
        )
        return TaskPredecessorGate(blocks=True, unfinished_titles=titles)

    def _blocking_predecessors(self, task: Any) -> list[Any]:
        """끝나지도 취소되지도 않은 활성 선행들."""
        rows = []
        for predecessor_id in self.repository.active_predecessor_ids(task.id):
            node = self.repository.task_by_id(predecessor_id)
            if node is None:  # pragma: no cover - 외래키가 막는다
                continue
            if _external_state(node.state) in {TaskState.DONE.value, TaskState.CANCELLED.value}:
                continue
            rows.append(node)
        return rows

    def _predecessor_summary(self, principal: Principal, task: Any) -> list[dict[str, Any]]:
        """상세가 싣는 선행 요약. **읽기 판정이 부른 상세에서는 비운다** (`_may_read_predecessor`).

        그 호출의 결과는 「열 수 있나」 하나를 묻고 버려진다 — 거기까지 요약을 만들면 사슬을 따라
        같은 질의가 겹쳐 쌓인다. 밖으로 나가는 상세는 언제나 이 빗장 밖에서 만들어진다.
        """
        if self._resolving_predecessor_access:
            return []
        return self.predecessor_views(principal, [task.id]).get(task.id, [])

    def predecessor_views(self, principal: Principal, task_ids: list[UUID]) -> dict[UUID, list[dict[str, Any]]]:
        """선행 요약 — 제목과 상태. **볼 수 없는 선행은 제목 없이** 자리만 남는다 (SPEC-001 §4).

        자료 구획과 다르다: 자료는 건수도 내지 않지만, 선행은 **시작을 막는 이유**라 이유를 숨기면
        사람이 다음 걸음을 고를 수 없다. 그래서 **제목은 감추고 건수는 낸다** — 배열 길이가 그 건수다.
        """
        edges = self.repository.predecessors_for(task_ids)
        views: dict[UUID, list[dict[str, Any]]] = {}
        readable: dict[UUID, bool] = {}
        for task_id, predecessor_ids in edges.items():
            rows: list[dict[str, Any]] = []
            for predecessor_id in predecessor_ids:
                if predecessor_id not in readable:
                    readable[predecessor_id] = self._may_read_predecessor(principal, predecessor_id)
                node = self.repository.task_by_id(predecessor_id) if readable[predecessor_id] else None
                rows.append(
                    {
                        "task_id": str(predecessor_id),
                        "title": str(node.title) if node is not None else None,
                        "state": _external_state(node.state) if node is not None else None,
                    }
                )
            views[task_id] = rows
        return views

    def _may_read_predecessor(self, principal: Principal, task_id: UUID) -> bool:
        """선행 하나를 이 사람이 열 수 있는가 — **판정은 `may_read_task()` 하나 그대로다.**

        다만 그 판정은 상세 투영을 지나고, 상세 투영은 다시 자기 선행의 요약을 만든다. 선행이
        사슬로 이어져 있으면 A 를 읽는 동안 B 를 읽고 B 를 읽는 동안 C 를 읽는 **되돌이**가 생긴다 —
        순환은 막혀 있어 무한하지는 않지만, 사슬 길이만큼 같은 질의가 겹쳐 쌓인다.

        그래서 **되돌이 동안에만** 선행 요약을 접는다. 그때의 상세 결과는 「열 수 있나」 하나를 묻고
        버려지므로 접어도 밖으로 나가는 답이 달라지지 않는다 — 판정을 느슨하게 하지 않는다.
        """
        if self._resolving_predecessor_access:
            return self.may_read_task(principal, task_id)
        self._resolving_predecessor_access = True
        try:
            return self.may_read_task(principal, task_id)
        finally:
            self._resolving_predecessor_access = False

    def _party_of(self, principal: Principal, task: Any) -> Any:
        """이 사람이 이 업무의 **어느 자리**에 있는가 — 요청자 · 담당 · 참조자, 아니면 아무 자리도 아니다.

        세 자리를 하나로 묶지 않는 이유는 `modules/work/parties.py` 에 적혀 있다: 묶으면 참조로 받은
        사람에게 판단과 수정까지 함께 열린다. 여기서는 **자리만 말하고** 무엇이 열리는지는 그 파일이 답한다.
        """
        requester_ids: list[str] = []
        request_id = getattr(task, "source_work_request_id", None)
        if request_id is not None and self._requests is not None:
            request = self._requests.request(request_id)
            if request is not None:
                requester_ids = [
                    str(request.requester_id),
                    str(getattr(request, "promoted_by_member_id", None) or ""),
                ]
        holder = self._holder_of(task)
        return party_of(
            str(principal.id),
            requester_ids=requester_ids,
            assignee_ids=[holder] if holder else [],
            cc_member_ids=self.repository.cc_member_ids(task.id),
        )

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

    def may_read_task(self, principal: Principal, task_id: UUID) -> bool:
        """이 사람이 그 업무를 열 수 있는가 — **한 자리에서만 답한다.**

        `readable_task_ids()` 와 이 답이 같은 규칙에서 나와야 한다. 예전에는 갈라져 있었다: 상세는
        `get()` 이 판정하고 자료는 `_list()` 가 판정했는데 `_list()` 에 요청 관계 길이 없어서,
        **요청자가 상세는 여는데 그 업무의 자료는 못 여는** 모양이 성립했다. Phase 4 가 `_list()` 에
        그 길을 넣었고 이 함수는 여전히 `get()` 을 쓴다 — 두 답이 같은 길 위에 선다.

        `get()` 을 쓰는 이유는 하나 더 있다: 목록에 넣기엔 너무 넓은 관계(상위를 관리하는 사람 등)를
        단건 조회에서만 인정하는 자리가 이미 있고, 그 규칙을 여기서 다시 쓰지 않기 위해서다.
        """
        try:
            self.get(principal, task_id)
        except (TaskNotFound, TaskAccessDenied):
            return False
        return True

    def readable_task_ids(self, principal: Principal) -> list[str]:
        """이 사람이 읽을 수 있는 업무 전부 — 자료 검색·그래프가 같은 답을 받는 자리."""
        return [str(row["task_id"]) for row in self.readable_tasks(principal, include_closed=True)]

    #: 옛 이름. 모듈 안에서 부르던 자리가 많아 그대로 두되, **판정은 한 곳**이다.
    _may_read = may_read_task

    def _hierarchy_view(self, principal: Principal, task: Any) -> dict[str, Any]:
        """이 일이 무엇의 일부이고 무엇이 이 일의 일부인가 — 각각 자기 권한으로 읽는다.

        **부모가 있는 업무도 자기 하위를 낸다.** 예전에는 하위의 `children` 을 비웠는데, 저장이 한 단계로
        묶여 있던 시절의 자취다. 저장 깊이가 열린 지금 그렇게 두면 받은 일을 다시 나눈 사람의 화면에서
        그 아래가 통째로 사라진다. `children` 은 언제나 **직속 하위만** 이고 (정책 L-11), 더 깊은 것은
        그 업무로 들어가 읽는다.

        읽을 수 없는 하위는 **목록에도 건수에도** 없다 — 셀 수만 있게 남기면 그것이 곁수로가 된다.
        """
        children = self._readable_children(principal, task)
        rounds = self.repository.approval_rounds_for([UUID(str(row["task_id"])) for row in children])
        settled = blocking = cancelled = 0
        for row in children:
            if row["state"] == TaskState.CANCELLED:
                cancelled += 1
                continue
            child = self.repository.task_by_id(UUID(str(row["task_id"])))
            if child is None or self._settlement_gap_with(child, rounds.get(child.id)) is None:
                settled += 1
            else:
                blocking += 1
        return {
            "parent": self._parent_summary(principal, task),
            "children": children,
            # `done` 은 **완결한 하위**다 — 취소는 따로 세고, `blocking` 이 0이어야 상위를 끝낼 수 있다.
            # 같은 `is_child_settled()` 판정을 쓰므로 이 숫자와 완료 거절이 어긋나지 않는다.
            "child_progress": {"done": settled, "blocking": blocking, "cancelled": cancelled, "total": len(children)},
        }

    def children(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        """직속 하위만, 그리고 그 진행 — 상세가 내는 것과 **같은 판정**이다."""
        self._require(principal, TASK_READ)
        if not self.may_read_task(principal, task_id):
            raise TaskNotFound("task was not found")
        task = self.repository.task_by_id(task_id)
        if task is None:
            raise TaskNotFound("task was not found")
        view = self._hierarchy_view(principal, task)
        return {"task_id": str(task_id), "children": view["children"], "child_progress": view["child_progress"]}

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
                    "state": _external_state(child.state),
                    "due_date": _iso(child.due_date),
                    "assignee": self._assignee_projection([child]).get(child.id),
                    "derived": self._derived_for([child], principal=principal).get(child.id),
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
        return {"task_id": str(parent.id), "title": parent.title, "state": _external_state(parent.state)}

    # ---- delivery: reporting what was handed over, and the answer the person who asked gives ----

    def is_child_settled(self, child: Any) -> bool:
        """**하위가 끝났는가** — 상위 완료를 막느냐 마느냐의 단 하나의 판정 (정책 V-15·V-16).

        세 가지가 함께여야 끝난 것이다: ① 취소가 아니고 ② `state=done` 이며 ③ **요청 업무면 요청자의
        승인까지** 받았다. 취소는 검사에서 **제외**되므로 여기서는 「끝난 것」과 같게 답한다 — 로그는
        남고 완료로 바뀌지도 않는다.

        예전에는 `state` 만 봤다(BASE-002 O-20). 그러면 담당자가 완료 보고만 제출해도 상위가 끝날 수
        있었다 — 요청자가 보지도 않은 결과 위에서 상위가 닫힌다. **v2 가 새로 닫는 자리가 여기다.**

        **모르면 완결로 치지 않는다.** 승인 행을 찾지 못하면 미완결이다.
        """
        if TaskState(child.state) is TaskState.CANCELLED:
            return True
        # 밖에서 보이는 값으로 본다 — 완료 보고를 낸 요청 하위는 `done` 이되 **승인 전**이고,
        # 그 구분은 바로 다음 줄이 한다. 내부 값으로 비교하면 그 행이 여기서 미리 걸러져
        # 「승인까지여야 완결」이라는 규칙이 실제로 적용되는 자리를 잃는다.
        if _external_state(child.state) != TaskState.DONE.value:
            return False
        if not self.requires_completion_review(child):
            return True
        return self._approval_state(child) == "approved"

    def _settlement_gap(self, child: Any) -> str | None:
        """무엇이 모자라 막는가 — `unfinished`(아직 안 끝남) 또는 `awaiting_approval`(승인 전).

        **밖에서 보이는 상태로 판정한다.** 완료 보고를 낸 요청 하위는 내부 값이
        `completion_submitted` 인데, 그것은 밖으로 `done` + `approval=awaiting_review` 다 — 「아직
        시작도 안 했다」가 아니라 「끝내 놓고 요청자의 답을 기다린다」이고, 그 둘은 사람이 할 일이 다르다.
        """
        if self.is_child_settled(child):
            return None
        return "unfinished" if _external_state(child.state) != TaskState.DONE.value else "awaiting_approval"

    def blocking_children(self, task: Any, principal: Principal | None = None) -> list[dict[str, str]]:
        """상위의 최종 완료를 **막는 하위**와 그 이유.

        `principal` 을 주면 **그 사람이 읽을 수 있는 것만** 남는다 — 이름과 건수가 밖으로 나가는 자리에
        쓴다. 주지 않으면 전부다 — **막을지 말지를 정하는 자리**에 쓴다. 두 쓰임을 한 함수로 두되
        인자로 가른다.
        """
        rows = []
        for child in self.repository.children_of(task.id):
            why = self._settlement_gap(child)
            if why is None:
                continue
            if principal is not None and not self.may_read_task(principal, child.id):
                continue
            rows.append({"task_id": str(child.id), "title": str(child.title), "why": why})
        return rows

    def _require_children_finished(self, task: Any, principal: Principal | None = None) -> None:
        """부분이 끝나지 않은 일은 끝난 일이 아니다.

        **막는 판정과 말해 주는 내용을 가른다.**
        - 막을지는 **하위 전부**로 정한다. 부르는 사람이 못 보는 하위가 안 끝났는데 상위를 끝내면
          그 완료가 거짓이 된다 — 안 보인다고 없는 것이 아니다.
        - 오류 본문은 **밖으로 나가는 투영**이라 그 사람이 읽을 수 있는 하위만 이름으로 낸다.
          읽을 수 없는 것만 막고 있으면 **이름도 건수도 내지 않고** 일반 문구로 답한다 — 거절 사유로
          남의 업무 제목이나 개수를 알려 주면 그 자체가 곁수로다.

        **자동 완료는 없다** — 하위가 다 끝나도 사람이 완료 명령을 부른다. 참고 연결은 하위가 아니므로
        이 검사에 들어가지 않는다 (E-6): 여기는 `children_of` 만 본다.
        """
        if not self.blocking_children(task):
            return
        shown = self.blocking_children(task, principal) if principal is not None else self.blocking_children(task)
        if not shown:
            raise TaskChildrenUnfinished("끝나지 않은 하위 업무가 있습니다")
        names = ", ".join(row["title"] for row in shown[:3])
        raise TaskChildrenUnfinished(f"끝나지 않은 하위 업무가 있습니다: {names}", tuple(shown))

    def requires_completion_review(self, task: Any) -> bool:
        """Work someone else asked for is finished when they say so, not when the holder says so."""
        return getattr(task, "source_work_request_id", None) is not None

    def _approval_state(self, task: Any) -> str | None:
        """완료 확인이 어디까지 왔나 — `awaiting_review` · `awaiting_revision` · `approved` · `null`.

        **판단 원장에서 답한다.** `state` 문자열이나 투영을 읽어 되짚지 않는다 (SPEC-001 §4).

        **지금 완료 회차에 유효한 승인만 센다** (BASE-002 O-33). 보완으로 회차가 오르거나 재개가
        있었으면 **그 이전 승인은 무효다** — 아무 과거 승인 하나로 충분하다고 하면, 한 번 승인받은 일이
        다시 열리고 바뀐 뒤에도 영원히 「승인됨」으로 남는다.
        """
        item = self.repository.delivery_item(task)
        if item is None:
            return None
        submissions = self.repository.delivery_submissions(item)
        if not submissions:
            return None
        latest = submissions[-1]
        decisions = [row for row in self.repository.delivery_decisions([latest.id]) if row.submission_id == latest.id]
        if not decisions:
            return "awaiting_review"
        accepted = [row for row in decisions if row.decision == "accept"]
        if not accepted:
            return "awaiting_revision"
        # 재개 이후의 승인만 유효하다. 재개가 그 회차를 다시 열었으므로 그 전의 「인정」은 지금 결과에
        # 대한 답이 아니다. 이력은 그대로 남는다 (E-5) — 판정만 이 선 뒤를 본다.
        reopened_at = _as_utc(getattr(task, "reopened_at", None))
        if reopened_at is not None and all(_as_utc(row.decided_at) <= reopened_at for row in accepted):
            return "awaiting_review" if TaskState(task.state) is TaskState.DONE else None
        return "approved"

    def submit_completion(
        self,
        principal: Principal,
        task_id: UUID,
        expected_version: int,
        *,
        summary: str,
        output_material_ids: list[UUID] | None = None,
    ) -> TaskCompletionResult:
        """Hand the work over: freeze what was delivered and put it in front of the person who asked for it."""
        self._require(principal, TASK_SELF_MANAGE)
        command = TaskCompletionInput(expected_version=expected_version, summary=summary, output_material_ids=output_material_ids or [])
        expected_version, summary, output_material_ids = command.expected_version, command.summary, command.output_material_ids
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
        self.repository.open_delivery_round(task, str(principal.id), reviewer_id, snapshot)
        # 내부 값은 그대로 두고 **투영에서만** `done` + `approval=awaiting_review` 로 낸다 —
        # 제출 가드와 승인 가드가 이 값을 읽고 있어서, enum 을 지금 없애면 그 둘이 함께 무너진다.
        task.state = TaskState.COMPLETION_SUBMITTED
        task.block_reason = None
        # 밖으로 `done` 인 순간이 이때다. 승인은 `state` 를 바꾸지 않으므로 이 시각이 완료 시각이다.
        task.completed_at = datetime.now(UTC)
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task, str(principal.id), "task.completion_submitted", f"완료 보고: {clean_summary[:80]}"
        )
        return {**self._view(task, principal), "delivery": self.delivery_view(principal, task)}

    def accept_delivery(self, principal: Principal, task: Any, submission: Any, expected_version: int) -> None:
        """The person who asked says this is what they wanted. Only this closes the work."""
        if TaskState(task.state) is not TaskState.COMPLETION_SUBMITTED:
            raise InvalidTaskTransition("확인할 완료 보고가 없습니다")
        self._require_children_finished(task, principal)
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
        # 보완은 **같은 업무의 다음 회차**다 (E-3). 끝나지 않았으므로 완료 시각을 지운다 — 다음 제출이
        # 새로 찍는다. 지난 회차와 그 판단은 그대로 남는다.
        task.completed_at = None
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
    ) -> TaskDateMutationResult:
        self._require(principal, TASK_SELF_MANAGE)
        if target is TaskState.CANCELLED:
            # **이 거절은 담당자보다 먼저 온다.** 수락된 요청 업무의 직접 취소는 요청자·담당자·관리자
            # **누구에게도** 열려 있지 않은데(정책 V-19), 들고 있는 사람만 찾아 보면 요청자는 「없는
            # 업무」로 끝난다 — 왜 못 하는지도, 어디로 가야 하는지도 모른 채로. 읽을 수 있는 사람에게는
            # 그 이유를 말한다. 읽을 수 없는 사람에게는 아래에서 그대로 404 다.
            readable = self.repository.task_by_id(task_id)
            if readable is not None and self.may_read_task(principal, task_id):
                self._require_cancellable(readable)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        requires_review = self.requires_completion_review(task)
        # **완결 판정은 `state` 만 보지 않는다** — 요청 하위는 요청자의 승인까지여야 끝난 것이다.
        # 막을지는 하위 전부로 정하고, 이름은 이 사람이 읽을 수 있는 것만 낸다. 읽을 수 있는 것이
        # 하나도 없으면 이름 없이 막는다 — 없는 이름을 지어내지도, 남의 제목을 흘리지도 않는다.
        children_block = target is TaskState.DONE and not requires_review and bool(self.blocking_children(task))
        unfinished = (
            tuple(row["title"] for row in self.blocking_children(task, principal)) if children_block else ()
        )
        transition = transition_task(
            Task(
                id=str(task.id),
                title=str(task.title),
                state=TaskState(task.state),
                version=int(task.version),
                start_date=task.start_date,
                block_reason=task.block_reason,
            ),
            ChangeTaskState(
                target=target,
                expected_version=expected_version,
                reason=reason,
                today=today_for_tasks(),
            ),
            TaskCompletionContext(
                requires_completion_review=requires_review,
                unfinished_child_titles=unfinished,
                children_block=children_block,
            ),
            # **판정과 전이가 한 덩어리다** (§5 동시성). 업무 행을 이미 잠근 뒤에 세므로, 센 다음
            # 전이 전에 선행이 다시 열리는 틈이 이 transaction 안에 없다.
            self._predecessor_gate(task, principal),
        )
        # **D1 의 두 번째 자리** (SPEC-004 §5). `open → in_progress` 가 **비어 있던 시작일을 오늘로
        # 채운다** — 그것도 날짜 변경이다. 이 자리는 `validate_schedule` 을 지나지 않으므로
        # **뒤집힌 기간이 실재하고**, 그래서 계약이 K11 정규화로 그것을 덮는다.
        dates_before = (task.start_date, task.due_date)
        task.state = transition.task.state
        task.start_date = transition.task.start_date
        task.block_reason = transition.task.block_reason
        task.version = transition.task.version
        # **계획과 실제를 가른다** (정책 V-13·L-10). `start_date` 는 계획이고 이 두 값이 실제다.
        # `open` 에서 바로 완료하면 `started_at` 은 **비어 있는 채로 둔다** — 시작하지 않은 일이 끝난
        # 것이고, 없던 시작을 지어내지 않는다.
        now = datetime.now(UTC)
        if target is TaskState.IN_PROGRESS and task.started_at is None:
            task.started_at = now
        if target is TaskState.DONE:
            task.completed_at = now
        if target is TaskState.CANCELLED and task.cancel_reason is None:
            task.cancel_reason = "direct"
        released = self._schedule_release_for(task, dates_before)
        self.repository.touch(task)
        self.repository.record_activity(
            task,
            str(principal.id),
            "task.state_changed",
            transition.event.summary,
            before_ref=transition.event.before_ref,
            reason=transition.event.reason,
        )
        return {**self._view(task, principal), "schedule_release": released}

    def _require_cancellable(self, task: Any) -> None:
        """**수락된 요청 업무는 직접 취소할 수 없다** (정책 V-19 · `WORK_CANCEL_REQUIRES_AGREEMENT`).

        요청자·담당자·관리자 **모두** 같다. 둘이 합의해 시작한 일을 한쪽이 혼자 접으면 다른 쪽은
        접혔다는 사실을 나중에 알게 된다. 그 자리에서 취소로 가는 길은 **합의 취소 하나**다.

        수락 전 요청 업무·본인 업무·배정 업무의 직접 취소는 **현행 그대로**다 — 아무도 받아들인 적이
        없거나, 합의할 상대가 없는 일이다.
        """
        if not self.requires_completion_review(task):
            return
        facts = self.repository.origin_facts([task]).get(task.id, {})
        if facts.get("assignee_id") is None:
            return
        raise TaskCancelRequiresAgreement(
            "수락된 요청 업무는 직접 취소할 수 없습니다. 취소 제안으로 상대의 동의를 받으세요"
        )

    # ---- 재개: 끝난 일을 다시 연다 (SPEC-003 §4 `POST /api/tasks/{id}/reopen`) ----

    def reopen(self, principal: Principal, task_id: UUID, expected_version: int, reason: str | None = None) -> TaskMutationResult:
        """**끝난 일을 다시 연다.** 새 업무처럼 만드는 것이 아니다 (E-5).

        이전 완료 이력·회차·결과를 **지우지 않는다** — `reopened_at` 이 「여기서부터 다시」를 가리키는
        선이고, 그 선 앞의 승인은 지금 결과에 대한 답이 아니므로 완결 판정에서만 무효가 된다.

        **완료된 상위가 있으면 거부한다** (정책 L-13): 상위가 이미 끝났는데 그 부분이 다시 열리면
        상위의 완료가 거짓이 된다. **상위를 먼저 재개하라고 낸다.**
        """
        self._require(principal, TASK_READ)
        task = self.repository.task_by_id(task_id, lock=True)
        if task is None or not self.may_read_task(principal, task_id):
            raise TaskNotFound("task was not found")
        if int(task.version) != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if TaskState(task.state) not in {TaskState.DONE, TaskState.COMPLETION_SUBMITTED}:
            raise InvalidTaskTransition("끝난 업무만 다시 열 수 있습니다")
        self._require_may_reopen(principal, task)
        parent_id = getattr(task, "parent_task_id", None)
        if parent_id is not None:
            # **상위 행을 잡고 다시 읽는다.** 잠금 없이 읽으면 상위 완료와 하위 재개가 서로 다른 행만
            # 잠근 채 지나가 `done` 인 상위 아래에 `in_progress` 인 하위가 남는다 — READ COMMITTED 의
            # write skew 다(정책 L-13). 완료 경로가 이미 **완료 대상 행**을 먼저 잡으므로, 여기서
            # 상위를 기다리면 그 커밋 뒤의 상태를 보고 막힌다.
            #
            # 잠금 순서는 하위 → 상위이고 완료 경로는 자기 한 행만 잡는다. 한쪽이 둘, 다른 쪽이 하나라
            # 서로를 기다리는 고리가 생기지 않는다 — 모순을 deadlock 으로 바꾸지 않는다.
            parent = self.repository.task_by_id(parent_id, lock=True)
            if parent is not None and TaskState(parent.state) in {TaskState.DONE, TaskState.COMPLETION_SUBMITTED}:
                raise TaskReopenParentDone("상위 업무가 완료되어 있습니다. 상위 업무를 먼저 다시 여세요")
        now = datetime.now(UTC)
        task.state = TaskState.IN_PROGRESS
        task.reopened_at = now
        task.completed_at = None
        task.version += 1
        self.repository.touch(task)
        clean = " ".join(str(reason or "").split()) or None
        self.repository.record_activity(
            task, str(principal.id), "task.reopened", f"업무 재개: {task.title}",
            before_ref=f"task:{task.id}@{expected_version}", reason=clean,
        )
        return self._view(task, principal)

    def _require_may_reopen(self, principal: Principal, task: Any) -> None:
        """누가 다시 열 수 있나 — **본인 업무는 담당자, 요청 업무는 요청자** (정책 V-19).

        **배정 업무는 이 SPEC 이 정하지 않았다**(미정 M-3). 새로 열지 않는다 — 열어 두면 미정을
        기본값으로 확정해 버린다. 배정 업무에서는 담당자 자신의 재개만 현행 권한 그대로 선다.
        """
        facts = self.repository.origin_facts([task]).get(task.id, {})
        if self.requires_completion_review(task):
            requester = self._requester_of(task)
            if requester is None or str(principal.id) != requester:
                raise TaskReopenForbidden("이 업무를 다시 열 수 있는 사람은 요청자입니다")
            return
        if facts.get("assignee_id") != str(principal.id):
            raise TaskReopenForbidden("이 업무를 다시 열 수 있는 사람은 담당자입니다")

    # ---- 제안–동의: 수락 뒤의 취소와 조건 변경 (SPEC-003 §4) ----

    def propose(
        self,
        principal: Principal,
        task_id: UUID,
        kind: str,
        expected_version: int,
        *,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """제안을 낸다. **이 명령만으로는 상태도 조건도 바뀌지 않는다** (정책 V-19·V-20).

        낼 수 있는 사람은 **요청자**다 — 승격 요청이면 **누른 사람**이 그 자리다 (BASE-002 O-31).
        """
        self._require(principal, TASK_READ)
        if kind not in {"cancellation", "terms_change"}:
            raise TaskError("제안 종류는 cancellation 또는 terms_change 입니다")
        task = self._proposal_task(principal, task_id, expected_version)
        if not self._is_request_owner(principal, task):
            raise TaskAccessDenied("이 업무의 제안은 요청자만 낼 수 있습니다")
        if kind == "terms_change" and not payload:
            raise TaskError("조건 변경 제안에는 바꿀 내용이 필요합니다")
        clean = " ".join(str(reason or "").split()) or None
        if kind == "cancellation" and not clean:
            raise TaskError("취소 제안에는 사유가 필요합니다")
        if any(row.state == "pending" and row.kind == kind for row in self.repository.proposals_for(task.id)):
            raise TaskProposalNotPending("이미 응답을 기다리는 같은 종류의 제안이 있습니다")
        record = self.repository.open_proposal(task, kind, str(principal.id), payload, clean)
        return {"task_id": str(task.id), "proposal": _proposal_view(record), "task_version": int(task.version)}

    def respond_to_proposal(
        self, principal: Principal, task_id: UUID, proposal_id: UUID, expected_version: int, *, agree: bool, reason: str | None = None
    ) -> dict[str, Any]:
        """**담당자만** 답한다 (SPEC-003 §4). 동의해야 비로소 바뀐다."""
        self._require(principal, TASK_READ)
        # **읽기 권한을 먼저 다시 검사한다** (K-2) — 영수증을 돌려주기 전에도 지금의 권한으로 묻는다.
        # 잃었으면 존재를 숨긴다.
        if not self.may_read_task(principal, task_id):
            raise TaskNotFound("task was not found")
        task = self.repository.task_by_id(task_id, lock=True)
        if task is None:
            raise TaskNotFound("task was not found")
        record = self.repository.proposal(task_id, proposal_id, lock=True)
        if record is None:
            raise TaskNotFound("proposal was not found")
        # **재전송을 회차 검사보다 먼저 가른다.** 동의가 업무를 바꾸면 회차가 오르므로, 사람이 보던
        # 화면에서 그대로 다시 누른 요청은 「그때의 회차」를 싣고 온다 — 그것을 stale 로 거절하면
        # 재전송이 실패로 읽힌다. 합의 취소 뒤에는 담당 관계까지 끝나 있어서 담당자 검사도 막는다.
        # 재전송의 신원은 **저장된 답**이 갖고 있다: 같은 사람 · 같은 답 · 그 제안이 소비한 회차.
        # 세 가지가 모두 같을 때만 영수증이고, **두 번째 effect 는 없다.**
        # 같은 사람의 **다른 답**은 영수증이 아니다 — 409 로 갈린다(아래).
        if (
            record.state in {"agreed", "declined"}
            and record.responder_id == str(principal.id)
            and record.state == ("agreed" if agree else "declined")
            and int(record.task_version) == int(expected_version)
        ):
            # **재전송은 영수증이다** — 두 번째 effect 가 없으므로 닫힌 건수도 `0` 이다.
            return {
                "task_id": str(task.id),
                "proposal": _proposal_view(record),
                "task_version": int(task.version),
                "schedule_release": _NO_SCHEDULE_RELEASE,
            }
        if record.state != "pending":
            # **「이미 답했다」가 「회차가 낡았다」보다 먼저다.** 위에서 재전송이 아니라고 갈렸으므로
            # 여기 오는 것은 *다른 답*이거나 *지금 회차로 다시 답하는 것*이고, 둘 다 사람에게 알려 줄
            # 사실은 하나다 — 이 제안은 이미 끝났다. 회차 검사가 먼저 걸리면 그 사실이 「낡았다」로
            # 뭉개져 다음 걸음이 보이지 않는다.
            raise TaskProposalNotPending("이미 처리된 제안입니다")
        if int(task.version) != expected_version:
            raise InvalidTaskTransition("task version is stale")
        facts = self.repository.origin_facts([task]).get(task.id, {})
        if facts.get("assignee_id") != str(principal.id):
            raise TaskProposalResponderOnly("이 제안에 답할 수 있는 사람은 담당자입니다")
        self.repository.settle_proposal(record, str(principal.id), "agreed" if agree else "declined")
        # **거절은 아무것도 바꾸지 않는다** — 그래도 묶음은 낸다 (증보 K3: 세 자리 전부).
        released = self._apply_proposal(principal, task, record) if agree else _NO_SCHEDULE_RELEASE
        return {
            "task_id": str(task.id),
            "proposal": _proposal_view(record),
            "task_version": int(task.version),
            "schedule_release": released,
        }

    def withdraw_proposal(self, principal: Principal, task_id: UUID, proposal_id: UUID, expected_version: int) -> dict[str, Any]:
        """제안한 사람이 거둔다. 답하기 전에만 열려 있고 **회차가 필수다** (K-4).

        재전송이면 영수증이다 — 이미 거둔 제안에 같은 사람이 다시 부르면 두 번째 effect 없이 현재를
        돌려준다. 돌려주기 전에 읽을 권한을 **다시 검사한다** (K-2).
        """
        self._require(principal, TASK_READ)
        if not self.may_read_task(principal, task_id):
            raise TaskNotFound("task was not found")
        task = self.repository.task_by_id(task_id, lock=True)
        if task is None:
            raise TaskNotFound("task was not found")
        record = self.repository.proposal(task_id, proposal_id, lock=True)
        if record is None or record.proposed_by != str(principal.id):
            raise TaskNotFound("proposal was not found")
        # 거두기도 재전송을 **회차 검사보다 먼저** 가른다 — 같은 사람이 같은 제안을 다시 거두면
        # 두 번째 effect 없이 현재를 돌려준다. 거두는 것은 업무를 바꾸지 않으므로 회차는 그대로다.
        if record.state == "withdrawn":
            return {"task_id": str(task_id), "proposal": _proposal_view(record), "task_version": int(task.version)}
        if record.state != "pending":
            raise TaskProposalNotPending("이미 처리된 제안입니다")
        if int(task.version) != expected_version:
            raise InvalidTaskTransition("task version is stale")
        self.repository.settle_proposal(record, str(principal.id), "withdrawn")
        return {"task_id": str(task_id), "proposal": _proposal_view(record), "task_version": int(task.version)}

    def proposals(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        self._require(principal, TASK_READ)
        if not self.may_read_task(principal, task_id):
            raise TaskNotFound("task was not found")
        rows = [_proposal_view(row) for row in self.repository.proposals_for(task_id)]
        return {
            "task_id": str(task_id),
            "pending": [row for row in rows if row["state"] == "pending"],
            "history": rows,
        }

    def _apply_proposal(self, principal: Principal, task: Any, record: Any) -> TaskScheduleReleaseView:
        """동의가 실제로 바꾸는 것. 그 전에는 **원래 조건과 상태가 유지된다.**

        **D1 의 세 번째 자리** (SPEC-004 §5) — 제안이 마감일을 덮어쓰면 그것도 날짜 변경이다.
        이 자리도 `validate_schedule` 을 지나지 않으므로 뒤집힌 기간이 설 수 있고, K11 정규화가 덮는다.
        """
        now = datetime.now(UTC)
        if record.kind == "cancellation":
            task.state = TaskState.CANCELLED
            task.cancel_reason = "cancellation_agreed"
            task.version += 1
            self.repository.touch(task)
            self.repository.record_activity(
                task, str(principal.id), "task.state_changed", f"합의 취소: {task.title}", reason=record.reason
            )
            # **요청도 같은 transaction 에서 끝난다.** 업무만 닫고 요청을 `accepted` 로 두면 세 자리가
            # 어긋난다: 보낸 업무 표가 「담당 확정」으로 읽고, 담당 관계가 `active` 로 남고, 목록 정리가
            # 「진행 중」이라며 거절한다 — 합의로 접은 바로 그 항목을 치울 수 없게 된다.
            # 요청 상태도의 `accepted → cancelled_by_agreement` 가 이 전이다 (SPEC-003 §4 State).
            self._settle_cancelled_request(principal, task, record)
            # **업무 종료는 쓰기가 아니다** (§B) — 취소된 업무의 배정은 닫지 않고, 합본 조회가
            # 상태로 거른다. 상태 변경 아홉 경로에 아무것도 더하지 않는 이유가 그것이다.
            return _NO_SCHEDULE_RELEASE
        payload = dict(record.payload or {})
        changed: list[str] = []
        dates_before = (task.start_date, task.due_date)
        if "due_date" in payload:
            wanted = payload["due_date"]
            task.due_date = date.fromisoformat(str(wanted)) if wanted else None
            changed.append("due_date")
        if "title" in payload and str(payload["title"] or "").strip():
            task.title = str(payload["title"]).strip()[:300]
            changed.append("title")
        if "description" in payload:
            task.description = _clean_text(payload["description"])
            changed.append("description")
        if not changed:
            raise TaskError("조건 변경 제안에 적용할 내용이 없습니다")
        released = self._schedule_release_for(task, dates_before)
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task, str(principal.id), "task.updated", f"조건 변경 동의: {task.title} ({', '.join(changed)})",
            reason=record.reason,
        )
        _ = now
        return released

    def _settle_cancelled_request(self, principal: Principal, task: Any, record: Any) -> None:
        """합의 취소가 그 **요청**도 끝낸다 — 상태·이력·담당 관계를 한 덩어리로 맞춘다.

        본인 업무·배정 업무에는 요청이 없으므로 할 일이 없다. 그쪽의 취소는 직접 취소로 이미 끝났다.
        """
        if self._requests is None or getattr(task, "source_work_request_id", None) is None:
            return
        self._requests.settle_by_agreement(
            task.source_work_request_id, str(principal.id), reason=record.reason
        )

    def _proposal_task(self, principal: Principal, task_id: UUID, expected_version: int) -> Any:
        if not self.may_read_task(principal, task_id):
            raise TaskNotFound("task was not found")
        task = self.repository.task_by_id(task_id, lock=True)
        if task is None:
            raise TaskNotFound("task was not found")
        if int(task.version) != expected_version:
            raise InvalidTaskTransition("task version is stale")
        return task

    def _is_request_owner(self, principal: Principal, task: Any) -> bool:
        """요청자 자리에 선 사람인가 — 요청자 본인 **또는 승격에서 누른 사람** (BASE-002 O-31).

        요청자 전용 조작(수정·재상신·철회)이 이미 그 모양으로 판정한다. 제안도 같은 모양을 쓴다.
        """
        if self._requests is None or getattr(task, "source_work_request_id", None) is None:
            return False
        request = self._requests.request(task.source_work_request_id)
        if request is None:
            return False
        return str(principal.id) in {
            str(request.requester_id),
            str(getattr(request, "promoted_by_member_id", None) or ""),
        }

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    def history(self, principal: Principal, task_id: UUID) -> TaskHistoryResult:
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

    def history_diff(self, principal: Principal, task_id: UUID, before: int, after: int) -> TaskHistoryDiffResult:
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
    ) -> ChecklistMutationResult:
        """Only the person who holds the Task may add a step, and the text must say something."""
        task = self._holding(principal, task_id, expected_task_version)
        try:
            command = ChecklistAddInput(text=text, expected_task_version=expected_task_version)
        except ValueError as error:
            raise TaskError(str(error)) from error
        text = command.text
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
    ) -> ChecklistMutationResult:
        """Checking a step records who did it and when; unchecking clears those facts rather than keeping a stale actor."""
        task = self._holding(principal, task_id, expected_task_version)
        try:
            command = ChecklistUpdateInput(text=text, done=done, expected_version=expected_version, expected_task_version=expected_task_version)
        except ValueError as error:
            raise TaskError(str(error)) from error
        text, done, expected_version = command.text, command.done, command.expected_version
        item = self.repository.checklist_item(task.id, item_id, lock=True)
        if item is None:
            raise TaskNotFound("checklist item was not found")
        now = datetime.now(UTC)
        change = update_checklist_item(
            ChecklistItem(
                id=str(item.id),
                task_id=str(task.id),
                text=str(item.text),
                done=bool(item.done),
                version=int(item.version),
                completed_by=item.completed_by,
                completed_at=item.completed_at,
            ),
            UpdateChecklistItem(
                text=text,
                done=done,
                expected_version=expected_version,
                actor_id=str(principal.id),
                changed_at=now,
            ),
        )
        item.text = change.item.text
        item.done = change.item.done
        item.completed_by = change.item.completed_by
        item.completed_at = change.item.completed_at
        item.version = change.item.version
        for activity in change.activities:
            self._moved(task)
            self.repository.record_activity(
                task,
                str(principal.id),
                activity.kind,
                activity.summary,
            )
        item.updated_at = now
        return _checklist_view(item, task)

    def add_progress_note(
        self,
        principal: Principal,
        task_id: UUID,
        summary: str,
        *,
        expected_task_version: int,
    ) -> dict[str, Any]:
        """Append a person's progress fact to an existing Task without creating a second work object."""
        task = self._holding(principal, task_id, expected_task_version)
        if TaskState(task.state) in {TaskState.DONE, TaskState.CANCELLED}:
            raise TaskError("a closed task cannot receive a progress note")
        cleaned = " ".join(summary.split())
        if not cleaned:
            raise TaskError("progress note summary is required")
        cleaned = cleaned[:300]
        self._moved(task)
        self.repository.record_activity(
            task,
            str(principal.id),
            "task.progress.noted",
            f"진행 메모: {cleaned}",
        )
        return {"task_id": str(task.id), "task_version": int(task.version), "summary": cleaned}

    def archive_checklist_item(
        self,
        principal: Principal,
        task_id: UUID,
        item_id: UUID,
        *,
        expected_version: int | None = None,
        expected_task_version: int | None = None,
    ) -> ChecklistMutationResult:
        """Take a step off the list without erasing that it was ever there."""
        task = self._holding(principal, task_id, expected_task_version)
        try:
            command = ChecklistArchiveInput(expected_version=expected_version, expected_task_version=expected_task_version)
        except ValueError as error:
            raise TaskError(str(error)) from error
        expected_version = command.expected_version
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
    ) -> ChecklistOrderResult:
        """Put the steps in the order the work happens. The whole order is rewritten, never one step nudged."""
        task = self._holding(principal, task_id, expected_task_version)
        try:
            command = ChecklistOrderInput(item_ids=item_ids, expected_task_version=expected_task_version)
        except ValueError as error:
            raise TaskError(str(error)) from error
        item_ids = command.item_ids
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

    def _checklist_fields(self, task: Any) -> dict[str, Any]:
        """항목과 집계를 **함께** 낸다 — 하나만 실으면 같은 사실을 두 규칙으로 읽는 것이 된다 (D-29)."""
        items = [_checklist_view(item) for item in self.repository.checklist_for(task.id)]
        return {
            "checklist": items,
            "checklist_progress": {"done": sum(1 for item in items if item["done"]), "total": len(items)},
        }

    def _with_checklist(self, task: Any, principal: Principal) -> dict[str, Any]:
        return {
            **self._view(task, principal),
            **self._checklist_fields(task),
            "references": self.references(principal, task),
            "delivery": self.delivery_view(principal, task),
            **self._hierarchy_view(principal, task),
            "origin": self._origin_projection(principal, [task]).get(task.id),
            "assignee": self._assignee_projection([task]).get(task.id),
            # 상세는 선행의 **제목과 상태**까지 낸다 — 무엇이 시작을 막는지가 그 줄의 쓸모다 (U-13).
            "predecessors": self._predecessor_summary(principal, task),
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

    def add_reference(self, principal: Principal, task_id: UUID, referenced_task_id: UUID) -> TaskReferenceResult:
        """Point at work that came before. Only at work this person may already read, and never at itself."""
        command = TaskReferenceCommand(task_id=task_id, referenced_task_id=referenced_task_id)
        task_id, referenced_task_id = command.task_id, command.referenced_task_id
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

    def release_reference(self, principal: Principal, task_id: UUID, reference_id: UUID) -> TaskReferenceReleaseResult:
        """Stop pointing at it. The row closes rather than disappearing, so history still shows it was there."""
        command = TaskReferenceReleaseCommand(task_id=task_id, reference_id=reference_id)
        task_id, reference_id = command.task_id, command.reference_id
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

    def _view(
        self,
        task: Any,
        principal: Principal | None = None,
        *,
        derived: dict[str, Any] | None = None,
        cc_member_ids: list[str] | None = None,
        preceding_task_ids: list[UUID] | None = None,
    ) -> TaskMutationResult:
        """밖으로 나가는 업무 하나. **`state` 는 계약의 넷뿐이고 `derived` 는 서버가 만든다.**

        `derived` 를 미리 계산해 넘길 수 있다 — 목록이 줄마다 같은 질의를 반복하지 않게 하는 자리다.
        """
        return {
            "task_id": str(task.id),
            "title": task.title,
            "state": _external_state(task.state),
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
            # 왜 취소됐는가. 상위 목록에서 「취소됨 — 요청 거절」로 읽히는 값이다.
            "cancel_reason": getattr(task, "cancel_reason", None),
            # **계획과 실제를 가른다** — `start_date` 는 계획이고 `started_at` 은 실제로 시작한 그 순간이다.
            "started_at": _iso(getattr(task, "started_at", None)),
            "completed_at": _iso(getattr(task, "completed_at", None)),
            "reopened_at": _iso(getattr(task, "reopened_at", None)),
            "assignment": _assignment_view(getattr(task, "assignments", None)),
            # **참조자.** 요청의 `cc_member_ids` 와 같은 뜻이고 같은 표에서 나온다 — 두 표면이 같은
            # 이름으로 같은 것을 낸다. 목록은 미리 모아 넘기고, 단건은 여기서 한 번 묻는다.
            "cc_member_ids": (
                list(cc_member_ids) if cc_member_ids is not None else self.repository.cc_member_ids(task.id)
            ),
            # **선행업무 — 활성인 것만** (SPEC-001 §4). 뗀 것은 빠진다. 상위(`parent_task_id`)·
            # 참고(`references`)와 **다른 줄**이다: 한 배열에 섞으면 무엇이 시작을 막는지가 사라진다.
            "preceding_task_ids": [
                str(item)
                for item in (
                    preceding_task_ids
                    if preceding_task_ids is not None
                    else self.repository.active_predecessor_ids(task.id)
                )
            ],
            # 승인자(화면 라벨 「결재자」) 0..1 — `업무` 갈래가 값을 넣는다 (SPEC-001 §7 OQ-M).
            "approver_id": _str(getattr(task, "approver_id", None)),
            # **파생 표시는 서버가 만든다** (SPEC-003 §2.11·§4 Data). 화면이 `state`·`origin_kind` 로
            # 기다림과 권한을 되짚지 않는다 — 되짚으면 두 곳의 규칙이 조용히 갈린다.
            "derived": derived if derived is not None else self._derived_for([task], principal=principal).get(task.id),
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


def _as_utc(value: Any) -> datetime | None:
    """시각 하나를 **비교할 수 있는 모양**으로 맞춘다.

    같은 열이 백엔드마다 다르게 돌아온다: PostgreSQL 은 tz 를 달아 주고 SQLite 는 naive 로 준다.
    둘을 그냥 비교하면 `TypeError` 다. 저장된 시각은 전부 UTC 로 쓰므로, tz 가 없으면 UTC 로 읽는다 —
    **없는 정보를 지어내는 것이 아니라 쓸 때의 약속을 읽을 때 되살리는 것**이다.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _proposal_view(record: Any) -> dict[str, Any]:
    return {
        "proposal_id": str(record.id),
        "kind": record.kind,
        "state": record.state,
        "proposed_by": record.proposed_by,
        "responder_id": record.responder_id,
        "payload": record.payload,
        "reason": record.reason,
        "created_at": _iso(record.created_at),
        "responded_at": _iso(record.responded_at),
    }


#: 상태 투영과 기한 경과일의 **판정 한 자리** (SPEC-005 §4 · 어긋남 ①). 프로젝트 상세도 같은 함수를
#: 지나므로 여기서 이름만 잇는다 — 규칙을 두 벌로 쓰지 않는다.
_external_state = external_state


def _assignment_wait(fact: dict[str, Any]) -> str | None:
    """무엇을 기다리는가 — 담당 행 둘이 답한다 (SPEC-003 §4 `derived.assignment`).

    `pending` 만 있으면 **아직 아무도 들지 않았다**: 요청 수락 대기이거나 첫 지정이다.
    `active` 와 `pending` 이 **함께** 있으면 들고 있는 사람이 있고 교체 제안이 답을 기다린다 —
    그 공존이 「책임 공백 없음」의 모습이다 (정책 V-18).
    """
    if fact.get("pending_assignee_id") is None:
        return None
    return "awaiting_handover" if fact.get("assignee_id") is not None else "awaiting_acceptance"


_overdue_days = overdue_days


def _assignment_view(assignments: Any) -> TaskAssignmentView | None:
    """**지금 이 업무를 든 담당 행.**

    예전에는 마지막에 붙은 행을 그대로 냈다. 담당 변경 제안이 기존 담당을 닫지 않게 된 지금(정책 V-18)
    마지막 행은 **답을 기다리는 제안**이므로, 그것을 내면 아직 수락하지도 않은 사람이 담당자로 읽힌다.
    `active` 를 먼저 찾고, 없으면(수락 대기·첫 지정) 기다리는 행을 낸다 — 그 경우 `status` 가
    `pending` 이라 「아직 아무도 들지 않았다」가 그대로 읽힌다.
    """
    if not assignments:
        return None
    rows = list(assignments)
    current = next((row for row in rows if row.status == "active"), None) or rows[-1]
    return {
        "assignment_id": str(current.id),
        "kind": current.assignment_kind,
        "status": current.status,
        "assigned_by": current.assigned_by,
        "accepted_at": _iso(current.accepted_at),
    }


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


def _hhmm(value: Any) -> str:
    """시각은 `HH:MM` 이다 — 저장은 `Time` 이지만 계약의 정밀도는 분이다 (SPEC-004 §4)."""
    return value.strftime("%H:%M")


def _span_end(span: TaskSpan | None, field: str) -> str | None:
    return getattr(span, field).isoformat() if span is not None else None


def _schedule_view(schedule: Any) -> TaskScheduleView:
    return {
        "schedule_id": str(schedule.id),
        "task_id": str(schedule.task_id),
        "on_date": schedule.on_date.isoformat(),
        "starts_at": _hhmm(schedule.starts_at),
        "ends_at": _hhmm(schedule.ends_at),
        "version": schedule.version,
    }


def _schedule_entry(schedule: Any) -> CalendarScheduleEntry:
    """합본 조회 원소 — `task_id` 는 매달린 업무 행이 이미 갖는다."""
    return {
        "schedule_id": str(schedule.id),
        "on_date": schedule.on_date.isoformat(),
        "starts_at": _hhmm(schedule.starts_at),
        "ends_at": _hhmm(schedule.ends_at),
        "version": schedule.version,
    }


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
