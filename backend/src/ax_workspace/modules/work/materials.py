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
from ax_workspace.modules.work.material_extraction import (
    MAX_SEARCH_HITS,
    MaterialExtractionJob,
    MaterialExtractionQueue,
    MaterialExtractionRepository,
    MaterialRetriever,
    extraction_view,
)

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
    def __init__(
        self,
        tasks: TaskRepository,
        attachments: AttachmentRepository,
        storage: MaterialStorage,
        extractions: MaterialExtractionRepository | None = None,
        extraction_queue: MaterialExtractionQueue | None = None,
        retriever: MaterialRetriever | None = None,
    ) -> None:
        self._tasks = tasks
        self._attachments = attachments
        self._storage = storage
        self._extractions = extractions
        self._extraction_queue = extraction_queue
        self._retriever = retriever

    def list(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        self._require(principal, TASK_READ)
        self._tasks.task(task_id, str(principal.id))
        active = [(binding, attachment) for binding, attachment in self._attachments.bindings_for("task", str(task_id)) if binding.unbound_at is None]
        extractions = self._extractions.for_attachments([attachment.id for _, attachment in active]) if self._extractions else {}
        return [self._view(binding, attachment, extractions.get(attachment.id)) for binding, attachment in active]

    def search(self, principal: Principal, task_id: UUID, query: str, *, limit: int = 5) -> dict[str, Any]:
        """SPEC-006 `material.search` scoped to one Task: authorization (active assignment + live binding) is re-checked here,
        before ranking, and only bounded excerpts with the source identity leave."""
        self._require(principal, TASK_READ)
        task = self._tasks.task(task_id, str(principal.id))
        cleaned = " ".join(query.split())
        if not cleaned:
            raise MaterialError("search query is required")
        if self._extractions is None or self._retriever is None:
            raise MaterialError("material search is not available")
        active = [(binding, attachment) for binding, attachment in self._attachments.bindings_for("task", str(task_id)) if binding.unbound_at is None]
        extractions = self._extractions.for_attachments([attachment.id for _, attachment in active])
        searchable: dict[UUID, tuple[Any, Any, Any]] = {}
        unavailable: list[dict[str, Any]] = []
        for binding, attachment in active:
            extraction = extractions.get(attachment.id)
            if extraction is not None and extraction.status == "completed" and extraction.integrity_ref == attachment.integrity_ref:
                searchable[extraction.id] = (binding, attachment, extraction)
            else:
                unavailable.append({"material_id": str(binding.id), "name": attachment.name, "kind": binding.role, "extraction": extraction_view(extraction)})
        hits = self._retriever.search(list(searchable), cleaned, limit=max(1, min(limit, MAX_SEARCH_HITS)))
        results = []
        for hit in hits:
            binding, attachment, extraction = searchable[hit.extraction_id]
            results.append(
                {
                    "material_id": str(binding.id),
                    "attachment_id": str(attachment.id),
                    "chunk_id": str(hit.chunk_id),
                    "task_id": str(task.id),
                    "kind": binding.role,
                    "name": attachment.name,
                    "content_type": attachment.content_type,
                    "integrity_ref": attachment.integrity_ref,
                    "extraction_id": str(extraction.id),
                    "sequence": hit.sequence,
                    "page": hit.page,
                    "excerpt": hit.excerpt,
                    "matched_tokens": hit.matched_tokens,
                    "origin": f"/api/tasks/{task.id}/materials/{binding.id}/content",
                }
            )
        return {
            "task_id": str(task.id),
            "task_title": task.title,
            "query": cleaned,
            "results": results,
            "searched_materials": len(searchable),
            "unavailable_materials": unavailable,
        }

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
        extraction = None
        if self._extractions is not None:
            # Same transaction as the attachment/binding: the job exists exactly when the material does.
            extraction = self._extractions.request(attachment)
            if extraction.status == "queued" and self._extraction_queue is not None:
                self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id))
        return self._view(binding, attachment, extraction)

    def open(self, principal: Principal, task_id: UUID, binding_id: UUID) -> tuple[dict[str, Any], bytes]:
        self._require(principal, TASK_READ)
        self._tasks.task(task_id, str(principal.id))
        found = self._attachments.binding("task", str(task_id), binding_id)
        if found is None or found[0].unbound_at is not None:
            raise MaterialNotFound("material was not found")
        binding, attachment = found
        if attachment.source_kind != "file":
            raise MaterialError("only file attachments have downloadable content")
        return self._view(binding, attachment, self._extraction_for(attachment)), self._storage.get(attachment.source_ref)

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
        return self._view(binding, attachment, self._extraction_for(attachment))

    def _extraction_for(self, attachment: Any) -> Any | None:
        if self._extractions is None:
            return None
        return self._extractions.for_attachments([attachment.id]).get(attachment.id)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(binding: Any, attachment: Any, extraction: Any | None = None) -> dict[str, Any]:
        return {
            "extraction": extraction_view(extraction),
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
