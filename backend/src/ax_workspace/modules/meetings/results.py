"""Authorized Meeting projections shared by queries and mutation receipts."""
from typing import Literal
from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict
from ax_workspace.modules.work.extraction_results import ExtractionView
from ax_workspace.modules.work.task_results import TaskDetailResult
from ax_workspace.modules.work.request_results import WorkRequestMutationResult, WorkRequestDetailResult


class MeetingAttendeeView(TypedDict):
    member_id: str
    display_name: str


class MeetingLineageView(TypedDict):
    source_action_item_id: str | None
    source_decision_item_id: str | None
    source_submission_id: str | None
    source_review_decision_id: str | None
    confirmed_by: str | None


class MeetingNoteVersionView(TypedDict):
    version_id: str
    version: int
    body: str
    created_by: str
    created_at: str | None
    source_status: str | None
    source_evidence: list[dict[str, JsonValue]]


class MeetingNoteView(TypedDict):
    note_id: str
    lifecycle: str
    version: int
    body: str
    source_status: str | None
    versions: list[MeetingNoteVersionView]
    finalized_at: str | None
    finalized_by: str | None


class MeetingRawSegmentView(TypedDict):
    segment_id: str
    sequence: int
    source_segment_key: str
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None
    confirmed_member_id: str | None


class MeetingTranscriptView(TypedDict):
    transcript_revision_id: str
    recording_id: str
    revision: int
    state: str
    source_kind: str
    provider: str
    segments: list[MeetingRawSegmentView]


class MeetingLiveTranscriptReceipt(TypedDict):
    transcript_revision_id: str
    source_kind: str
    segment_count: int


class MeetingRefinedSegmentView(TypedDict):
    segment_id: str
    raw_start_segment_id: str
    raw_end_segment_id: str
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None
    confirmed_member_id: str | None
    correction_kind: str
    confidence: float | None


class MeetingRefinementView(TypedDict):
    refinement_revision_id: str
    raw_transcript_revision_id: str
    revision: int
    state: str
    provider_call_ref: str | None
    segments: list[MeetingRefinedSegmentView]


class MeetingSpeakerView(TypedDict):
    speaker_assignment_id: str
    transcript_revision_id: str
    speaker_label: str
    member_id: str
    scope: str
    raw_start_segment_id: str
    raw_end_segment_id: str
    source_audio_start_ms: int
    source_audio_end_ms: int
    source: str
    state: str


class MeetingRecordingView(TypedDict):
    recording_id: str
    meeting_id: str
    purpose: str
    state: str
    version: int
    content_type: str | None
    original_name: str | None
    size_bytes: int | None
    sha256: str | None
    started_at: str | None
    ended_at: str | None
    storage_key: None
    raw_transcript: NotRequired[MeetingTranscriptView | None]
    refinement: NotRequired[MeetingRefinementView | None]
    speaker_assignments: NotRequired[list[MeetingSpeakerView]]


class MeetingStatementView(TypedDict):
    statement_index: int
    kind: str
    text: str
    raw_start_ms: int
    raw_end_ms: int
    promoted: bool
    promoted_task_id: str | None
    promoted_work_request_id: str | None


class MeetingSummaryEvidenceView(TypedDict):
    statement_index: int
    kind: str
    text: str
    refinement_start_segment_id: str
    refinement_end_segment_id: str
    raw_start_segment_id: str
    raw_end_segment_id: str
    raw_start_ms: int
    raw_end_ms: int


class MeetingSummaryView(TypedDict):
    summary_id: str
    meeting_id: str
    raw_transcript_revision_id: str
    refinement_revision_id: str
    kind: str
    state: str
    version: int
    body: str
    provider_call_ref: str | None
    statements: list[MeetingStatementView]
    evidence: list[MeetingSummaryEvidenceView]


class MeetingSummaryAdoptionResult(TypedDict):
    summary: MeetingSummaryView
    note: MeetingNoteView


class MeetingResourceReference(TypedDict):
    type: str
    id: str
    title: str


class MeetingMaterialView(TypedDict):
    material_id: str
    binding_id: str
    attachment_id: str
    meeting_id: str
    meeting_version: int
    kind: str
    name: str
    resource: MeetingResourceReference | None
    content_type: str
    size_bytes: int
    source_kind: str
    url: str | None
    mutable_source: bool
    integrity_ref: str | None
    uploaded_by: str
    created_at: str
    removed_at: str | None
    extraction: ExtractionView | None


class MeetingView(TypedDict):
    kind: Literal['meeting']
    meeting_id: str
    organization_id: str
    owner_id: str
    title: str
    description: str | None
    starts_at: str | None
    ends_at: str | None
    visibility: str
    lifecycle: str
    version: int
    attendees: list[MeetingAttendeeView]
    lineage: MeetingLineageView
    note: NotRequired[MeetingNoteView | None]
    recordings: NotRequired[list[MeetingRecordingView]]
    summaries: NotRequired[list[MeetingSummaryView]]
    materials: NotRequired[list[MeetingMaterialView]]


class MeetingListResult(TypedDict):
    entries: list[MeetingView]


class MeetingFollowupResult(TypedDict):
    already_promoted: bool
    task: TaskDetailResult | None
    work_request: WorkRequestMutationResult | WorkRequestDetailResult | None
