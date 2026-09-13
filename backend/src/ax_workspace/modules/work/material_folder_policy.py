"""Pure ownership and command policy for independent material folders."""

from __future__ import annotations

from dataclasses import dataclass

from ax_workspace.modules.work.material_values import MaterialError, MaterialNotFound


@dataclass(frozen=True, slots=True)
class FolderTitle:
    value: str

    @classmethod
    def create(cls, value: str) -> FolderTitle:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 300:
            raise MaterialError("folder kind and a title of 1 to 300 characters are required")
        return cls(cleaned)


@dataclass(frozen=True, slots=True)
class FolderCreationContext:
    actor_id: str
    kind: str
    title: FolderTitle
    organization_id: str | None
    member_organization_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class FolderCreationDecision:
    kind: str
    title: FolderTitle
    owner_member_id: str | None
    organization_id: str | None


def decide_folder_creation(context: FolderCreationContext) -> FolderCreationDecision:
    if context.kind not in {"personal", "team"}:
        raise MaterialError("folder kind and a title of 1 to 300 characters are required")
    if context.kind == "personal":
        if context.organization_id is not None:
            raise MaterialError("a personal folder cannot have a team owner")
        return FolderCreationDecision(context.kind, context.title, context.actor_id, None)
    if context.organization_id not in context.member_organization_ids:
        raise MaterialNotFound("organization was not found")
    return FolderCreationDecision(context.kind, context.title, None, context.organization_id)


def ensure_folder_organization_active(*, organization_active: bool) -> None:
    if not organization_active:
        raise MaterialNotFound("organization was not found")


def ensure_folder_detachable(*, actor_id: str, uploaded_by_member_ids: tuple[str, ...]) -> None:
    if any(uploaded_by != actor_id for uploaded_by in uploaded_by_member_ids):
        raise MaterialError("only the uploader may detach this material")


def ensure_folder_archivable(*, actor_id: str, created_by_member_id: str) -> None:
    if created_by_member_id != actor_id:
        raise MaterialError("only the folder creator may archive it")
