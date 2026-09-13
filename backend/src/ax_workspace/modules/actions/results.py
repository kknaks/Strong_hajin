"""Judgment envelopes retain immutable rounds and the originating operation's extensions."""
from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict

from ax_workspace.modules.ax_execution.result_contracts import (
    ActionCommandView, ActionEditContract, ActionMaterialDraftView,
    ActionMaterialReceipt, ActionPreviewField,
)


class WaitingMemberView(TypedDict):
    member_id: str
    display_name: str


class ActionResourceView(TypedDict):
    type: str
    id: str


class ActionEnvelopeExtensions(TypedDict):
    suggested_changes: NotRequired[dict[str, JsonValue]]
    derived_task_id: NotRequired[str | None]
    derived_meeting_id: NotRequired[str | None]
    material_drafts: NotRequired[list[ActionMaterialDraftView]]
    material_results: NotRequired[list[ActionMaterialReceipt]]
    execution_result: NotRequired[dict[str, JsonValue]]
    result_summary: NotRequired[str]
    edit_contract: NotRequired[ActionEditContract]


class ActionEnvelopeResult(ActionEnvelopeExtensions):
    action_item_id: str
    kind: str
    status: str
    subject: str
    operation_label: str
    current_question: str
    preview: list[ActionPreviewField]
    allowed_commands: list[ActionCommandView]
    submission_version: int
    waiting_on: WaitingMemberView | None
    resource: ActionResourceView
    expected_version: int | None


class ActionEvidenceView(TypedDict):
    attachment_id: str
    evidence_role: str
    fixed_snapshot_ref: str


class ActionDecisionView(TypedDict):
    review_decision_id: str
    actor_member_id: str
    decision: str
    reason: str | None
    evidence_hash: str | None
    decided_at: str
    suggested_changes: NotRequired[dict[str, JsonValue]]


class ActionRoundView(TypedDict):
    submission_id: str
    submission_version: int
    submitted_by: str | None
    submitted_at: str
    content_hash: str
    snapshot: dict[str, JsonValue]
    diff: dict[str, JsonValue] | None
    evidence: list[ActionEvidenceView]
    evidence_hash: str | None
    decisions: list[ActionDecisionView]
    capture_kind: NotRequired[str]


class DiscussionAttachmentView(TypedDict):
    attachment_id: str
    name: str
    content_type: str
    size_bytes: int
    uploaded_by: str
    created_at: str


class ActionDiscussionView(TypedDict):
    comment_id: str
    author_member_id: str
    body: str
    created_at: str
    edited_at: str | None
    attachments: list[DiscussionAttachmentView]


class ActionDetailResult(ActionEnvelopeResult):
    rounds: list[ActionRoundView]
    discussion: list[ActionDiscussionView]
