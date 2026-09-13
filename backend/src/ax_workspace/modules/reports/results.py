"""Report drafts and submitted revisions have distinct receipts."""
from typing import Literal
from typing_extensions import NotRequired, TypedDict
from pydantic import JsonValue


class ReportSourceResult(TypedDict, extra_items=JsonValue):
    """Captured source identity and metadata, without adding input defaults."""
    task_id: str
    task_version: int
    occurred_at: str
    state: NotRequired[str | None]


class ReportDraftResult(TypedDict):
    report_id: str
    draft_id: str
    draft_version: int
    body: str
    source_refs: list[ReportSourceResult]
    status: str


class ReportGeneratedResult(ReportDraftResult):
    workflow_run_id: str
    definition_version_id: str
    workflow_state: str
    submission_status: Literal['unsubmitted']


class ReportSubmissionResult(TypedDict):
    report_id: str
    submission_id: str
    submission_version: int
    status: Literal['submitted']
    body: str
    source_refs: list[ReportSourceResult]
    submitted_at: str


class ReportSubmissionPreview(ReportDraftResult):
    report_date: str


class ReportDraftHistoryView(TypedDict):
    draft_id: str
    version: int
    body: str
    source_refs: list[ReportSourceResult]
    workflow_run_id: str
    definition_version_id: str


class ReportSubmissionHistoryView(TypedDict):
    submission_id: str
    version: int
    body: str
    source_refs: list[ReportSourceResult]
    reason: str | None
    submitted_at: str


class ReportHistoryResult(TypedDict):
    report_id: str
    report_date: str
    status: str
    drafts: list[ReportDraftHistoryView]
    submissions: list[ReportSubmissionHistoryView]


class ReportRecentView(TypedDict):
    report_id: str
    report_date: str
    status: str
    source_refs: list[ReportSourceResult]


class ReportStatusResult(TypedDict):
    report_date: str
    status: Literal['not_started', 'draft', 'submitted']
    report_id: str | None
    generation_id: str | None
    generation_status: Literal['queued', 'running', 'completed', 'failed', 'needs_verification'] | None
    generation_error_code: str | None
