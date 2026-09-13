"""Current-authority material discovery, extraction locations, and owner context."""
from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict

from ax_workspace.modules.work.extraction_results import ExtractionView


class RecordingMaterialView(TypedDict):
    material_id: str
    integrity_ref: str
    origin: str


class MaterialSourceContext(TypedDict):
    resource_type: str
    resource_id: str
    title: str
    binding_id: str
    role: str
    origin: str
    binding_context_type: NotRequired[str]
    binding_context_id: NotRequired[str]
    source_layer: NotRequired[str]
    source_revision_id: NotRequired[str]
    source_revision: NotRequired[int]
    is_current_revision: NotRequired[bool]
    note_id: NotRequired[str]
    note_lifecycle: NotRequired[str]
    recording_id: NotRequired[str]
    recording_integrity_ref: NotRequired[str]
    recording_state: NotRequired[str]
    recording_material: NotRequired[RecordingMaterialView]


class FolderMaterialView(TypedDict):
    material_id: str
    binding_id: str
    folder_id: str
    name: str
    content_type: str
    size_bytes: int
    integrity_ref: str
    extraction: ExtractionView | None
    origin: str


class MaterialSearchMetadata(TypedDict):
    material_id: str
    name: str
    content_type: str
    integrity_ref: str
    source_contexts: list[MaterialSourceContext]
    extraction: ExtractionView | None


class MaterialMetadataResult(MaterialSearchMetadata):
    size_bytes: int
    state: str
    origin: str


class UnavailableMaterialView(MaterialSearchMetadata):
    reason: str


class MaterialSearchHit(MaterialSearchMetadata):
    chunk_id: str
    extraction_id: str
    sequence: int
    page: int | None
    excerpt: str
    matched_tokens: int
    source_resource_type: str
    source_resource_id: str
    source_resource_title: str
    origin: str
    source_locator: NotRequired[dict[str, JsonValue]]
    header_context: NotRequired[dict[str, JsonValue] | None]
    locator_label: NotRequired[str | None]


class MaterialSearchResult(TypedDict):
    query: str
    resource_types: list[str]
    resource_type: str | None
    resource_id: str | None
    registered_from: str | None
    registered_until: str | None
    results: list[MaterialSearchHit]
    searched_materials: int
    unavailable_materials: list[UnavailableMaterialView]
    unavailable_materials_count: int
    unavailable_truncated: bool
    selected_material: NotRequired[MaterialSearchMetadata]
