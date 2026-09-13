"""Expiring materials prepared for an editable AX creation proposal before its domain owner exists."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from ax_workspace.modules.actions.confirmation import ATTACHABLE_ACTION_TYPES
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, ACTION_READ, Principal
from ax_workspace.modules.ax_execution.result_contracts import ActionMaterialDraftView
from ax_workspace.modules.work.application import TaskError, TaskNotFound
from ax_workspace.modules.work.material_values import MAX_MATERIAL_BYTES, normalize_material_link
from ax_workspace.modules.work.materials import MaterialStorage
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob

ACTION_MATERIAL_TTL = timedelta(hours=24)


class ActionMaterialError(TaskError):
    pass


class ActionMaterialNotFound(TaskNotFound):
    pass


class ActionMaterialDraftRepository(Protocol):
    def action_for(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> Any | None: ...
    def add(self, **fields: Any) -> Any: ...
    def visible_for(self, action_id: UUID, owner_id: str) -> list[Any]: ...
    def selected_for_claim(self, action_id: UUID, owner_id: str, ids: list[UUID]) -> list[Any]: ...
    def draft_for(self, action_id: UUID, owner_id: str, draft_id: UUID, *, lock: bool = False) -> Any | None: ...
    def cleanup_candidates(self, now: datetime, limit: int) -> list[Any]: ...
    def flush(self) -> None: ...


class CanonicalAttachmentRepository(Protocol):
    def add_file(self, **fields: Any) -> Any: ...
    def add_link(self, **fields: Any) -> Any: ...
    def bind(self, **fields: Any) -> Any: ...


class ActionMaterialDraftApplication:
    def __init__(
        self,
        repository: ActionMaterialDraftRepository,
        storage: MaterialStorage,
        attachments: CanonicalAttachmentRepository | None = None,
        extractions: Any = None,
        extraction_queue: Any = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._attachments = attachments
        self._extractions = extractions
        self._extraction_queue = extraction_queue

    def list(self, principal: Principal, action_id: UUID) -> list[dict[str, Any]]:
        # Every AX envelope carries this optional projection, including action
        # types that do not support pre-create materials. Preserve those
        # envelopes and simply project no drafts for them.
        action = self._repository.action_for(action_id, str(principal.id))
        if action is None:
            raise ActionMaterialNotFound("action was not found")
        if action.action_type not in ATTACHABLE_ACTION_TYPES:
            return []
        return [self._view(row) for row in self._repository.visible_for(action_id, str(principal.id))]

    def stage_link(self, principal: Principal, action_id: UUID, *, url: str, label: str) -> dict[str, Any]:
        self._action(principal, action_id)
        clean_url = normalize_material_link(url)
        clean_label = str(label or "").strip()[:300]
        if not clean_label:
            raise ActionMaterialError("material label is required")
        now = datetime.now(UTC)
        row = self._repository.add(
            action_id=action_id,
            owner_id=str(principal.id),
            source_kind="external_link",
            source_ref=clean_url,
            name=clean_label,
            content_type="text/uri-list",
            size_bytes=0,
            integrity_ref=f"observed:{now.isoformat()}",
            state="staged",
            expires_at=now + ACTION_MATERIAL_TTL,
            created_at=now,
        )
        return self._view(row)

    def reserve_file(
        self,
        principal: Principal,
        action_id: UUID,
        *,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        self._action(principal, action_id)
        clean_name = str(name or "").strip().replace("/", "_").replace("\\", "_")[:300]
        if not clean_name:
            raise ActionMaterialError("file name is required")
        if not data:
            raise ActionMaterialError("file content is empty")
        if len(data) > MAX_MATERIAL_BYTES:
            raise ActionMaterialError("file exceeds the 25MB limit")
        now = datetime.now(UTC)
        storage_key = f"action_items/{action_id}/drafts/{uuid4()}"
        media_type = content_type or "application/octet-stream"
        row = self._repository.add(
            action_id=action_id,
            owner_id=str(principal.id),
            source_kind="file",
            source_ref=storage_key,
            name=clean_name,
            content_type=media_type,
            size_bytes=len(data),
            integrity_ref=f"sha256:{hashlib.sha256(data).hexdigest()}",
            state="uploading",
            expires_at=now + ACTION_MATERIAL_TTL,
            created_at=now,
        )
        return {**self._view(row), "_storage_key": storage_key}

    def complete_file(self, principal: Principal, action_id: UUID, draft_id: UUID) -> dict[str, Any]:
        self._action(principal, action_id)
        row = self._repository.draft_for(action_id, str(principal.id), draft_id, lock=True)
        if row is None or row.state != "uploading":
            raise ActionMaterialNotFound("material draft was not found")
        try:
            stored = self._storage.get(row.source_ref)
        except FileNotFoundError as error:
            raise ActionMaterialError("file upload failed") from error
        if len(stored) != row.size_bytes or f"sha256:{hashlib.sha256(stored).hexdigest()}" != row.integrity_ref:
            raise ActionMaterialError("file upload integrity check failed")
        row.state = "staged"
        self._repository.flush()
        return self._view(row)

    def upload_target(self, principal: Principal, action_id: UUID, *, pending: bool = True) -> tuple[str, int]:
        if not pending and not {ACTION_READ, ACTION_DECIDE} & principal.capabilities:
            raise ActionMaterialNotFound('action was not found')
        action = self._action(principal, action_id, pending=pending, lock=pending)
        return str((action.payload or {}).get('title') or action.title), int(action.version)

    def store_reserved_file(self, principal: Principal, action_id: UUID, draft_id: UUID, data: bytes) -> ActionMaterialDraftView:
        self._action(principal, action_id)
        row = self._repository.draft_for(action_id, str(principal.id), draft_id, lock=True)
        if row is None or row.state != 'uploading':
            raise ActionMaterialNotFound('material draft was not found')
        try:
            self._storage.put(row.source_ref, data, row.content_type)
        except Exception as error:
            raise ActionMaterialError('file upload failed') from error
        return self.complete_file(principal, action_id, draft_id)

    def abort_reserved_file(self, principal: Principal, action_id: UUID, draft_id: UUID) -> None:
        row = self._repository.draft_for(action_id, str(principal.id), draft_id, lock=True)
        if row is None or row.state != 'uploading':
            return
        self.fail_file(principal, action_id, draft_id)
        try:
            self._storage.delete(row.source_ref)
        except Exception:
            pass  # The durable discarded draft remains discoverable by reconciliation.

    def fail_file(self, principal: Principal, action_id: UUID, draft_id: UUID) -> None:
        row = self._repository.draft_for(action_id, str(principal.id), draft_id, lock=True)
        if row is None or row.state != "uploading":
            return
        row.state = "discarded"
        row.discarded_at = datetime.now(UTC)
        self._repository.flush()

    def discard(self, principal: Principal, action_id: UUID, draft_id: UUID) -> dict[str, Any]:
        self._action(principal, action_id)
        row = self._repository.draft_for(action_id, str(principal.id), draft_id, lock=True)
        if row is None or row.state != "staged":
            raise ActionMaterialNotFound("material draft was not found")
        row.state = "discarded"
        row.discarded_at = datetime.now(UTC)
        self._repository.flush()
        return self._view(row)

    def claim(
        self,
        principal: Principal,
        action_id: UUID,
        draft_ids: list[UUID],
        context_type: str,
        context_id: UUID,
    ) -> list[dict[str, Any]]:
        rows = self.validate_claim(principal, action_id, draft_ids, context_type)
        if not rows:
            return []
        by_id = {row.id: row for row in rows}
        now = datetime.now(UTC)
        results: list[dict[str, Any]] = []
        for draft_id in draft_ids:
            row = by_id.get(draft_id)
            if row.source_kind == "file":
                attachment = self._attachments.add_file(
                    storage_key=row.source_ref,
                    name=row.name,
                    content_type=row.content_type,
                    size_bytes=row.size_bytes,
                    integrity_ref=row.integrity_ref,
                    provenance=f"staged upload by {principal.id} on action {action_id}",
                    uploaded_by=str(principal.id),
                )
            else:
                attachment = self._attachments.add_link(
                    url=row.source_ref,
                    name=row.name,
                    provenance=f"staged link by {principal.id} on action {action_id}",
                    uploaded_by=str(principal.id),
                )
            binding = self._attachments.bind(
                attachment_id=attachment.id,
                context_type=context_type,
                context_id=str(context_id),
                role="input",
                bound_by=str(principal.id),
            )
            if row.source_kind == "file" and self._extractions is not None:
                extraction = self._extractions.request(attachment)
                if extraction.status == "queued" and self._extraction_queue is not None:
                    self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id, str(principal.id)))
            row.state = "claimed"
            row.claimed_at = now
            if context_type == "task":
                row.claimed_task_id = context_id
            else:
                row.claimed_meeting_id = context_id
            results.append({
                "material_draft_id": str(row.id),
                "material_id": str(binding.id),
                "attachment_id": str(attachment.id),
                f"{context_type}_id": str(context_id),
                "kind": "input",
                "name": attachment.name,
                "content_type": attachment.content_type,
                "size_bytes": int(attachment.size_bytes),
                "source_kind": attachment.source_kind,
                "url": attachment.source_ref if attachment.source_kind == "external_link" else None,
                "integrity_ref": attachment.integrity_ref,
            })
        self._repository.flush()
        return results

    def validate_claim(
        self,
        principal: Principal,
        action_id: UUID,
        draft_ids: list[UUID],
        context_type: str,
    ) -> list[Any]:
        """Prove selected drafts can be claimed before any external create side effect."""
        if not draft_ids:
            return []
        if self._attachments is None:
            raise ActionMaterialError("canonical attachment repository is required")
        if context_type not in {"task", "meeting"}:
            raise ActionMaterialError("unsupported material owner")
        rows = self._repository.selected_for_claim(action_id, str(principal.id), draft_ids)
        by_id = {row.id: row for row in rows}
        now = datetime.now(UTC)
        for draft_id in draft_ids:
            row = by_id.get(draft_id)
            if row is None:
                raise ActionMaterialNotFound("material draft was not found")
            if row.state != "staged":
                raise ActionMaterialError("material draft is no longer available")
            if self._aware(row.expires_at) <= now:
                raise ActionMaterialError("material draft has expired")
            if row.source_kind == "file":
                try:
                    stored = self._storage.get(row.source_ref)
                except FileNotFoundError as error:
                    raise ActionMaterialError("staged file is incomplete") from error
                if f"sha256:{hashlib.sha256(stored).hexdigest()}" != row.integrity_ref:
                    raise ActionMaterialError("staged file integrity check failed")
        return rows

    def reconcile(self, *, now: datetime | None = None, limit: int = 100) -> int:
        cutoff = now or datetime.now(UTC)
        cleaned = 0
        for row in self._repository.cleanup_candidates(cutoff, max(1, limit)):
            if row.source_kind == "file":
                try:
                    self._storage.delete(row.source_ref)
                except Exception:
                    continue
            row.state = "purged"
            self._repository.flush()
            cleaned += 1
        return cleaned

    def target_title(self, principal: Principal, action_id: UUID) -> str:
        action = self._action(principal, action_id, lock=False)
        return str((action.payload or {}).get('title') or action.title)

    def _action(self, principal: Principal, action_id: UUID, *, pending: bool = True, lock: bool = True) -> Any:
        if pending and ACTION_DECIDE not in principal.capabilities:
            raise ActionMaterialNotFound("action was not found")
        action = self._repository.action_for(action_id, str(principal.id), lock=lock)
        if (
            action is None
            or (pending and action.state != "pending")
            or action.action_type not in ATTACHABLE_ACTION_TYPES
        ):
            raise ActionMaterialNotFound("action was not found")
        return action

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @classmethod
    def _view(cls, row: Any) -> dict[str, Any]:
        return {
            "material_draft_id": str(row.id),
            "action_item_id": str(row.action_id),
            "source_kind": row.source_kind,
            "name": row.name,
            "content_type": row.content_type,
            "size_bytes": int(row.size_bytes),
            "url": row.source_ref if row.source_kind == "external_link" else None,
            "integrity_ref": row.integrity_ref,
            "state": row.state,
            "expires_at": cls._aware(row.expires_at).isoformat(),
            "claimed_task_id": str(row.claimed_task_id) if row.claimed_task_id else None,
            "claimed_meeting_id": str(row.claimed_meeting_id) if row.claimed_meeting_id else None,
        }
