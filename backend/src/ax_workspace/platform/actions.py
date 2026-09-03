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
from ax_workspace.modules.work.application import TaskApplication, TaskState
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import (
    ActionItemAuditEventRecord,
    ActionItemRecord,
    ConversationRecord,
    ConversationTurnRecord,
)
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.platform.work_tasks import (
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRecordSource,
    SqlAlchemyWorkRequestRepository,
)


class SqlAlchemyActionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        return self._session.scalar(statement.with_for_update() if lock else statement)

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

    def view(self, action: ActionItemRecord) -> dict[str, Any]:
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
            return TaskApplication(SqlAlchemyTaskRepository(self._session)).create_self(
                principal, str(action.payload["title"]), causation_key=str(action.id)
            )
        if action.action_type == "task.transition":
            return TaskApplication(SqlAlchemyTaskRepository(self._session)).transition(
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
