"""Search immutable artifacts through their currently readable owner contexts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionQueue, MaterialExtractionRepository, MaterialRetriever, extraction_view, projection_failure
from ax_workspace.modules.work.material_search_policy import (
    RESOURCE_TYPES as RESOURCE_TYPES,
    MaterialProjectionContext,
    MaterialSearchQuery,
    MaterialResourceType as MaterialResourceType,
    is_registered_within,
    project_material,
)
from ax_workspace.modules.work.materials import MaterialNotFound

MAX_UNAVAILABLE = 20
MAX_BACKFILL = 20


@dataclass(frozen=True)
class ReadableMaterialSource:
    attachment: Any
    context: dict[str, Any]


class MaterialOwnerPort(Protocol):
    def sources(self, principal: Principal, resource_types: set[str], *, resource_type: str | None, resource_id: str | None,
                material_id: UUID | None = None) -> list[ReadableMaterialSource]: ...


class MaterialSearchApplication:
    def __init__(self, owners: MaterialOwnerPort, extractions: MaterialExtractionRepository, retriever: MaterialRetriever,
                 extraction_queue: MaterialExtractionQueue | None = None) -> None:
        self._owners, self._extractions, self._retriever = owners, extractions, retriever
        self._extraction_queue = extraction_queue

    def search(self, principal: Principal, query: str, *, limit: int = 5, resource_types: list[str] | None = None,
               resource_type: str | None = None, resource_id: str | None = None, material_id: UUID | None = None,
               registered_from: date | None = None, registered_until: date | None = None) -> dict[str, Any]:
        search_query = MaterialSearchQuery.create(
            query,
            limit=limit,
            resource_types=resource_types,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        cleaned = search_query.query
        kinds = set(search_query.resource_types)
        resource_type = search_query.resource_type
        resource_id = search_query.resource_id
        # Owner modules decide the candidate set before the extraction/index repository sees any ids.
        sources = self._owners.sources(principal, kinds, resource_type=resource_type, resource_id=resource_id, material_id=material_id)
        materials: dict[UUID, dict[str, Any]] = {}
        for source in sources:
            attachment = source.attachment
            if getattr(attachment, "lifecycle", "available") == "purged":
                continue
            if material_id is not None and attachment.id != material_id:
                continue
            if not is_registered_within(
                getattr(attachment, "created_at", None),
                since=registered_from,
                until=registered_until,
            ):
                continue
            entry = materials.setdefault(attachment.id, {"attachment": attachment, "contexts": []})
            if source.context not in entry["contexts"]:
                entry["contexts"].append(source.context)
        if material_id is not None and not materials:
            raise MaterialNotFound("material was not found")
        for entry in materials.values():
            entry["contexts"].sort(key=lambda context: (context["resource_type"], context["resource_id"], context["binding_id"]))
        extractions = self._extractions.for_attachments(list(materials))
        # Older owner upload paths did not request projections. Discover only authorized files, in a bounded batch.
        if self._extraction_queue is not None:
            missing = sorted(
                (
                    identifier
                    for identifier, entry in materials.items()
                    if project_material(
                        _projection_context(
                            entry["attachment"],
                            extractions.get(identifier),
                            selected=material_id is not None,
                        )
                    ).needs_backfill
                ),
                key=str,
            )
            for identifier in missing[:MAX_BACKFILL]:
                extraction = self._extractions.request(materials[identifier]["attachment"])
                extractions[identifier] = extraction
                if extraction.status == "queued":
                    self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, identifier))
        searchable = {}
        unavailable = []
        metadata = {}
        for identifier, entry in materials.items():
            attachment, contexts = entry["attachment"], entry["contexts"]
            extraction = extractions.get(identifier)
            view = {"material_id": str(identifier), "name": attachment.name, "content_type": attachment.content_type,
                    "integrity_ref": attachment.integrity_ref, "source_contexts": contexts,
                    "extraction": extraction_view(extraction)}
            metadata[identifier] = view
            projection = project_material(
                _projection_context(attachment, extraction, selected=material_id is not None)
            )
            if projection.searchable and extraction is not None:
                searchable[extraction.id] = identifier
            else:
                unavailable.append({**view, "reason": projection.unavailable_reason})
        hits = self._retriever.search(list(searchable), cleaned, limit=search_query.limit)
        locators = self._extractions.chunk_contexts([hit.chunk_id for hit in hits])
        results = []
        for hit in hits:
            view = metadata[searchable[hit.extraction_id]]
            primary = view["source_contexts"][0]
            results.append({**view, "chunk_id": str(hit.chunk_id), "extraction_id": str(hit.extraction_id),
                            "sequence": hit.sequence, "page": hit.page, "excerpt": hit.excerpt,
                            "matched_tokens": hit.matched_tokens, **locators.get(hit.chunk_id, {}),
                            "source_resource_type": primary["resource_type"], "source_resource_id": primary["resource_id"],
                            "source_resource_title": primary["title"], "origin": primary["origin"]})
        unavailable.sort(key=lambda item: item["material_id"])
        return {"query": cleaned, "resource_types": sorted(kinds), "resource_type": resource_type, "resource_id": resource_id,
                "registered_from": registered_from.isoformat() if registered_from else None,
                "registered_until": registered_until.isoformat() if registered_until else None,
                "results": results, "searched_materials": len(searchable),
                "unavailable_materials": unavailable[:MAX_UNAVAILABLE], "unavailable_materials_count": len(unavailable),
                "unavailable_truncated": len(unavailable) > MAX_UNAVAILABLE,
                **({"selected_material": metadata[material_id]} if material_id is not None else {})}


def _projection_context(
    attachment: Any,
    extraction: Any | None,
    *,
    selected: bool,
) -> MaterialProjectionContext:
    return MaterialProjectionContext(
        source_kind=attachment.source_kind,
        attachment_integrity_ref=attachment.integrity_ref,
        extraction_status=None if extraction is None else extraction.status,
        extraction_integrity_ref=None if extraction is None else extraction.integrity_ref,
        projection_failed=extraction is not None and projection_failure(extraction) is not None,
        selected=selected,
    )
