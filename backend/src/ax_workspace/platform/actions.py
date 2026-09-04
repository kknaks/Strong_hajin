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
        payload_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
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
        if action.action_type == "task.assign":
            return f"업무 배정: {action.payload['title']} → {action.payload['assignee_id']}"
        return action.action_type


class SqlAlchemyActionExecutor:
    """Invokes existing public application commands inside the Action transaction."""

    def __init__(self, session: Session, report_provider: Any) -> None:
        self._session = session
        self._report_provider = report_provider

    def execute(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
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
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), self).create_self(
                principal, str(action.payload["title"]), causation_key=str(action.id), source_action_item_id=action.id
            )
        if action.action_type == "task.update":
            changes = dict(action.payload.get("changes", {}))
            for field in ("start_date", "due_date"):
                if field in changes:
                    changes[field] = _parse_date(changes[field])
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), self).update(
                UUID(str(action.payload["task_id"])), principal, int(action.payload["expected_version"]), changes
            )
        if action.action_type == "task.assign":
            return self._assignments().assign(
                principal, str(action.payload["title"]), str(action.payload["assignee_id"]),
                description=action.payload.get("description"),
                start_date=_parse_date(action.payload.get("start_date")),
                due_date=_parse_date(action.payload.get("due_date")),
                causation_key=str(action.id),
            )
        if action.action_type == "task.assignment.accept":
            return self._assignments().accept(principal, UUID(str(action.payload["assignment_id"])))
        if action.action_type == "task.assignment.decline":
            return self._assignments().decline(principal, UUID(str(action.payload["assignment_id"])), str(action.payload.get("reason") or ""))
        if action.action_type == "task.transition":
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), self).transition(
                UUID(str(action.payload["task_id"])), principal, TaskState(str(action.payload["target"])),
                action.payload.get("reason"), int(action.payload["expected_version"])
            )
        if action.action_type == "work_request.accept":
            return WorkRequestApplication(SqlAlchemyWorkRequestRepository(self._session), OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))).accept(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]))
        if action.action_type == "work_request.reject":
            return WorkRequestApplication(SqlAlchemyWorkRequestRepository(self._session), OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))).reject(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]), str(action.payload["reason"]))
        if action.action_type == "work_request.negotiate":
            return WorkRequestApplication(SqlAlchemyWorkRequestRepository(self._session), OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))).negotiate(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]), dict(action.payload["conditions"]))
        raise ValueError("unsupported action type")

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
    "daily_report.edit": "일일보고 수정",
    "daily_report.submit": "일일보고 제출",
}
_TASK_STATE_LABELS: dict[str, str] = {"open": "대기", "in_progress": "진행 중", "blocked": "막힘", "done": "완료", "cancelled": "취소"}
_UNKNOWN_MEMBER = "확인할 수 없는 구성원"


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

        if kind in {"task.create_self", "work_request.create", "task.assign"}:
            subject = str(payload.get("title") or action.title)
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
        elif kind in {"task.assignment.decline", "work_request.reject", "daily_report.submit"}:
            self._text(fields, "reason", "사유", payload.get("reason"))
        elif kind == "work_request.negotiate":
            conditions = dict(payload.get("conditions") or {})
            self._date(fields, "due_date", "제안 기한", conditions.get("due_date"))
            self._text(fields, "note", "메모", conditions.get("note") or conditions.get("reason"))

        self._evidence(fields, action, principal)
        return {"subject": subject, "operation_label": _OPERATION_LABELS.get(kind, kind), "preview": fields}

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
                TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), self).get(principal, task_id)
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
            return str(TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), self).get(principal, UUID(str(task_id)))["title"])
        except (TaskError, ValueError):
            return None
