"""Task material metadata and mutation receipts, without stored bytes or storage keys."""
from typing_extensions import TypedDict
from ax_workspace.modules.work.extraction_results import ExtractionView


class MaterialResourceView(TypedDict):
    type: str
    id: str
    title: str


class TaskMaterialView(TypedDict):
    extraction: ExtractionView | None
    material_id: str
    binding_id: str
    attachment_id: str
    task_id: str
    kind: str
    name: str
    resource: MaterialResourceView | None
    content_type: str
    size_bytes: int
    source_kind: str
    url: str | None
    mutable_source: bool
    integrity_ref: str
    purged: bool
    uploaded_by: str
    created_at: str
    removed_at: str | None


class TaskMaterialResult(TaskMaterialView):
    task_version: int
