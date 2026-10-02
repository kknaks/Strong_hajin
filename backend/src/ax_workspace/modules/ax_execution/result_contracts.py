"""Public proposal projections and shape-preserving MCP command results."""
from typing import Generic, Literal, TypeVar

from pydantic import ConfigDict, JsonValue, RootModel
from typing_extensions import NotRequired, TypedDict


class ActionMaterialDraftView(TypedDict):
    material_draft_id: str
    action_item_id: str
    source_kind: str
    name: str
    content_type: str
    size_bytes: int
    url: str | None
    integrity_ref: str
    state: str
    expires_at: str
    claimed_task_id: str | None
    claimed_meeting_id: str | None


class ActionMaterialReceipt(TypedDict):
    material_draft_id: NotRequired[str]
    material_id: str
    attachment_id: str
    task_id: NotRequired[str]
    meeting_id: NotRequired[str]
    kind: str
    name: str
    content_type: str
    size_bytes: int
    source_kind: str
    url: str | None
    integrity_ref: str


class ActionPreviewField(TypedDict):
    id: str
    label: str
    value: str
    kind: str


class ActionCommandView(TypedDict):
    id: str
    label: str
    tone: str
    requires_reason: NotRequired[bool]


class ActionEditOption(TypedDict):
    value: str
    label: str
    organization_ids: NotRequired[list[str]]


class ActionEditField(TypedDict):
    id: str
    label: str
    type: str
    required: bool
    editable: bool
    empty_policy: NotRequired[Literal['forbid', 'null', 'omit', 'empty_string']]
    value: NotRequired[JsonValue]
    label_value: NotRequired[str | None]
    options: NotRequired[list[ActionEditOption]]


class ActionEditContract(TypedDict):
    editor: str
    base_submission_version: int
    values: dict[str, JsonValue]
    fields: list[ActionEditField]
    warnings: NotRequired[list[str]]
    #: 「초안 저장」 명령 id — 확정 없이 고친 값을 다음 회차로 남긴다. 저장이 열리는 두 kind 에만 선다 (SPEC-002 §4).
    save_command: NotRequired[str]


class ActionProposalResult(TypedDict):
    action_id: str
    conversation_id: str
    turn_id: str
    action_type: str
    title: str
    state: str
    version: int
    payload_hash: str
    payload_summary: str
    result: dict[str, JsonValue] | None
    material_drafts: list[ActionMaterialDraftView]
    material_results: list[ActionMaterialReceipt]
    audit_ref: str | None
    #: 만든 시각(ISO 8601) — 판단 대기 봉투의 `created_at` 과 같은 값. 채팅 카드도 「만든 지 며칠」을 센다 (SPEC-002 §2.9).
    created_at: str
    subject: str
    operation_label: str
    preview: list[ActionPreviewField]
    obsolete: bool
    commands: list[ActionCommandView]
    edit_contract: NotRequired[ActionEditContract]
    result_summary: NotRequired[str]


DomainResult = TypeVar('DomainResult')


class CommandResult(RootModel[DomainResult | ActionProposalResult], Generic[DomainResult]):
    """Validate either owned result without the SDK's generic-union `result` wrapper."""

    model_config = ConfigDict(json_schema_extra={'type': 'object'})
