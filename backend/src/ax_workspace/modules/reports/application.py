from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from ax_workspace.modules.organization_access.domain import (
    DAILY_REPORT_EDIT,
    DAILY_REPORT_GENERATE,
    DAILY_REPORT_READ,
    DAILY_REPORT_SUBMIT,
    Principal,
)


class DailyReportAccessDenied(ValueError):
    pass


class DailyReportRepository(Protocol):
    def generated_draft_for_causation(self, owner_id: str, causation_key: str) -> tuple[Any, str] | None: ...

    def create_draft(
        self,
        owner_id: str,
        report_date: str,
        source_refs: list[dict[str, Any]],
        workflow_run_id: str,
        definition_version_id: str,
        body: str,
        causation_key: str | None = None,
    ) -> Any: ...

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

    def generate_draft(
        self,
        principal: Principal,
        report_date: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        self._require(principal, DAILY_REPORT_GENERATE)
        parsed_date = date.fromisoformat(report_date)
        if parsed_date > date.today():
            raise ValueError("report date cannot be in the future")
        if causation_key:
            existing = self._reports.generated_draft_for_causation(str(principal.id), causation_key)
            if existing is not None:
                draft, workflow_state = existing
                return self._generated_view(draft, workflow_state)
        generated = self._workflow.run(principal, report_date)
        source_refs = generated["source_refs"]
        draft = self._reports.create_draft(
            str(principal.id),
            report_date,
            source_refs,
            generated["run_id"],
            generated["definition_version_id"],
            generated["body"],
            causation_key,
        )
        return self._generated_view(draft, generated["state"])

    @staticmethod
    def _generated_view(draft: Any, workflow_state: str) -> dict[str, Any]:
        return {
            "report_id": str(draft.report_id),
            "draft_id": str(draft.id),
            "draft_version": draft.version,
            "body": draft.body,
            "status": draft.status,
            "source_refs": draft.source_refs,
            "workflow_run_id": str(draft.workflow_run_id),
            "definition_version_id": str(draft.definition_version_id),
            "workflow_state": workflow_state,
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
        self._require(principal, DAILY_REPORT_EDIT)
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
        self._require(principal, DAILY_REPORT_SUBMIT)
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
        self._require(principal, DAILY_REPORT_READ)
        return self._reports.history(str(principal.id), report_id)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise DailyReportAccessDenied(f"{capability} capability is required")

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
