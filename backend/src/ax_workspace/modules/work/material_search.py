"""Search immutable artifacts through their currently readable owner contexts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol, get_args
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.material_extraction import MAX_SEARCH_HITS, MaterialExtractionJob, MaterialExtractionQueue, MaterialExtractionRepository, MaterialRetriever, extraction_view, projection_failure
from ax_workspace.modules.work.materials import MaterialError, MaterialNotFound, _registered_within

MaterialResourceType = Literal["task", "work_request", "personal_folder", "team_folder", "meeting", "report"]
RESOURCE_TYPES = frozenset(get_args(MaterialResourceType))
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
        cleaned = " ".join(query.split())
        if not cleaned:
            raise MaterialError("search query is required")
        kinds = set(resource_types) if resource_types is not None else set(RESOURCE_TYPES)
        if not kinds or not kinds <= RESOURCE_TYPES:
            raise MaterialError("unknown material resource type")
        if (resource_type is None) != (resource_id is None):
            raise MaterialError("resource_type and resource_id must be supplied together")
        if resource_type is not None:
            if resource_type not in kinds:
                raise MaterialError("resource anchor is outside resource_types")
            kinds = {resource_type}
        # Owner modules decide the candidate set before the extraction/index repository sees any ids.
        sources = self._owners.sources(principal, kinds, resource_type=resource_type, resource_id=resource_id, material_id=material_id)
        materials: dict[UUID, dict[str, Any]] = {}
        for source in sources:
            attachment = source.attachment
            if getattr(attachment, "lifecycle", "available") == "purged":
                continue
            if material_id is not None and attachment.id != material_id:
                continue
            if not _registered_within(attachment, registered_from, registered_until):
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
            missing = sorted((identifier for identifier, entry in materials.items()
                              if identifier not in extractions and entry["attachment"].source_kind in {"file", "native_revision"}), key=str)
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
            statuses = {"completed", "partial"} if material_id is not None else {"completed"}
            if (extraction is not None and extraction.status in statuses and projection_failure(extraction) is None
                    and extraction.integrity_ref == attachment.integrity_ref):
                searchable[extraction.id] = identifier
            else:
                unavailable.append({**view, "reason": "extraction" if attachment.source_kind in {"file", "native_revision"} else attachment.source_kind})
        hits = self._retriever.search(list(searchable), cleaned, limit=max(1, min(limit, MAX_SEARCH_HITS)))
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
