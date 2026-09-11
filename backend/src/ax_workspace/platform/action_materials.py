"""SQL persistence for Action-bound material drafts."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import ActionItemRecord, ActionMaterialDraftRecord


class SqlAlchemyActionMaterialDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def action_for(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> ActionItemRecord | None:
        statement = select(ActionItemRecord).where(ActionItemRecord.id == action_id, ActionItemRecord.owner_id == owner_id)
        if lock:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, **fields: Any) -> ActionMaterialDraftRecord:
        row = ActionMaterialDraftRecord(**fields)
        self._session.add(row)
        self._session.flush()
        return row

    def visible_for(self, action_id: UUID, owner_id: str) -> list[ActionMaterialDraftRecord]:
        return list(self._session.scalars(
            select(ActionMaterialDraftRecord)
            .where(
                ActionMaterialDraftRecord.action_id == action_id,
                ActionMaterialDraftRecord.owner_id == owner_id,
                ActionMaterialDraftRecord.state.in_(("staged", "claimed")),
            )
            .order_by(ActionMaterialDraftRecord.created_at, ActionMaterialDraftRecord.id)
        ))

    def selected_for_claim(self, action_id: UUID, owner_id: str, ids: list[UUID]) -> list[ActionMaterialDraftRecord]:
        if not ids:
            return []
        return list(self._session.scalars(
            select(ActionMaterialDraftRecord)
            .where(
                ActionMaterialDraftRecord.action_id == action_id,
                ActionMaterialDraftRecord.owner_id == owner_id,
                ActionMaterialDraftRecord.id.in_(ids),
            )
            .with_for_update()
        ))

    def draft_for(
        self, action_id: UUID, owner_id: str, draft_id: UUID, *, lock: bool = False
    ) -> ActionMaterialDraftRecord | None:
        statement = select(ActionMaterialDraftRecord).where(
            ActionMaterialDraftRecord.id == draft_id,
            ActionMaterialDraftRecord.action_id == action_id,
            ActionMaterialDraftRecord.owner_id == owner_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def cleanup_candidates(self, now: datetime, limit: int) -> list[ActionMaterialDraftRecord]:
        return list(self._session.scalars(
            select(ActionMaterialDraftRecord)
            .where(or_(
                ActionMaterialDraftRecord.state == "discarded",
                (ActionMaterialDraftRecord.state.in_(("uploading", "staged"))) & (ActionMaterialDraftRecord.expires_at <= now),
            ))
            .order_by(ActionMaterialDraftRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ))

    def flush(self) -> None:
        self._session.flush()
