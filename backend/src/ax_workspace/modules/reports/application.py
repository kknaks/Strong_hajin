from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from ax_workspace.modules.ax_execution.ai import AiGeneration, AiProvider
from ax_workspace.modules.organization_access.domain import Principal


class DailyReportRepository(Protocol):
    def create_draft(self, owner_id: str, report_date: str, source_refs: list[dict[str, Any]], workflow_run_id: str, definition_version_id: str, body: str) -> Any: ...

    def edit_draft(
        self,
        owner_id: str,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]],
        exclude_source_refs: list[dict[str, Any]],
    ) -> Any: ...

    def submit(self, owner_id: str, report_id: str, draft_id: str, expected_version: int, reason: str | None) -> Any: ...

    def history(self, owner_id: str, report_id: str) -> dict[str, Any]: ...


class DailyReportDraftWorkflowPort(Protocol):
    def run(self, principal: Principal, report_date: str) -> dict[str, Any]: ...




class DailyReportApplication:
    def __init__(self, reports: DailyReportRepository, workflow: DailyReportDraftWorkflowPort) -> None:
        self._reports = reports
        self._workflow = workflow

    def generate_draft(self, principal: Principal, report_date: str) -> dict[str, Any]:
        parsed_date = date.fromisoformat(report_date)
        if parsed_date > date.today():
            raise ValueError("report date cannot be in the future")
        generated = self._workflow.run(principal, report_date)
        source_refs = generated["source_refs"]
        draft = self._reports.create_draft(
            str(principal.id),
            report_date,
            source_refs,
            generated["run_id"],
            generated["definition_version_id"],
            generated["body"],
        )
        return {
            "report_id": str(draft.report_id),
            "draft_id": str(draft.id),
            "draft_version": draft.version,
            "body": draft.body,
            "status": draft.status,
            "source_refs": draft.source_refs,
            "workflow_run_id": generated["run_id"],
            "definition_version_id": generated["definition_version_id"],
            "workflow_state": generated["state"],
            "submission_status": "unsubmitted",
        }

    def edit(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]] | None = None,
        exclude_source_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        draft = self._reports.edit_draft(
            str(principal.id),
            report_id,
            draft_id,
            expected_version,
            body,
            include_source_refs or [],
            exclude_source_refs or [],
        )
        return self._draft_view(draft)

    def submit(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]:
        submission = self._reports.submit(
            str(principal.id), report_id, draft_id, expected_version, reason
        )
        return {
            "report_id": str(submission.report_id),
            "submission_id": str(submission.id),
            "submission_version": submission.submission_version,
            "status": "submitted",
            "body": submission.body,
            "source_refs": submission.source_refs,
            "submitted_at": submission.submitted_at.isoformat(),
        }

    def history(self, principal: Principal, report_id: str) -> dict[str, Any]:
        return self._reports.history(str(principal.id), report_id)

    @staticmethod
    def _draft_view(draft: Any) -> dict[str, Any]:
        return {
            "report_id": str(draft.report_id),
            "draft_id": str(draft.id),
            "draft_version": draft.version,
            "body": draft.body,
            "source_refs": draft.source_refs,
            "status": draft.status,
        }
