from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax.database import WorkflowDefinitionRecord, WorkflowDefinitionVersionRecord
from ax.workflows import catalog_definitions


def seed_catalog(session: Session) -> None:
    """Idempotently seed code-owned immutable definition versions after an explicit reset."""
    for definition in catalog_definitions():
        record = session.get(WorkflowDefinitionRecord, definition.workflow_id)
        if record is None:
            session.add(WorkflowDefinitionRecord(id=definition.workflow_id, title=definition.title))
        exists = session.scalar(
            select(WorkflowDefinitionVersionRecord).where(
                WorkflowDefinitionVersionRecord.workflow_id == definition.workflow_id,
                WorkflowDefinitionVersionRecord.version == definition.version,
            )
        )
        if exists is None:
            session.add(
                WorkflowDefinitionVersionRecord(
                    workflow_id=definition.workflow_id,
                    version=definition.version,
                    definition=definition.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
            )
    session.commit()

