"""Independent files belong to explicit personal or team folders."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.material_folder_policy import (
    FolderCreationContext,
    FolderTitle,
    decide_folder_creation,
    ensure_folder_archivable,
    ensure_folder_detachable,
    ensure_folder_organization_active,
)
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionQueue, MaterialExtractionRepository, extraction_view
from ax_workspace.modules.work.materials import AttachmentRepository, MaterialNotFound, MaterialStorage, store_file


class MaterialFolderRepository(Protocol):
    def active_organization(self, organization_id: str) -> bool: ...
    def create(self, *, kind: str, title: str, owner_member_id: str | None, organization_id: str | None, created_by: str) -> Any: ...
    def readable(self, member_id: str, organization_ids: list[str]) -> list[Any]: ...


class FolderMemberDirectory(Protocol):
    def my_profile(self, principal: Principal) -> dict[str, Any]: ...


class MaterialFolderApplication:
    def __init__(self, folders: MaterialFolderRepository, directory: FolderMemberDirectory, attachments: AttachmentRepository,
                 storage: MaterialStorage, extractions: MaterialExtractionRepository, queue: MaterialExtractionQueue):
        self._folders, self._directory, self._attachments = folders, directory, attachments
        self._storage, self._extractions, self._queue = storage, extractions, queue

    def _membership(self, principal: Principal) -> dict[str, Any]:
        # Read the organization ledger again; a Principal cached in a previous turn is not a membership snapshot.
        try:
            return self._directory.my_profile(principal)
        except LookupError as error:
            raise MaterialNotFound("folder was not found") from error

    def create(self, principal: Principal, *, kind: str, title: str, organization_id: str | None = None) -> dict[str, Any]:
        profile = self._membership(principal)
        member_organization_ids = frozenset(unit["id"] for unit in profile["organizations"])
        decision = decide_folder_creation(
            FolderCreationContext(
                actor_id=str(principal.id),
                kind=kind,
                title=FolderTitle.create(title),
                organization_id=organization_id,
                member_organization_ids=member_organization_ids,
            )
        )
        if decision.organization_id is not None:
            ensure_folder_organization_active(
                organization_active=self._folders.active_organization(decision.organization_id),
            )
        folder = self._folders.create(
            kind=decision.kind,
            title=decision.title.value,
            owner_member_id=decision.owner_member_id,
            organization_id=decision.organization_id,
            created_by=str(principal.id),
        )
        return self._view(folder)

    def readable(self, principal: Principal) -> list[Any]:
        profile = self._membership(principal)
        return self._folders.readable(str(principal.id), [unit["id"] for unit in profile["organizations"]])

    def list_for(self, principal: Principal) -> list[dict[str, Any]]:
        return [self._view(folder) for folder in self.readable(principal)]

    def _readable(self, principal: Principal, folder_id: UUID) -> Any:
        folder = next((folder for folder in self.readable(principal) if folder.id == folder_id), None)
        if folder is None:
            raise MaterialNotFound("folder was not found")
        return folder

    def bindings(self, principal: Principal, folder_id: UUID) -> list[tuple[Any, Any]]:
        self._readable(principal, folder_id)
        return [(binding, attachment) for binding, attachment in self._attachments.bindings_for("material_folder", str(folder_id))
                if binding.unbound_at is None and attachment.lifecycle != "purged"]

    def materials(self, principal: Principal, folder_id: UUID) -> list[dict[str, Any]]:
        bindings = self.bindings(principal, folder_id)
        extractions = self._extractions.for_attachments([attachment.id for _, attachment in bindings])
        return [self._material_view(folder_id, binding, attachment, extractions.get(attachment.id)) for binding, attachment in bindings]

    def upload(self, principal: Principal, folder_id: UUID, *, name: str, content_type: str, data: bytes) -> dict[str, Any]:
        self._readable(principal, folder_id)
        attachment = store_file(self._attachments, self._storage, key_prefix=f"material_folders/{folder_id}",
                                name=name, content_type=content_type, data=data, provenance=f"material_folder:{folder_id}", uploaded_by=str(principal.id))
        binding = self._attachments.bind(attachment_id=attachment.id, context_type="material_folder", context_id=str(folder_id),
                                         role="input", bound_by=str(principal.id))
        extraction = self._extractions.request(attachment)
        if extraction.status == "queued":
            self._queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id))
        return self._material_view(folder_id, binding, attachment, extraction)

    def open(self, principal: Principal, folder_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        pair = next(((binding, attachment) for binding, attachment in self.bindings(principal, folder_id) if attachment.id == material_id), None)
        if pair is None:
            raise MaterialNotFound("material was not found")
        binding, attachment = pair
        return self._material_view(folder_id, binding, attachment, None), self._storage.get(attachment.source_ref)

    def detach(self, principal: Principal, folder_id: UUID, material_id: UUID) -> dict[str, Any]:
        pairs = [(binding, attachment) for binding, attachment in self.bindings(principal, folder_id) if attachment.id == material_id]
        if not pairs:
            raise MaterialNotFound("material was not found")
        ensure_folder_detachable(
            actor_id=str(principal.id),
            uploaded_by_member_ids=tuple(attachment.uploaded_by for _, attachment in pairs),
        )
        for binding, _ in pairs:
            self._attachments.unbind(binding)
        return {"folder_id": str(folder_id), "material_id": str(material_id), "detached": True}

    def archive(self, principal: Principal, folder_id: UUID) -> dict[str, Any]:
        folder = self._readable(principal, folder_id)
        ensure_folder_archivable(
            actor_id=str(principal.id),
            created_by_member_id=folder.created_by,
        )
        folder.archived_at = datetime.now(UTC)
        return {"folder_id": str(folder_id), "archived": True}

    @staticmethod
    def _view(folder: Any) -> dict[str, Any]:
        return {"folder_id": str(folder.id), "kind": folder.kind, "title": folder.title,
                "owner_member_id": folder.owner_member_id, "organization_id": folder.organization_id}

    @staticmethod
    def _material_view(folder_id: UUID, binding: Any, attachment: Any, extraction: Any) -> dict[str, Any]:
        return {"material_id": str(attachment.id), "binding_id": str(binding.id), "folder_id": str(folder_id),
                "name": attachment.name, "content_type": attachment.content_type, "size_bytes": attachment.size_bytes,
                "integrity_ref": attachment.integrity_ref, "extraction": extraction_view(extraction),
                "origin": f"/api/material-folders/{folder_id}/materials/{attachment.id}/content"}
