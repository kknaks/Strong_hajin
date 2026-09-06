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
#: A link is only a link when it can be opened. Anything else is a mistake or an attempt at something else.
LINK_SCHEMES = frozenset({"http", "https"})
#: The things a Task may point at through a material binding, each resolved by its own module's authorized read.
#: SCAX Tasks are deliberately not here: pointing at earlier work is a `참고 업무` reference with a real foreign key,
#: not an attachment that happens to name a task.
REFERENCE_TYPES = frozenset({"meeting"})
MAX_MATERIAL_BYTES = 25 * 1024 * 1024


class MaterialError(TaskError):
    pass


class MaterialNotFound(MaterialError):
    pass


class MaterialStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class AttachmentRepository(Protocol):
    def add_file(self, *, storage_key: str, name: str, content_type: str, size_bytes: int, integrity_ref: str, provenance: str, uploaded_by: str) -> Any: ...
    def add_link(self, *, url: str, name: str, provenance: str, uploaded_by: str) -> Any: ...
    def add_reference(self, *, resource_type: str, resource_id: str, name: str, provenance: str, uploaded_by: str) -> Any: ...
    def bind(self, *, attachment_id: UUID, context_type: str, context_id: str, role: str, bound_by: str) -> Any: ...
    def bindings_for(self, context_type: str, context_id: str) -> list[tuple[Any, Any]]: ...
    def binding(self, context_type: str, context_id: str, binding_id: UUID) -> tuple[Any, Any] | None: ...
    def unbind(self, binding: Any) -> None: ...


def _link_url(url: str) -> str:
    """A material link must be openable and must carry no secret of its own."""
    from urllib.parse import urlsplit

    cleaned = (url or "").strip()
    if not cleaned:
        raise MaterialError("material url is required")
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in LINK_SCHEMES or not parts.netloc:
        raise MaterialError("material url must be an http(s) address")
    if "@" in parts.netloc:
        # Credentials belong to the connected service, never to a material row.
        raise MaterialError("material url must not carry credentials")
    if len(cleaned) > 500:
        raise MaterialError("material url is too long")
    return cleaned


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


class ResourceReferencePort(Protocol):
    """Resolving a referenced resource, through the owning module's own authorization.

    Returns the title when this principal may read it, and None when they may not — never a stored copy of a title
    they have lost access to.
    """

    def title(self, principal: Principal, resource_type: str, resource_id: str) -> str | None: ...


class ReadableWorkPort(Protocol):
    """이 사람이 읽을 수 있는 업무들. 자료 검색은 그 업무들에 붙은 것만 본다."""

    def readable_task_ids(self, principal: Principal) -> list[str]: ...


class TaskMaterialApplication:
    def __init__(
        self,
        tasks: TaskRepository,
        attachments: AttachmentRepository,
        storage: MaterialStorage,
        extractions: MaterialExtractionRepository | None = None,
        extraction_queue: MaterialExtractionQueue | None = None,
        retriever: MaterialRetriever | None = None,
        references: ResourceReferencePort | None = None,
        readable_work: ReadableWorkPort | None = None,
    ) -> None:
        self._tasks = tasks
        # 어느 업무를 읽을 수 있는지는 업무 모듈이 답한다. 자료 검색이 스스로 다시 계산하지 않는다.
        self._readable_work = readable_work
        self._attachments = attachments
        self._storage = storage
        self._extractions = extractions
        self._extraction_queue = extraction_queue
        self._retriever = retriever
        self._references = references

    def list(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        self._require(principal, TASK_READ)
        self._tasks.task(task_id, str(principal.id))
        active = [(binding, attachment) for binding, attachment in self._attachments.bindings_for("task", str(task_id)) if binding.unbound_at is None]
        extractions = self._extractions.for_attachments([attachment.id for _, attachment in active]) if self._extractions else {}
        return [
            self._view(binding, attachment, extractions.get(attachment.id), principal=principal, references=self._references)
            for binding, attachment in active
        ]

    def search(self, principal: Principal, task_id: UUID | None, query: str, *, limit: int = 5) -> dict[str, Any]:
        """`material.search`. 어느 업무의 자료인지 알면 그 업무에서, 모르면 읽을 수 있는 업무 전부에서 찾는다.

        어느 자료에 있는지 모르는 채로 묻는 것이 자료 검색의 보통이다. 시작점을 대라고 요구하면 아는 사람만 찾을
        수 있고, 그것은 검색이 아니라 조회다.

        시작점이 넓어져도 권한은 넓어지지 않는다. 볼 수 있는 업무는 업무 모듈이 답하고, 그 업무에 지금 살아 있는
        binding만 본다 — 여기서 하는 일은 순위를 매기는 것뿐이다.
        """
        self._require(principal, TASK_READ)
        task = self._tasks.task(task_id, str(principal.id)) if task_id is not None else None
        cleaned = " ".join(query.split())
        if not cleaned:
            raise MaterialError("search query is required")
        if self._extractions is None or self._retriever is None:
            raise MaterialError("material search is not available")
        if task is not None:
            anchors = [str(task.id)]
        elif self._readable_work is not None:
            anchors = self._readable_work.readable_task_ids(principal)
        else:
            raise MaterialError("material search needs a task")
        active = [
            (binding, attachment)
            for binding, attachment in self._attachments.bindings_for_many("task", anchors)
            if binding.unbound_at is None
        ]
        extractions = self._extractions.for_attachments([attachment.id for _, attachment in active])
        searchable: dict[UUID, tuple[Any, Any, Any]] = {}
        unavailable: list[dict[str, Any]] = []
        for binding, attachment in active:
            extraction = extractions.get(attachment.id)
            if extraction is not None and extraction.status == "completed" and extraction.integrity_ref == attachment.integrity_ref:
                searchable[extraction.id] = (binding, attachment, extraction)
            else:
                unavailable.append(
                    {
                        "material_id": str(binding.id),
                        "name": attachment.name,
                        "kind": binding.role,
                        # Why it could not be read: a link has no content to extract, a file may still be pending.
                        "reason": attachment.source_kind if attachment.source_kind != "file" else "extraction",
                        "extraction": extraction_view(extraction),
                    }
                )
        hits = self._retriever.search(list(searchable), cleaned, limit=max(1, min(limit, MAX_SEARCH_HITS)))
        results = []
        for hit in hits:
            binding, attachment, extraction = searchable[hit.extraction_id]
            results.append(
                {
                    "material_id": str(binding.id),
                    "attachment_id": str(attachment.id),
                    "chunk_id": str(hit.chunk_id),
                    "task_id": binding.context_id,
                    "kind": binding.role,
                    "name": attachment.name,
                    "content_type": attachment.content_type,
                    "integrity_ref": attachment.integrity_ref,
                    "extraction_id": str(extraction.id),
                    "sequence": hit.sequence,
                    "page": hit.page,
                    "excerpt": hit.excerpt,
                    "matched_tokens": hit.matched_tokens,
                    "origin": f"/api/tasks/{binding.context_id}/materials/{binding.id}/content",
                }
            )
        return {
            # 시작점을 대지 않았으면 답에도 시작점이 없다. 결과의 각 줄이 자기 업무를 말한다.
            "task_id": str(task.id) if task is not None else None,
            "task_title": task.title if task is not None else None,
            "query": cleaned,
            "results": results,
            "searched_materials": len(searchable),
            "unavailable_materials": unavailable,
        }

    def attach_link(self, principal: Principal, task_id: UUID, *, kind: str, url: str, label: str) -> dict[str, Any]:
        """Point a Task at work that lives somewhere else.

        Nothing is fetched and no revision is pinned, so this is a changeable link and every surface says so. A
        different URL is a different material, never a rewrite of the one someone already looked at.
        """
        self._require(principal, TASK_SELF_MANAGE)
        task = self._tasks.task(task_id, str(principal.id), lock=True)
        if kind not in MATERIAL_KINDS:
            raise MaterialError("material kind must be input or output")
        clean_url = _link_url(url)
        clean_label = label.strip()[:300]
        if not clean_label:
            raise MaterialError("material label is required")
        attachment = self._attachments.add_link(
            url=clean_url, name=clean_label,
            provenance=f"link by {principal.id} on task {task.id}", uploaded_by=str(principal.id),
        )
        binding = self._attachments.bind(
            attachment_id=attachment.id, context_type="task", context_id=str(task.id), role=kind, bound_by=str(principal.id)
        )
        self._moved(task)
        self._tasks.record_activity(
            task, str(principal.id), "task.material_attached",
            f"{'참고 자료' if kind == 'input' else '산출물'} 링크 연결: {clean_label}",
        )
        # A link has no content to extract, so no extraction is requested and search reports it as unreadable.
        return self._moved_view(task, binding, attachment, None, principal=principal, references=self._references)

    def attach_reference(self, principal: Principal, task_id: UUID, *, kind: str, resource_type: str, resource_id: str) -> dict[str, Any]:
        """Point a Task at another thing inside SCAX, but only at something this person may already read."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self._tasks.task(task_id, str(principal.id), lock=True)
        if kind not in MATERIAL_KINDS:
            raise MaterialError("material kind must be input or output")
        if resource_type == "task":
            raise MaterialError("업무는 자료가 아니라 참고 업무로 연결하세요")
        if resource_type not in REFERENCE_TYPES:
            raise MaterialError(f"material reference type must be one of {sorted(REFERENCE_TYPES)}")
        if self._references is None:
            raise MaterialError("material references are not available")
        title = self._references.title(principal, resource_type, str(resource_id))
        if title is None:
            # Refusing the same way for "not readable" and "not there" leaves nothing to probe for.
            raise MaterialNotFound("referenced resource was not found")
        attachment = self._attachments.add_reference(
            resource_type=resource_type, resource_id=str(resource_id), name=title,
            provenance=f"reference by {principal.id} on task {task.id}", uploaded_by=str(principal.id),
        )
        binding = self._attachments.bind(
            attachment_id=attachment.id, context_type="task", context_id=str(task.id), role=kind, bound_by=str(principal.id)
        )
        self._moved(task)
        self._tasks.record_activity(
            task, str(principal.id), "task.material_attached",
            f"{'참고 자료' if kind == 'input' else '산출물'} 연결: {title}",
        )
        return self._moved_view(task, binding, attachment, None, principal=principal, references=self._references)

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
        self._moved(task)
        self._tasks.record_activity(task, str(principal.id), "task.material_attached", f"{'참고 자료' if kind == 'input' else '산출물'} 등록: {clean_name}")
        extraction = None
        if self._extractions is not None:
            # Same transaction as the attachment/binding: the job exists exactly when the material does.
            extraction = self._extractions.request(attachment)
            if extraction.status == "queued" and self._extraction_queue is not None:
                self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id))
        return self._moved_view(task, binding, attachment, extraction, principal=principal, references=self._references)

    def open(self, principal: Principal, task_id: UUID, binding_id: UUID) -> tuple[dict[str, Any], bytes]:
        self._require(principal, TASK_READ)
        self._tasks.task(task_id, str(principal.id))
        found = self._attachments.binding("task", str(task_id), binding_id)
        if found is None or found[0].unbound_at is not None:
            raise MaterialNotFound("material was not found")
        binding, attachment = found
        if attachment.source_kind != "file":
            raise MaterialError("only file attachments have downloadable content")
        if getattr(attachment, "lifecycle", "available") == "purged":
            raise MaterialNotFound("이 자료는 완전히 삭제되어 더 이상 내려받을 수 없습니다")
        try:
            data = self._storage.get(attachment.source_ref)
        except FileNotFoundError as error:
            # The row says a file is there and the store disagrees: say so plainly rather than failing as a bug.
            raise MaterialNotFound("자료 원본을 찾을 수 없습니다") from error
        return self._view(binding, attachment, self._extraction_for(attachment), principal=principal, references=self._references), data

    def detach(self, principal: Principal, task_id: UUID, binding_id: UUID) -> dict[str, Any]:
        """Unbinding keeps the Attachment and bytes; the binding records when it left the Task."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self._tasks.task(task_id, str(principal.id), lock=True)
        found = self._attachments.binding("task", str(task_id), binding_id)
        if found is None or found[0].unbound_at is not None:
            raise MaterialNotFound("material was not found")
        binding, attachment = found
        self._attachments.unbind(binding)
        self._moved(task)
        self._tasks.record_activity(task, str(principal.id), "task.material_detached", f"자료 해제: {attachment.name}")
        return self._moved_view(task, binding, attachment, self._extraction_for(attachment), principal=principal, references=self._references)

    def _moved_view(self, task: Any, binding: Any, attachment: Any, extraction: Any, *, principal: Any, references: Any) -> dict[str, Any]:
        """A mutation answers with the material and the Task version it moved to, so an open screen is not left stale."""
        view = self._view(binding, attachment, extraction, principal=principal, references=references)
        return {**view, "task_version": int(task.version)}

    def _moved(self, task: Any) -> None:
        """Attaching or detaching changes what the Task contains, so the Task moves on and history freezes it."""
        task.version += 1
        self._tasks.touch(task)

    def _extraction_for(self, attachment: Any) -> Any | None:
        if self._extractions is None:
            return None
        return self._extractions.for_attachments([attachment.id]).get(attachment.id)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(
        binding: Any,
        attachment: Any,
        extraction: Any | None = None,
        *,
        principal: Principal | None = None,
        references: ResourceReferencePort | None = None,
    ) -> dict[str, Any]:
        resource = None
        name = attachment.name
        if attachment.source_kind == "resource_ref":
            resource_type, _, resource_id = str(attachment.source_ref).partition(":")
            title = references.title(principal, resource_type, resource_id) if references and principal else None
            # The stored name is a fallback; a reader who may not open it is never handed the title.
            resource = {"type": resource_type, "id": resource_id, "title": title} if title else None
            name = title or "볼 수 없는 자료"
        return {
            "extraction": extraction_view(extraction),
            "material_id": str(binding.id),
            "attachment_id": str(attachment.id),
            "task_id": binding.context_id,
            "kind": binding.role,
            "name": name,
            "resource": resource,
            "content_type": attachment.content_type,
            "size_bytes": int(attachment.size_bytes),
            "source_kind": attachment.source_kind,
            "url": attachment.source_ref if attachment.source_kind == "external_link" else None,
            # SCAX did not read it and pinned no revision, so it must not be mistaken for a frozen artifact.
            "mutable_source": attachment.source_kind != "file",
            "integrity_ref": attachment.integrity_ref,
            # The file itself was destroyed on request; the material stays as a fact of the Task, unreadable.
            "purged": getattr(attachment, "lifecycle", "available") == "purged",
            "uploaded_by": attachment.uploaded_by,
            "created_at": binding.bound_at.isoformat(),
            "removed_at": binding.unbound_at.isoformat() if binding.unbound_at else None,
        }
