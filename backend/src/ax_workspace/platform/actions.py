"""PostgreSQL adapter for AX confirmation Actions and their canonical effects."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.reports.application import DailyReportApplication
from ax_workspace.modules.ax_execution.actions import (
    ACTION_ITEM_COMMAND,
    ACTION_ITEM_COMMAND_TITLE,
    ActionError,
    action_payload_hash,
)
from ax_workspace.modules.work.requests import WorkRequestApplication
from ax_workspace.modules.work.application import TaskApplication, TaskError, TaskState
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import (
    ActionItemAuditEventRecord,
    ActionItemRecord,
    ConversationMaterialEvidenceRecord,
    ConversationRecord,
    ConversationTurnRecord,
)
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.platform.work_tasks import (
    caused_by,
    SqlAlchemyTaskAssignmentRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRecordSource,
    SqlAlchemyWorkRequestRepository,
)


class SqlAlchemyActionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._presenter = ActionPresenter(session)

    def propose(
        self,
        owner_id: str,
        execution_id: UUID,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> ActionItemRecord:
        """Return one recoverable effect slot per action type and turn execution.

        A provider redelivery may produce a different payload for the same
        delegated turn. It must reuse the first proposal rather than create a
        second effect. Intentional repeated effects belong in a later turn.
        """
        payload_hash = action_payload_hash(payload)
        turn = self._session.scalar(
            select(ConversationTurnRecord)
            .join(ConversationRecord, ConversationRecord.id == ConversationTurnRecord.conversation_id)
            .where(
                ConversationTurnRecord.execution_id == execution_id,
                ConversationRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if turn is None:
            raise ValueError("delegated action execution was not found")
        existing = self._session.scalar(
            select(ActionItemRecord).where(
                ActionItemRecord.execution_id == execution_id,
                ActionItemRecord.action_type == action_type,
            )
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        action = ActionItemRecord(
            owner_id=owner_id,
            conversation_id=turn.conversation_id,
            turn_id=turn.id,
            execution_id=execution_id,
            action_type=action_type,
            title=title,
            payload=payload,
            payload_hash=payload_hash,
            state="pending",
            version=1,
            result=None,
            audit_ref=None,
            created_at=now,
            decided_at=None,
        )
        self._session.add(action)
        self._session.flush()
        self._audit(action, owner_id, "action.proposed", {})
        return action

    def action(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> ActionItemRecord | None:
        statement = select(ActionItemRecord).where(
            ActionItemRecord.id == action_id,
            ActionItemRecord.owner_id == owner_id,
        )
        return self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    def subject_label(self, action: ActionItemRecord) -> str:
        return action_subject_label(action)

    def list_for(self, owner_id: str) -> list[ActionItemRecord]:
        return list(
            self._session.scalars(
                select(ActionItemRecord)
                .where(ActionItemRecord.owner_id == owner_id)
                .order_by(ActionItemRecord.created_at.desc(), ActionItemRecord.id.desc())
            )
        )

    def for_conversation(self, conversation_id: UUID) -> list[ActionItemRecord]:
        return list(
            self._session.scalars(
                select(ActionItemRecord)
                .where(ActionItemRecord.conversation_id == conversation_id)
                .order_by(ActionItemRecord.created_at, ActionItemRecord.id)
            )
        )

    def resolve(
        self,
        action: ActionItemRecord,
        actor_id: str,
        decision: str,
        result: dict[str, Any] | None,
    ) -> None:
        action.state = "approved" if decision == "approve" else "rejected"
        action.version += 1
        action.result = result
        action.audit_ref = str(action.id)
        action.decided_at = datetime.now(UTC)
        self._audit(action, actor_id, f"action.{action.state}", {"result": result or {}})

    def view(self, action: ActionItemRecord, principal: Principal | None = None) -> dict[str, Any]:
        """Canonical Action row plus its structured, permission-safe presentation for `principal`."""
        return {
            "action_id": str(action.id),
            "conversation_id": str(action.conversation_id),
            "turn_id": str(action.turn_id),
            "action_type": action.action_type,
            "title": action.title,
            "state": action.state,
            "version": action.version,
            "payload_hash": action.payload_hash,
            "payload_summary": self._summary(action),
            "result": action.result,
            "audit_ref": action.audit_ref,
            **self._presenter.present(action, principal),
        }

    def _audit(self, action: ActionItemRecord, actor_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._session.add(
            ActionItemAuditEventRecord(
                action_id=action.id,
                actor_id=actor_id,
                event_type=event_type,
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _summary(action: ActionItemRecord) -> str:
        if action.action_type == "work_request.create":
            return f"업무 요청: {action.payload['title']}"
        if action.action_type == "daily_report.edit":
            return "일일보고 초안 수정"
        if action.action_type.startswith("task.checklist."):
            return _OPERATION_LABELS.get(action.action_type, action.action_type)
        if action.action_type == "task.assign":
            return f"업무 배정: {action.payload['title']} → {action.payload['assignee_id']}"
        if action.action_type == ACTION_ITEM_COMMAND:
            # Deliberately says nothing about the target: the row outlives the reader's access to the work it names.
            return ACTION_ITEM_COMMAND_TITLE
        return action.action_type


def action_center_application(session: Session, executor: Any) -> Any:
    """The one judgement application, built here so the wrapper runs exactly what HTTP and the UI run."""
    # Imported late: the ActionCenter presents AX proposals through this module.
    from ax_workspace.modules.ax_execution.actions import ActionApplication
    from ax_workspace.modules.actions.domain import ActionCenterApplication
    from ax_workspace.platform.action_center import action_handlers

    return ActionCenterApplication(
        action_handlers(
            session,
            work_requests=WorkRequestApplication(
                SqlAlchemyWorkRequestRepository(session),
                OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
            ),
            actions=ActionApplication(SqlAlchemyActionRepository(session), executor),
            assignments=TaskAssignmentApplication(
                SqlAlchemyTaskAssignmentRepository(session),
                OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
            ),
        )
    )


class SqlAlchemyActionExecutor:
    """Invokes existing public application commands inside the Action transaction."""

    def __init__(self, session: Session, report_provider: Any) -> None:
        self._session = session
        self._report_provider = report_provider

    def execute(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        # Everything this approval causes says which confirmation carried it; the actor stays the approver.
        with caused_by(f"action_item:{action.id}"):
            return self._execute(principal, action)

    def _execute(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        if action.action_type == "work_request.create":
            return WorkRequestApplication(
                SqlAlchemyWorkRequestRepository(self._session),
                OrganizationApplication(SqlAlchemyOrganizationRepository(self._session)),
            ).create(
                principal,
                str(action.payload["title"]),
                str(action.payload["assignee_id"]),
                causation_key=str(action.id),
                description=action.payload.get("description"),
                due_date=_parse_date(action.payload.get("due_date")),
                cc_member_ids=list(action.payload.get("cc_member_ids") or []),
                checklist=list(action.payload.get("checklist") or []),
                reference_task_ids=[UUID(str(item)) for item in action.payload.get("reference_task_ids") or []],
            )
        if action.action_type == "daily_report.edit":
            return DailyReportApplication(
                SqlAlchemyDailyReportRepository(self._session),
                SqlAlchemyDailyReportDraftWorkflow(
                    self._session,
                    SqlAlchemyWorkRecordSource(self._session),
                    self._report_provider,
                ),
            ).edit(
                principal,
                str(action.payload["report_id"]),
                str(action.payload["draft_id"]),
                int(action.payload["expected_version"]),
                str(action.payload["body"]),
                list(action.payload.get("include_source_refs", [])),
                list(action.payload.get("exclude_source_refs", [])),
            )
        if action.action_type == "daily_report.submit":
            return DailyReportApplication(
                SqlAlchemyDailyReportRepository(self._session),
                SqlAlchemyDailyReportDraftWorkflow(self._session, SqlAlchemyWorkRecordSource(self._session), self._report_provider),
            ).submit(principal, str(action.payload["report_id"]), str(action.payload["draft_id"]), int(action.payload["expected_version"]), action.payload.get("reason"))
        if action.action_type == "task.create_self":
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).create_self(
                principal, str(action.payload["title"]), causation_key=str(action.id), source_action_item_id=action.id,
                checklist=list(action.payload.get("checklist") or []),
                reference_task_ids=[UUID(str(item)) for item in action.payload.get("reference_task_ids") or []],
            )
        if action.action_type == "task.update":
            changes = dict(action.payload.get("changes", {}))
            for field in ("start_date", "due_date"):
                if field in changes:
                    changes[field] = _parse_date(changes[field])
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).update(
                UUID(str(action.payload["task_id"])), principal, int(action.payload["expected_version"]), changes
            )
        if action.action_type.startswith("task.checklist."):
            return self._run_checklist_command(principal, action)
        if action.action_type == "task.assign":
            return self._assignments().assign(
                principal, str(action.payload["title"]), str(action.payload["assignee_id"]),
                description=action.payload.get("description"),
                start_date=_parse_date(action.payload.get("start_date")),
                due_date=_parse_date(action.payload.get("due_date")),
                causation_key=str(action.id),
                checklist=list(action.payload.get("checklist") or []),
            )
        if action.action_type == "task.assignment.accept":
            return self._assignments().accept(principal, UUID(str(action.payload["assignment_id"])))
        if action.action_type == "task.assignment.decline":
            return self._assignments().decline(principal, UUID(str(action.payload["assignment_id"])), str(action.payload.get("reason") or ""))
        if action.action_type == "task.transition":
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).transition(
                UUID(str(action.payload["task_id"])), principal, TaskState(str(action.payload["target"])),
                action.payload.get("reason"), int(action.payload["expected_version"])
            )
        if action.action_type == "work_request.accept":
            return WorkRequestApplication(SqlAlchemyWorkRequestRepository(self._session), OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))).accept(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]))
        if action.action_type == "work_request.reject":
            return WorkRequestApplication(SqlAlchemyWorkRequestRepository(self._session), OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))).reject(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]), str(action.payload["reason"]))
        if action.action_type == ACTION_ITEM_COMMAND:
            return self._run_action_item_command(principal, action)
        if action.action_type == "work_request.amend":
            return WorkRequestApplication(
                SqlAlchemyWorkRequestRepository(self._session),
                OrganizationApplication(SqlAlchemyOrganizationRepository(self._session)),
            ).amend(
                principal,
                UUID(str(action.payload["request_id"])),
                int(action.payload["expected_version"]),
                title=action.payload.get("title"),
                description=action.payload.get("description"),
                due_date=_parse_date(action.payload.get("due_date")),
                clear_due_date=bool(action.payload.get("clear_due_date")),
            )
        if action.action_type == "work_request.negotiate":
            return WorkRequestApplication(SqlAlchemyWorkRequestRepository(self._session), OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))).negotiate(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]), dict(action.payload["conditions"]))
        raise ValueError("unsupported action type")

    def _run_checklist_command(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        """A checklist change a delegated turn prepared, applied once by the person who approved it."""
        tasks = TaskApplication(
            SqlAlchemyTaskRepository(self._session),
            SqlAlchemyWorkRequestRepository(self._session),
            SqlAlchemyActionRepository(self._session),
        )
        payload = dict(action.payload)
        task_id = UUID(str(payload["task_id"]))
        if action.action_type == "task.checklist.add":
            return tasks.add_checklist_item(principal, task_id, str(payload["text"]))
        if action.action_type == "task.checklist.reorder":
            return tasks.reorder_checklist(principal, task_id, [UUID(str(item)) for item in payload["item_ids"]])
        item_id = UUID(str(payload["item_id"]))
        expected_version = int(payload["expected_version"])
        if action.action_type == "task.checklist.archive":
            return tasks.archive_checklist_item(principal, task_id, item_id, expected_version=expected_version)
        changes: dict[str, Any] = {"expected_version": expected_version}
        if payload.get("text") is not None:
            changes["text"] = str(payload["text"])
        if payload.get("done") is not None:
            changes["done"] = bool(payload["done"])
        return tasks.update_checklist_item(principal, task_id, item_id, **changes)

    def _run_action_item_command(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        """Apply the judgement a delegated turn prepared, through the one canonical command path.

        The approving person's own authority is what runs it: the ActionCenter re-checks that the command is still
        offered to them on that item and that the version they are answering is still the current one.
        """
        payload = action.payload or {}
        center = action_center_application(self._session, self)
        target = str(payload["action_item_id"])
        envelope = center.detail(principal, target)
        if envelope.get("expected_version") != payload.get("expected_version"):
            raise ActionError("대상이 바뀌어 이 확인은 더 이상 쓸 수 없습니다. 판단을 다시 준비하세요")
        if str(envelope.get("kind", "")).startswith("ax."):
            # The gate exists to put a person between AX and the effect; approving one gate must not open another.
            raise ActionError("an AX proposal cannot be decided by another proposal")
        return center.execute(
            principal,
            target,
            str(payload["command"]),
            {key: payload[key] for key in ("expected_version", "reason", "changes") if key in payload},
        )

    def _assignments(self) -> TaskAssignmentApplication:
        return TaskAssignmentApplication(
            SqlAlchemyTaskAssignmentRepository(self._session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(self._session)),
        )


def _parse_date(value: Any):
    if value in (None, ""):
        return None
    from datetime import date

    return date.fromisoformat(str(value))


# ---- structured, permission-safe Action presentation -------------------------------------------------------------

_OPERATION_LABELS: dict[str, str] = {
    "task.create_self": "업무 생성",
    "work_request.create": "업무 요청",
    "task.assign": "업무 배정",
    "task.update": "업무 수정",
    "task.transition": "업무 상태 변경",
    "task.assignment.accept": "배정 수락",
    "task.assignment.decline": "배정 거절",
    "work_request.accept": "요청 수락",
    "work_request.reject": "요청 거절",
    "work_request.negotiate": "요청 조건 협의",
    "work_request.amend": "요청 수정",
    "daily_report.edit": "일일보고 수정",
    "daily_report.submit": "일일보고 제출",
    "task.checklist.add": "체크리스트 단계 추가",
    "task.checklist.update": "체크리스트 단계 수정",
    "task.checklist.archive": "체크리스트 단계 정리",
    "task.checklist.reorder": "체크리스트 순서 변경",
}
_TASK_STATE_LABELS: dict[str, str] = {"open": "대기", "in_progress": "진행 중", "blocked": "막힘", "done": "완료", "cancelled": "취소"}
_UNKNOWN_MEMBER = "확인할 수 없는 구성원"
_CREATION_KINDS = {"task.create_self", "work_request.create", "task.assign"}


def action_subject_label(action: ActionItemRecord) -> str:
    """What the proposal is about, in the words of the work itself rather than the confirmation wording."""
    if action.action_type in _CREATION_KINDS:
        title = (action.payload or {}).get("title")
        if title:
            return str(title)
    return str(action.title)


class _NoEffectExecutor:
    """Reading a judgement never runs one; the presenter builds the ledger with an executor that refuses to act."""

    def execute(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        raise ValueError("the presenter never executes an action")


_NO_EFFECT_EXECUTOR = _NoEffectExecutor()


class ActionPresenter:
    """Server-side preview of what approving an Action will actually do.

    The client renders `subject`, `operation_label`, and `preview` label/value rows verbatim and never derives fields or
    controls from `action_type` or the raw payload. Only fields present in the actual command are emitted; member names
    are resolved through the principal's organization visibility and referenced Tasks only when the principal can read
    them, so the preview never widens what the approver may see.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._name_cache: dict[str, dict[str, str]] = {}
        self._evidence_cache: dict[UUID, list[ConversationMaterialEvidenceRecord]] = {}
        self._readable_task_cache: dict[tuple[str, UUID], bool] = {}

    def present(self, action: ActionItemRecord, principal: Principal | None) -> dict[str, Any]:
        payload = action.payload or {}
        kind = action.action_type
        fields: list[dict[str, str]] = []
        subject = action.title
        # Only a confirmation of another judgement can go out of date; every other proposal carries its own effect.
        obsolete = False

        if kind in _CREATION_KINDS:
            subject = action_subject_label(action)
            self._text(fields, "description", "설명", payload.get("description"))
            if kind == "work_request.create":
                self._person(fields, "requester", "요청자", action.owner_id, principal)
                self._person(fields, "assignee", "요청 대상", payload.get("assignee_id"), principal)
            elif kind == "task.assign":
                self._person(fields, "assignee", "담당 후보", payload.get("assignee_id"), principal)
            else:
                self._person(fields, "assignee", "담당", action.owner_id, principal)
            self._date(fields, "start_date", "시작일", payload.get("start_date"))
            self._date(fields, "due_date", "기한", payload.get("due_date"))
            pointers = [str(item) for item in payload.get("reference_task_ids") or []]
            if pointers:
                titles = [self._readable_task_title(item, principal) for item in pointers]
                named = [title for title in titles if title]
                hidden = len(titles) - len(named)
                value = " · ".join(named) if named else ""
                if hidden:
                    value = f"{value} · 볼 수 없는 업무 {hidden}건".strip(" ·")
                fields.append({"id": "references", "label": "참고 업무", "value": value, "kind": "text"})
            steps = [str(step) for step in payload.get("checklist") or []]
            if steps:
                fields.append({
                    "id": "checklist", "label": "체크리스트",
                    "value": f"{len(steps)}단계 · " + " → ".join(steps), "kind": "text",
                })
            cc = [str(member) for member in payload.get("cc_member_ids") or []]
            if cc:
                names = self._names(principal)
                fields.append({"id": "cc", "label": "참조자", "value": ", ".join(names.get(member, _UNKNOWN_MEMBER) for member in cc), "kind": "people"})
        elif kind in {"task.update", "task.transition"}:
            task_title = self._readable_task_title(payload.get("task_id"), principal)
            if task_title is not None:
                subject = task_title
                fields.append({"id": "task", "label": "대상 업무", "value": task_title, "kind": "text"})
            if kind == "task.update":
                changes = dict(payload.get("changes") or {})
                self._text(fields, "title", "제목", changes.get("title"))
                self._text(fields, "description", "설명", changes.get("description"))
                self._date(fields, "start_date", "시작일", changes.get("start_date"))
                self._date(fields, "due_date", "기한", changes.get("due_date"))
            else:
                target = str(payload.get("target") or "")
                if target:
                    fields.append({"id": "target", "label": "변경 상태", "value": _TASK_STATE_LABELS.get(target, target), "kind": "state"})
                self._text(fields, "reason", "사유", payload.get("reason"))
        elif kind.startswith("task.checklist."):
            task_title = self._readable_task_title(payload.get("task_id"), principal)
            if task_title is not None:
                subject = task_title
                fields.append({"id": "task", "label": "대상 업무", "value": task_title, "kind": "text"})
            if kind == "task.checklist.add":
                self._text(fields, "text", "추가할 단계", payload.get("text"))
            elif kind == "task.checklist.update":
                self._text(fields, "text", "고칠 내용", payload.get("text"))
                if payload.get("done") is not None:
                    fields.append({
                        "id": "done", "label": "완료 여부",
                        "value": "완료로 표시" if payload.get("done") else "완료 해제", "kind": "state",
                    })
            elif kind == "task.checklist.reorder":
                fields.append({
                    "id": "order", "label": "새 순서",
                    "value": f"{len(payload.get('item_ids') or [])}단계를 다시 정렬", "kind": "text",
                })
            self._checklist_step(fields, payload, principal)
        elif kind in {"task.assignment.decline", "work_request.reject", "daily_report.submit"}:
            self._text(fields, "reason", "사유", payload.get("reason"))
        elif kind == "work_request.amend":
            self._text(fields, "title", "제목", payload.get("title"))
            self._text(fields, "description", "설명", payload.get("description"))
            if payload.get("clear_due_date"):
                fields.append({"id": "due_date", "label": "기한", "value": "없앰", "kind": "state"})
            else:
                self._date(fields, "due_date", "기한", payload.get("due_date"))
        elif kind == "work_request.negotiate":
            conditions = dict(payload.get("conditions") or {})
            self._date(fields, "due_date", "제안 기한", conditions.get("due_date"))
            self._text(fields, "note", "메모", conditions.get("note") or conditions.get("reason"))
        elif kind == ACTION_ITEM_COMMAND:
            return self._action_item_command(action, payload, principal, fields)

        self._evidence(fields, action, principal)
        return {"subject": subject, "operation_label": _OPERATION_LABELS.get(kind, kind), "preview": fields, "obsolete": obsolete}

    def _checklist_step(self, fields: list[dict[str, str]], payload: dict[str, Any], principal: Principal | None) -> None:
        """Which step is being changed, named by its own words rather than by an id nobody can read."""
        item_id = payload.get("item_id")
        task_id = payload.get("task_id")
        if not item_id or not task_id or principal is None:
            return
        from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository

        repository = SqlAlchemyTaskRepository(self._session)
        try:
            task = repository.task(UUID(str(task_id)), str(principal.id))
        except Exception:  # not this person's task: the card says nothing about it
            return
        for item in repository.checklist_for(task.id, include_archived=True):
            if str(item.id) == str(item_id):
                fields.append({"id": "step", "label": "대상 단계", "value": item.text, "kind": "text"})
                return

    def _action_item_command(
        self,
        action: ActionItemRecord,
        payload: dict[str, Any],
        principal: Principal | None,
        fields: list[dict[str, str]],
    ) -> dict[str, Any]:
        """A judgement a delegated turn prepared, shown as the target's own permission-safe card.

        The approver sees the work being judged and the answer that would be given — never the raw wire payload, and
        never more of the target than that person may already read.
        """
        target = self._target_envelope(payload.get("action_item_id"), principal)
        if target is None:
            # Not readable now, whatever was readable when the turn proposed it. Nobody can confirm a judgement they
            # cannot see, so the card offers only the way out rather than an approval that would fail on use.
            return {"subject": ACTION_ITEM_COMMAND_TITLE, "operation_label": "판단 확인", "preview": [], "obsolete": True}
        # A confirmation answers one moment. If the target has moved since, approving it would apply an answer to
        # something else, so the card says so rather than waiting to fail.
        obsolete = target.get("expected_version") != payload.get("expected_version")
        if obsolete:
            fields.append({"id": "obsolete", "label": "상태", "value": "대상이 바뀌어 이 확인은 더 이상 쓸 수 없습니다", "kind": "state"})
        command = str(payload.get("command") or "")
        label = next((entry["label"] for entry in target.get("allowed_commands", []) if entry["id"] == command), command)
        fields.append({"id": "command", "label": "판단", "value": label, "kind": "state"})
        self._text(fields, "question", "질문", target.get("current_question"))
        self._text(fields, "reason", "사유", payload.get("reason"))
        changes = dict(payload.get("changes") or {})
        self._text(fields, "changes_title", "제안 제목", changes.get("title"))
        self._text(fields, "changes_description", "제안 설명", changes.get("description"))
        self._date(fields, "changes_due_date", "제안 기한", changes.get("due_date"))
        # The target's own preview rows are already permission-safe for this principal.
        fields.extend(target.get("preview", []))
        self._evidence(fields, action, principal)
        return {
            "subject": str(target.get("subject") or action.title),
            "operation_label": f"{target.get('operation_label', '판단')} 판단",
            "preview": fields,
            "obsolete": obsolete,
        }

    def _target_envelope(self, action_item_id: Any, principal: Principal | None) -> dict[str, Any] | None:
        if principal is None or not action_item_id:
            return None
        try:
            return action_center_application(self._session, _NO_EFFECT_EXECUTOR).detail(principal, str(action_item_id))
        except Exception:
            # The approver cannot read the target: say nothing about it rather than widen what they may see.
            return None

    def _evidence(self, fields: list[dict[str, str]], action: ActionItemRecord, principal: Principal | None) -> None:
        """Attachments the proposing turn actually read, so the approver sees what the proposal is grounded in.

        These are links, not fields the command creates: no current command attaches files. Access is re-checked per
        approver against the owning Task, so evidence from a Task they can no longer read disappears from the preview.
        """
        if principal is None:
            return
        # Dedupe by material, not by file name: two distinct attachments may share a name and both are real sources.
        seen: set[UUID] = set()
        names: list[str] = []
        for item in self._turn_evidence(action.turn_id):
            if item.material_id in seen or not self._can_read_task(principal, item.task_id):
                continue
            seen.add(item.material_id)
            names.append(item.name)
        if names:
            fields.append({"id": "evidence", "label": "근거 자료", "value": ", ".join(names), "kind": "evidence"})

    def _turn_evidence(self, turn_id: UUID) -> list[ConversationMaterialEvidenceRecord]:
        cached = self._evidence_cache.get(turn_id)
        if cached is None:
            cached = list(
                self._session.scalars(
                    select(ConversationMaterialEvidenceRecord)
                    .where(ConversationMaterialEvidenceRecord.turn_id == turn_id)
                    .order_by(ConversationMaterialEvidenceRecord.rank, ConversationMaterialEvidenceRecord.id)
                )
            )
            self._evidence_cache[turn_id] = cached
        return cached

    def _can_read_task(self, principal: Principal, task_id: UUID) -> bool:
        key = (str(principal.id), task_id)
        cached = self._readable_task_cache.get(key)
        if cached is None:
            try:
                TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).get(principal, task_id)
                cached = True
            except (TaskError, ValueError):
                cached = False
            self._readable_task_cache[key] = cached
        return cached

    @staticmethod
    def _text(fields: list[dict[str, str]], field_id: str, label: str, value: Any) -> None:
        if value in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": str(value), "kind": "text"})

    @staticmethod
    def _date(fields: list[dict[str, str]], field_id: str, label: str, value: Any) -> None:
        if value in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": str(value), "kind": "date"})

    def _person(self, fields: list[dict[str, str]], field_id: str, label: str, member_id: Any, principal: Principal | None) -> None:
        if member_id in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": self._names(principal).get(str(member_id), _UNKNOWN_MEMBER), "kind": "person"})

    def _names(self, principal: Principal | None) -> dict[str, str]:
        """Names visible to THIS principal.

        The cache is keyed by principal id: one presenter instance is reused for every Action in a view, and two
        principals may ask the same instance (a worker loop, a shared session). An unkeyed cache would hand the first
        principal's candidate list to the second and widen what they can see.
        """
        if principal is None:
            return {}
        key = str(principal.id)
        cached = self._name_cache.get(key)
        if cached is not None:
            return cached
        organization = OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))
        names = {str(member["id"]): str(member["display_name"]) for member in organization.member_candidates(principal)}
        names[key] = principal.display_name
        self._name_cache[key] = names
        return names

    def _readable_task_title(self, task_id: Any, principal: Principal | None) -> str | None:
        if principal is None or task_id in (None, ""):
            return None
        try:
            return str(TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).get(principal, UUID(str(task_id)))["title"])
        except (TaskError, ValueError):
            return None
