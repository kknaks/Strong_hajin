"""Task materials = ERD Attachment + AttachmentBinding(context=task, role=input|output).

Bytes live behind the MaterialStorage port (local directory now, Azure Blob container later); the Work
ledger keeps the artifact identity, integrity hash, provenance, and where it is bound.
"""
from __future__ import annotations

import hashlib
from typing import Any, Protocol
from uuid import UUID, uuid4

from ax_workspace.modules.organization_access.domain import Principal, TASK_READ, TASK_SELF_MANAGE
from ax_workspace.modules.work.application import TaskAccessDenied, TaskError, TaskRepository

MATERIAL_KINDS = frozenset({"input", "output"})
MAX_MATERIAL_BYTES = 25 * 1024 * 1024


class MaterialError(TaskError):
    pass


class MaterialNotFound(MaterialError):
    pass


class MaterialStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...


class AttachmentRepository(Protocol):
    def add_file(self, *, storage_key: str, name: str, content_type: str, size_bytes: int, integrity_ref: str, provenance: str, uploaded_by: str) -> Any: ...
    def bind(self, *, attachment_id: UUID, context_type: str, context_id: str, role: str, bound_by: str) -> Any: ...
    def bindings_for(self, context_type: str, context_id: str) -> list[tuple[Any, Any]]: ...
    def binding(self, context_type: str, context_id: str, binding_id: UUID) -> tuple[Any, Any] | None: ...
    def unbind(self, binding: Any) -> None: ...


def store_file(
    attachments: AttachmentRepository,
    storage: MaterialStorage,
    *,
    key_prefix: str,
    name: str,
    content_type: str,
    data: bytes,
    provenance: str,
    uploaded_by: str,
) -> Any:
    """Persist bytes behind the storage port and record the ERD Attachment with its integrity hash."""
    clean_name = name.strip().replace("/", "_").replace("\\", "_")[:300]
    if not clean_name:
        raise MaterialError("file name is required")
    if not data:
        raise MaterialError("file content is empty")
    if len(data) > MAX_MATERIAL_BYTES:
        raise MaterialError("file exceeds the 25MB limit")
    media_type = content_type or "application/octet-stream"
    storage_key = f"{key_prefix}/{uuid4()}"
    storage.put(storage_key, data, media_type)
    return attachments.add_file(
        storage_key=storage_key,
        name=clean_name,
        content_type=media_type,
        size_bytes=len(data),
        integrity_ref=f"sha256:{hashlib.sha256(data).hexdigest()}",
        provenance=provenance,
        uploaded_by=uploaded_by,
    )


class TaskMaterialApplication:
    def __init__(self, tasks: TaskRepository, attachments: AttachmentRepository, storage: MaterialStorage) -> None:
        self._tasks = tasks
        self._attachments = attachments
        self._storage = storage

    def list(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        self._require(principal, TASK_READ)
        self._tasks.task(task_id, str(principal.id))
        return [self._view(binding, attachment) for binding, attachment in self._attachments.bindings_for("task", str(task_id)) if binding.unbound_at is None]

    def attach(self, principal: Principal, task_id: UUID, *, kind: str, name: str, content_type: str, data: bytes) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        task = self._tasks.task(task_id, str(principal.id), lock=True)
        if kind not in MATERIAL_KINDS:
            raise MaterialError("material kind must be input or output")
        clean_name = name.strip().replace("/", "_").replace("\\", "_")[:300]
        if not clean_name:
            raise MaterialError("material name is required")
        if not data:
            raise MaterialError("material content is empty")
        if len(data) > MAX_MATERIAL_BYTES:
            raise MaterialError("material exceeds the 25MB limit")
        attachment = store_file(
            self._attachments, self._storage,
            key_prefix=f"tasks/{task.id}", name=clean_name, content_type=content_type, data=data,
            provenance=f"upload by {principal.id} to task {task.id}", uploaded_by=str(principal.id),
        )
        binding = self._attachments.bind(attachment_id=attachment.id, context_type="task", context_id=str(task.id), role=kind, bound_by=str(principal.id))
        self._tasks.record_activity(task, str(principal.id), "task.material_attached", f"{'참고 자료' if kind == 'input' else '산출물'} 등록: {clean_name}")
        return self._view(binding, attachment)

    def open(self, principal: Principal, task_id: UUID, binding_id: UUID) -> tuple[dict[str, Any], bytes]:
        self._require(principal, TASK_READ)
        self._tasks.task(task_id, str(principal.id))
        found = self._attachments.binding("task", str(task_id), binding_id)
        if found is None or found[0].unbound_at is not None:
            raise MaterialNotFound("material was not found")
        binding, attachment = found
        if attachment.source_kind != "file":
            raise MaterialError("only file attachments have downloadable content")
        return self._view(binding, attachment), self._storage.get(attachment.source_ref)

    def detach(self, principal: Principal, task_id: UUID, binding_id: UUID) -> dict[str, Any]:
        """Unbinding keeps the Attachment and bytes; the binding records when it left the Task."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self._tasks.task(task_id, str(principal.id), lock=True)
        found = self._attachments.binding("task", str(task_id), binding_id)
        if found is None or found[0].unbound_at is not None:
            raise MaterialNotFound("material was not found")
        binding, attachment = found
        self._attachments.unbind(binding)
        self._tasks.record_activity(task, str(principal.id), "task.material_detached", f"자료 해제: {attachment.name}")
        return self._view(binding, attachment)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(binding: Any, attachment: Any) -> dict[str, Any]:
        return {
            "material_id": str(binding.id),
            "attachment_id": str(attachment.id),
            "task_id": binding.context_id,
            "kind": binding.role,
            "name": attachment.name,
            "content_type": attachment.content_type,
            "size_bytes": int(attachment.size_bytes),
            "source_kind": attachment.source_kind,
            "integrity_ref": attachment.integrity_ref,
            "uploaded_by": attachment.uploaded_by,
            "created_at": binding.bound_at.isoformat(),
            "removed_at": binding.unbound_at.isoformat() if binding.unbound_at else None,
        }
