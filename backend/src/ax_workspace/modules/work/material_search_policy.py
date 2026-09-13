"""Pure request policy for searching materials across authorized owner contexts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, get_args

from ax_workspace.modules.work.material_extraction import MAX_SEARCH_HITS
from ax_workspace.modules.work.material_values import MaterialError


MaterialResourceType = Literal["task", "work_request", "personal_folder", "team_folder", "meeting", "report"]
RESOURCE_TYPES = frozenset(get_args(MaterialResourceType))


@dataclass(frozen=True, slots=True)
class MaterialSearchQuery:
    query: str
    resource_types: frozenset[str]
    resource_type: str | None
    resource_id: str | None
    limit: int

    @classmethod
    def create(
        cls,
        query: str,
        *,
        limit: int,
        resource_types: list[str] | None,
        resource_type: str | None,
        resource_id: str | None,
    ) -> MaterialSearchQuery:
        cleaned = " ".join(query.split())
        if not cleaned:
            raise MaterialError("search query is required")
        kinds = frozenset(resource_types) if resource_types is not None else RESOURCE_TYPES
        if not kinds or not kinds <= RESOURCE_TYPES:
            raise MaterialError("unknown material resource type")
        if (resource_type is None) != (resource_id is None):
            raise MaterialError("resource_type and resource_id must be supplied together")
        if resource_type is not None:
            if resource_type not in kinds:
                raise MaterialError("resource anchor is outside resource_types")
            kinds = frozenset({resource_type})
        return cls(
            query=cleaned,
            resource_types=kinds,
            resource_type=resource_type,
            resource_id=resource_id,
            limit=max(1, min(limit, MAX_SEARCH_HITS)),
        )


@dataclass(frozen=True, slots=True)
class MaterialProjectionContext:
    source_kind: str
    attachment_integrity_ref: str
    extraction_status: str | None
    extraction_integrity_ref: str | None
    projection_failed: bool
    selected: bool


@dataclass(frozen=True, slots=True)
class MaterialProjection:
    needs_backfill: bool
    searchable: bool
    unavailable_reason: str


def project_material(context: MaterialProjectionContext) -> MaterialProjection:
    """Decide projection readiness without consulting its owner, repository, or index."""
    projectable = context.source_kind in {"file", "native_revision"}
    needs_backfill = projectable and context.extraction_status is None
    acceptable_statuses = {"completed", "partial"} if context.selected else {"completed"}
    searchable = (
        context.extraction_status in acceptable_statuses
        and not context.projection_failed
        and context.extraction_integrity_ref == context.attachment_integrity_ref
    )
    return MaterialProjection(
        needs_backfill=needs_backfill,
        searchable=searchable,
        unavailable_reason="extraction" if projectable else context.source_kind,
    )


def is_registered_within(registered_at: datetime | None, *, since: date | None, until: date | None) -> bool:
    """Registration filters include both boundary days and reject an unknown timestamp."""
    if since is None and until is None:
        return True
    if registered_at is None:
        return False
    day = registered_at.astimezone(UTC).date()
    if since is not None and day < since:
        return False
    return not (until is not None and day > until)
