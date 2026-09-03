from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax.database import (
    MeetingEvidenceRecord,
    WorkRecord,
    WorkflowDefinitionRecord,
    WorkflowDefinitionVersionRecord,
)
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
    if session.scalar(select(WorkRecord).where(WorkRecord.owner_id == "mina")) is None:
        session.add_all(
            [
                WorkRecord(owner_id="mina", title="Catalog validation review", status="done"),
                WorkRecord(owner_id="mina", title="Demo script preparation", status="in_progress"),
            ]
        )
    if session.scalar(select(MeetingEvidenceRecord)) is None:
        session.add(
            MeetingEvidenceRecord(
                title="Workflow catalog demo planning",
                candidate_task="Prepare the demo rehearsal checklist",
            )
        )
    session.commit()
