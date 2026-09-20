"""생성 명령 한 자리 — 세 경로가 같은 결정·같은 멱등 계약을 지난다 (WORK-001 Phase 1·2·5·6).

이 application 이 하는 일은 셋이다.

1. `decide_task_creation` 으로 **경로와 검사 종류**를 정하고, 그 경로가 원래 요구하던 역량을 그대로 지난다.
2. **멱등 원장**에 이 의도를 먼저 세운다 — 같은 키의 재전송은 영수증, 다른 내용은 충돌, 동시 실행은 한 건.
3. 실제 생성은 **각 명령을 소유한 application** 에 위임한다. 여기서 업무를 다시 만들지 않는다.

키를 서버가 만들어 채우는 fallback 은 어느 표면에도 없다. 키는 의도가 서는 자리 — 호출자 — 에서만 온다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.application import TaskApplication
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.modules.work.creation import (
    ASSIGN_TASK_COMMAND,
    CREATE_TASK_COMMAND,
    CREATE_WORK_REQUEST_COMMAND,
    TaskCreationContext,
    TaskCreationDecision,
    creation_fingerprint,
    decide_task_creation,
    require_idempotency_key,
)
from ax_workspace.modules.work.errors import (
    TaskAccessDenied,
    TaskError,
    TaskIdempotencyConflict,
    TaskNotFound,
    TaskRecipientNotAllowed,
)
from ax_workspace.modules.work.requests import WorkRequestApplication
from ax_workspace.modules.work.task_creation import TaskCreateInput
from ax_workspace.modules.work.task_results import TaskAssignmentResult, TaskMutationResult


class TaskCreationLedger(Protocol):
    """(행위자 · 명령 종류 · 키) → 만들어진 것. 업무·담당과 같은 transaction 에 선다."""

    def find(self, actor_id: str, command_kind: str, request_key: str) -> Any | None: ...
    def claim(self, actor_id: str, command_kind: str, request_key: str, payload_fingerprint: str) -> Any | None: ...
    def bind(self, attempt: Any, *, task_id: UUID | None = None, work_request_id: UUID | None = None) -> None: ...


class RecipientDirectory(Protocol):
    """「내가 이 사람에게 업무를 보낼 수 있나」 — 후보 목록과 **같은 판정**이다."""

    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool: ...


class TaskCreationApplication:
    def __init__(
        self,
        ledger: TaskCreationLedger,
        tasks: TaskApplication,
        requests: WorkRequestApplication,
        assignments: TaskAssignmentApplication,
        directory: RecipientDirectory,
    ) -> None:
        self._ledger = ledger
        self._tasks = tasks
        self._requests = requests
        self._assignments = assignments
        self._directory = directory

    def create_or_receipt(self, command: Any, *, receipt_only: bool) -> Any:
        """조립 층이 같은 명령을 두 번(생성·영수증) 부를 때 쓰는 자리. 인자 목록을 여기서 다시 적지 않는다."""
        return command(self, receipt_only)

    # ---- POST /api/tasks — 본인 또는 수신자 지정 --------------------------------

    def create_task(
        self,
        principal: Principal,
        title: str,
        *,
        idempotency_key: str | None,
        assignee_id: str | None = None,
        #: 기존 전역 `causation_key` 열 — **뜻이 다르므로 그대로 둔다**. 새 계약은 멱등 원장이 받는다.
        causation_key: str | None = None,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        project_id: UUID | None = None,
        cc_member_ids: list[str] | None = None,
        preceding_task_ids: list[UUID] | None = None,
        approver_id: str | None = None,
        source_action_item_id: UUID | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
        receipt_only: bool = False,
    ) -> TaskMutationResult | None:
        """한 번의 명시적 생성 명령. 담당이 없거나 나면 내 업무, 다르면 **수락 없이** 그 사람의 업무다.

        `receipt_only` 는 조립 층이 동시 실행에서 진 transaction 을 버린 뒤 다시 부르는 길이다 —
        이긴 쪽의 결과만 읽고 아무것도 만들지 않는다. 원장에 그 키가 없으면 `None` 이고, 그때 조립 층은
        원래의 제약 위반을 그대로 올린다 (다른 제약이 깨진 것을 영수증으로 위장하지 않는다).
        """
        key = require_idempotency_key(idempotency_key)
        try:
            command = TaskCreateInput(
                title=title, assignee_id=assignee_id, description=description, start_date=start_date,
                due_date=due_date, checklist=checklist, reference_task_ids=reference_task_ids,
                parent_task_id=parent_task_id, project_id=project_id, cc_member_ids=cc_member_ids,
                preceding_task_ids=preceding_task_ids, approver_id=approver_id,
            ).for_owner(str(principal.id))
        except ValueError as error:
            raise TaskError(str(error)) from error
        decision = self._decide(principal, command.assignee_id, managed=False)
        fingerprint = creation_fingerprint({
            "command": CREATE_TASK_COMMAND,
            "assignee_id": decision.assignee_id,
            **command.model_dump(mode="json", exclude={"assignee_id"}),
        })
        if receipt_only:
            return self._task_receipt(principal, CREATE_TASK_COMMAND, key, fingerprint, absent_is_none=True)
        attempt = self._ledger.claim(str(principal.id), CREATE_TASK_COMMAND, key, fingerprint)
        if attempt is None:
            return self._task_receipt(principal, CREATE_TASK_COMMAND, key, fingerprint)
        if decision.route == "self":
            created = self._tasks.create_self(
                principal, command.title, causation_key,
                description=command.description, start_date=command.start_date, due_date=command.due_date,
                checklist=command.checklist, reference_task_ids=command.reference_task_ids,
                parent_task_id=command.parent_task_id, project_id=command.project_id,
                cc_member_ids=command.cc_member_ids,
                preceding_task_ids=command.preceding_task_ids,
                approver_id=command.approver_id,
                source_action_item_id=source_action_item_id,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )
            self._ledger.bind(attempt, task_id=UUID(str(created["task_id"])))
            return created
        # 수평 요청은 출처 행을 계속 세운다 — 업무가 `source_work_request_id` 로 그것을 가리키고
        # 완료 승인의 확인자가 거기서 나온다 (WORK-001 § 최소 호환 경계).
        # AX 확인이 이 생성을 있게 했으면 그 계보도 함께 간다. 사람이 초안에서 담당을 골라 이 갈래로
        # 와도 업무는 자기를 만든 action 을 계속 가리킨다 — 버리지 않는다.
        self._refuse_unsupported_horizontal_fields(command)
        request = self._requests.create(
            principal, command.title, decision.assignee_id, causation_key,
            description=command.description, due_date=command.due_date,
            checklist=command.checklist, reference_task_ids=command.reference_task_ids,
            # 참조자와 결재자는 **두 갈래 모두 지나간다** — 공통 payload 의 칸이므로 남에게 보내는
            # 생성에서도 버리지 않는다. 결재자 판정(재직·담당자 본인 불가)은 요청 쪽이 같은 규칙으로 한다.
            cc_member_ids=command.cc_member_ids,
            approver_id=command.approver_id,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
        )
        task_id = UUID(str(request["task_id"]))
        self._ledger.bind(attempt, task_id=task_id, work_request_id=UUID(str(request["request_id"])))
        return self._tasks.get(principal, task_id)

    @staticmethod
    def _refuse_unsupported_horizontal_fields(command: TaskCreateInput) -> None:
        """남에게 보내는 생성이 받지 않는 값은 **조용히 버리지 않고** 거절한다.

        셋은 이제 `work_requests` 에 **열이 있다** — 그래서 이 거절은 저장의 한계가 아니라 **이 표면의
        계약**이다. `POST /api/tasks` 에 담당을 적는 길은 「내 업무를 만드는 명령에 수신자를 하나 적었다」는
        좁은 갈래이고, 시작일·상위·프로젝트까지 실어 보내는 것은 **업무 요청의 일**이다
        (`POST /api/work-requests` 가 같은 값을 전부 받는다). 여기서 조용히 열면 같은 요청을 세우는 길이
        둘이 되고, 둘 중 하나만 고쳤을 때 조용히 갈린다. 참조자(cc)는 이 목록에 **없다** — 요청이 원래
        받던 값이고 공통 payload 의 칸이라 그대로 흘러간다.
        """
        unsupported = [
            name
            for name, value in (
                ("start_date", command.start_date),
                ("parent_task_id", command.parent_task_id),
                ("project_id", command.project_id),
                # 선행은 **프로젝트 안에서만** 서는데 이 갈래가 프로젝트를 받지 않는다. 받아 두고
                # 프로젝트 미선택으로 다시 거절하면 사람이 두 걸음을 헛디딘다 — 이름으로 한 번에 말한다.
                ("preceding_task_ids", command.preceding_task_ids or None),
                # **결재자는 이 목록에 없다.** 요청 갈래가 그 값을 받아 저장하게 된 뒤로, 여기서만
                # 거절하면 같은 일을 세우는 두 길(`POST /api/tasks` + 담당 · `POST /api/work-requests`)의
                # 답이 갈린다. 아래에서 요청으로 그대로 흘려보낸다.
            )
            if value is not None
        ]
        if unsupported:
            raise TaskError(
                f"담당을 지정한 생성에는 쓸 수 없는 항목입니다: {', '.join(unsupported)}"
            )

    # ---- POST /api/work-requests — 수평 요청 그대로의 표면 ----------------------

    def create_work_request(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        *,
        idempotency_key: str | None,
        causation_key: str | None = None,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        project_id: UUID | None = None,
        approver_id: str | None = None,
        cc_member_ids: list[str] | None = None,
        preceding_task_ids: list[UUID] | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        source_meeting_id: UUID | None = None,
        source_agenda_id: UUID | None = None,
        parent_task_id: UUID | None = None,
        supersedes_request_id: UUID | None = None,
        allow_self_assignment: bool = False,
        promoted_by_member_id: str | None = None,
        source_action_item_id: UUID | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
        receipt_only: bool = False,
    ) -> dict[str, Any] | None:
        key = require_idempotency_key(idempotency_key)
        fingerprint = creation_fingerprint({
            "command": CREATE_WORK_REQUEST_COMMAND,
            "title": " ".join(str(title).split()),
            "assignee_id": assignee_id,
            "description": (description or "").strip() or None,
            # 같은 키에 다른 시작일·다른 프로젝트가 오면 **다른 내용**이다 — 지문에 함께 싣는다.
            "start_date": start_date,
            "due_date": due_date,
            "project_id": str(project_id) if project_id else None,
            # 같은 키에 **다른 결재자**가 오면 다른 내용이다.
            "approver_id": approver_id,
            "cc_member_ids": sorted(cc_member_ids or []),
            # 같은 키에 **다른 선행**이 오면 다른 내용이다. 순서까지 지문에 싣는다 — 화면이 고른 순서가
            # 저장 순서이고, 그것이 다른 두 명령을 한 건으로 합치지 않는다.
            "preceding_task_ids": [str(item) for item in (preceding_task_ids or [])],
            "checklist": list(checklist or []),
            "reference_task_ids": [str(item) for item in (reference_task_ids or [])],
            "source_meeting_id": source_meeting_id,
            "source_agenda_id": source_agenda_id,
            # 같은 키에 **다른 상위**나 다른 이전 요청이 오면 다른 내용이다 — 지문에 함께 싣는다.
            "parent_task_id": str(parent_task_id) if parent_task_id else None,
            "supersedes_request_id": str(supersedes_request_id) if supersedes_request_id else None,
        })
        if receipt_only:
            return self._request_receipt(principal, CREATE_WORK_REQUEST_COMMAND, key, fingerprint, absent_is_none=True)
        attempt = self._ledger.claim(str(principal.id), CREATE_WORK_REQUEST_COMMAND, key, fingerprint)
        if attempt is None:
            return self._request_receipt(principal, CREATE_WORK_REQUEST_COMMAND, key, fingerprint)
        created = self._requests.create(
            principal, title, assignee_id, causation_key,
            description=description, start_date=start_date, due_date=due_date, project_id=project_id,
            approver_id=approver_id,
            cc_member_ids=cc_member_ids, preceding_task_ids=preceding_task_ids, checklist=checklist,
            reference_task_ids=reference_task_ids, source_meeting_id=source_meeting_id,
            source_agenda_id=source_agenda_id,
            parent_task_id=parent_task_id, supersedes_request_id=supersedes_request_id,
            allow_self_assignment=allow_self_assignment,
            promoted_by_member_id=promoted_by_member_id,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
        )
        self._ledger.bind(
            attempt,
            work_request_id=UUID(str(created["request_id"])),
            task_id=UUID(str(created["task_id"])) if created.get("task_id") else None,
        )
        return created

    # ---- POST /api/tasks/assign — 관리자 직접 배정 -----------------------------

    def assign_task(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        *,
        idempotency_key: str | None,
        receipt_only: bool = False,
        **fields: Any,
    ) -> TaskAssignmentResult | None:
        key = require_idempotency_key(idempotency_key)
        # 관리자 경로의 역량(`task.assign`)과 조직 범위 검사는 배정 application 이 그대로 갖는다.
        # 결정 함수는 「본인에게 배정하지 않는다」는 경계만 여기서 한 번 더 세운다.
        decide_task_creation(TaskCreationContext(actor_id=str(principal.id), assignee_id=assignee_id, managed=True))
        fingerprint = creation_fingerprint({
            "command": ASSIGN_TASK_COMMAND,
            "title": " ".join(str(title).split()),
            "assignee_id": assignee_id,
            **{name: value for name, value in sorted(fields.items()) if not name.startswith("source_")},
        })
        if receipt_only:
            return self._assignment_receipt(principal, ASSIGN_TASK_COMMAND, key, fingerprint, absent_is_none=True)
        attempt = self._ledger.claim(str(principal.id), ASSIGN_TASK_COMMAND, key, fingerprint)
        if attempt is None:
            return self._assignment_receipt(principal, ASSIGN_TASK_COMMAND, key, fingerprint)
        result = self._assignments.assign(principal, title, assignee_id, **fields)
        self._ledger.bind(attempt, task_id=UUID(str(result["task"]["task_id"])))
        return result

    # ---- 영수증 — 돌려주기 **전에** 지금 읽을 수 있는지 다시 묻는다 -------------

    def _claimed(
        self, principal: Principal, command_kind: str, key: str, fingerprint: str, *, absent_is_none: bool
    ) -> Any:
        attempt = self._ledger.find(str(principal.id), command_kind, key)
        if attempt is None:
            if absent_is_none:
                return None
            raise TaskError("생성 원장을 찾지 못했습니다")
        if attempt.payload_fingerprint != fingerprint:
            raise TaskIdempotencyConflict("같은 요청 키로 다른 내용을 보낼 수 없습니다")
        return attempt

    def _task_receipt(
        self, principal: Principal, command_kind: str, key: str, fingerprint: str, *, absent_is_none: bool = False
    ) -> TaskMutationResult | None:
        attempt = self._claimed(principal, command_kind, key, fingerprint, absent_is_none=absent_is_none)
        if attempt is None:
            return None
        if attempt.task_id is None:
            raise TaskNotFound("task was not found")
        return self._tasks.creation_receipt(principal, attempt.task_id)

    def _request_receipt(
        self, principal: Principal, command_kind: str, key: str, fingerprint: str, *, absent_is_none: bool = False
    ) -> dict[str, Any] | None:
        attempt = self._claimed(principal, command_kind, key, fingerprint, absent_is_none=absent_is_none)
        if attempt is None:
            return None
        if attempt.work_request_id is None:
            raise TaskNotFound("work request was not found")
        return self._requests.receipt(principal, attempt.work_request_id)

    def _assignment_receipt(
        self, principal: Principal, command_kind: str, key: str, fingerprint: str, *, absent_is_none: bool = False
    ) -> TaskAssignmentResult | None:
        attempt = self._claimed(principal, command_kind, key, fingerprint, absent_is_none=absent_is_none)
        if attempt is None:
            return None
        if attempt.task_id is None:
            raise TaskNotFound("task was not found")
        return self._assignments.assignment_receipt(principal, attempt.task_id)

    # ---- 권한 ------------------------------------------------------------------

    def _decide(self, principal: Principal, assignee_id: str | None, *, managed: bool) -> TaskCreationDecision:
        decision = decide_task_creation(
            TaskCreationContext(actor_id=str(principal.id), assignee_id=assignee_id, managed=managed)
        )
        if decision.required_capability not in principal.capabilities:
            raise TaskAccessDenied(f"{decision.required_capability} capability is required")
        if decision.recipient_check == "work_request_candidate" and not self._directory.is_work_request_assignee(
            principal, decision.assignee_id
        ):
            raise TaskRecipientNotAllowed("이 사람에게는 업무를 보낼 수 없습니다")
        return decision
