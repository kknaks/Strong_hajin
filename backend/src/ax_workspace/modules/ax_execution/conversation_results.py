"""Conversation responses preserve delivered messages and reauthorized resource projections."""
from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict
from ax_workspace.modules.ax_execution.result_contracts import ActionProposalResult


class ConversationMessageView(TypedDict):
    message_id: str
    turn_id: str | None
    role: str
    body: str
    sequence: int
    state: str
    body_state: str
    idempotency_key: str | None
    follow_up_candidate_id: str | None
    created_at: str


class ConversationFollowUpView(TypedDict):
    candidate_id: str
    source_turn_id: str
    label: str
    user_text: str
    selected_message_id: str | None


class ConversationTurnView(TypedDict):
    turn_id: str
    state: str
    progress_state: str
    current_tool_display_name: str | None
    attempt: int
    queued_at: str
    execution_started_at: str | None
    execution_completed_at: str | None
    queue_wait_ms: int | None
    run_ms: int | None
    retry_of_turn_id: str | None
    usage: dict[str, JsonValue] | None
    provider_run_ref: str | None
    provider_session_ref: str | None
    error: str | None
    follow_up_candidates: list[ConversationFollowUpView]


class ConversationContextView(TypedDict):
    message_id: str
    turn_id: str | None
    resource_type: str
    resource_id: str
    resource_version: int
    summary: str
    included: bool


class ConversationToolView(TypedDict):
    turn_id: str
    sequence: int
    provider_call_id: str | None
    tool_name: str
    display_name: str
    input_summary: str
    state: str
    result_summary: str | None
    error_summary: str | None
    latency_ms: int | None
    started_at: str | None
    completed_at: str | None
    target_resource_id: str | None
    target_resource_version: str | None
    audit_ref: str | None


class ConversationAnswerResourceView(TypedDict):
    reference_id: str
    turn_id: str
    sequence: int
    resource_type: str
    resource_id: str
    resource_version: int | None
    source_contexts: list[dict[str, JsonValue]] | None
    integrity_ref: str | None
    source_locator: dict[str, JsonValue] | None
    title: str
    state: str | None
    current_version: int | None
    changed_since: bool
    origin: NotRequired[str]


class ConversationGraphReceiptView(TypedDict):
    receipt_id: str
    turn_id: str
    sequence: int
    kind: str
    node_ref: str | None
    node_title: str | None
    edge_kind: str | None
    from_ref: str | None
    from_title: str | None
    to_ref: str | None
    to_title: str | None
    source_contexts: list[dict[str, JsonValue]] | None
    integrity_ref: str | None
    observed_at: str


class ConversationMaterialEvidenceView(TypedDict):
    evidence_id: str
    turn_id: str
    material_id: str
    attachment_id: str
    chunk_id: str
    name: str
    integrity_ref: str
    page: int | None
    excerpt: str
    source_contexts: list[dict[str, JsonValue]]
    source_locator: dict[str, JsonValue] | None
    header_context: dict[str, JsonValue] | None
    extraction: dict[str, JsonValue] | None
    query: str
    rank: int
    recorded_at: str
    origin: str
    source_resource_type: str
    source_resource_id: str
    source_resource_title: str


class ConversationView(TypedDict):
    conversation_id: str
    title: str
    version: int
    messages: list[ConversationMessageView]
    turns: list[ConversationTurnView]
    context_references: list[ConversationContextView]
    tool_invocations: list[ConversationToolView]
    actions: list[ActionProposalResult]
    answer_resources: list[ConversationAnswerResourceView]
    graph_receipts: list[ConversationGraphReceiptView]
    material_evidence: list[ConversationMaterialEvidenceView]


class ConversationSearchHit(TypedDict):
    turn_id: str
    conversation_id: str
    conversation_title: str
    excerpt: str
    created_at: str | None


class ConversationSearchResult(TypedDict):
    turns: list[ConversationSearchHit]
