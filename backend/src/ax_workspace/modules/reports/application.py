from __future__ import annotations

from ax_workspace.modules.reports.results import ReportHistoryResult, ReportStatusResult, ReportRecentView

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.reports.commands import ReportEditCommand, ReportSubmitCommand
from ax_workspace.modules.reports.results import ReportDraftResult, ReportGeneratedResult, ReportSubmissionResult, ReportSubmissionPreview

from contextlib import nullcontext
from datetime import date
from typing import Any, Callable, ContextManager, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import (
    DAILY_REPORT_EDIT,
    DAILY_REPORT_GENERATE,
    DAILY_REPORT_READ,
    DAILY_REPORT_SUBMIT,
    Principal,
)


class DailyReportNotFound(ValueError, ResourceNotFound):
    pass


class DailyReportAccessDenied(ValueError):
    pass


class DailyReportGenerationConflict(RuntimeError):
    pass


class DailyReportRepository(Protocol):
    def generation_causation(self, owner_id: str, causation_key: str) -> ContextManager[None]: ...

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

    def history(self, owner_id: str, report_id: str) -> ReportHistoryResult: ...

    def status_for_date(self, owner_id: str, report_date: str) -> ReportStatusResult: ...

    def recent(self, owner_id: str, *, limit: int = 3) -> list[ReportRecentView]: ...

    def material_revisions(self, owner_id: str, *, report_id: str | None = None, include_history: bool = False) -> list[dict[str, Any]]: ...
    def request_generation(self, owner_id: str, report_date: str, causation_key: str | None) -> Any: ...
    def generation(self, generation_id: UUID, *, lock: bool = False) -> Any | None: ...
    def claim_generation(self, generation: Any, *, attempt: int, lease_token: UUID, stale_after_seconds: int) -> str: ...
    def generation_started_external_call(self, generation: Any) -> bool: ...
    def heartbeat_generation(self, generation: Any, *, stage_timeout_seconds: int, total_timeout_seconds: int) -> bool: ...
    def complete_generation(self, generation: Any, draft: Any) -> None: ...
    def record_generation_workflow_run(self, generation: Any, workflow_run_id: UUID) -> None: ...
    def retry_generation(self, generation: Any, code: str) -> None: ...
    def fail_generation(self, generation: Any, code: str) -> None: ...
    def mark_generation_needs_verification(self, generation: Any, code: str) -> None: ...


class DailyReportDraftWorkflowPort(Protocol):
    def run(
        self,
        principal: Principal,
        report_date: str,
        *,
        run_id: UUID | None = None,
        on_run_created: Callable[[UUID], None] | None = None,
    ) -> dict[str, Any]: ...




class DailyReportApplication:
    def __init__(self, reports: DailyReportRepository, workflow: DailyReportDraftWorkflowPort, *, publish_submission: Callable[[UUID], None]) -> None:
        self._reports = reports
        self._workflow = workflow
        self._publish_submission = publish_submission

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
        causation_guard = (
            self._reports.generation_causation(str(principal.id), causation_key)
            if causation_key
            else nullcontext()
        )
        # The PostgreSQL adapter owns a separate AUTOCOMMIT advisory connection.
        # It serializes lookup and workflow execution without retaining the report
        # session's transaction while the external provider is running.
        with causation_guard:
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

    def request_generation(
        self,
        principal: Principal,
        report_date: str,
        causation_key: str | None = None,
    ) -> Any:
        self._validate_generation(principal, report_date)
        return self._reports.request_generation(str(principal.id), report_date, causation_key)

    def generation_input(
        self,
        principal: Principal | None,
        generation_id: UUID,
        *,
        attempt: int,
        lease_token: UUID,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        generation = self._reports.generation(generation_id, lock=True)
        if generation is None:
            raise DailyReportNotFound("daily report generation was not found")
        if generation.state == "completed":
            return {"completed": True}
        claimed = self._reports.claim_generation(
            generation,
            attempt=attempt,
            lease_token=lease_token,
            stale_after_seconds=stale_after_seconds,
        )
        if claimed != "claimed":
            return {claimed: True}
        try:
            if principal is None or str(principal.id) != generation.owner_id:
                raise DailyReportAccessDenied("daily report generation actor is no longer authorized")
            self._require(principal, DAILY_REPORT_GENERATE)
        except DailyReportAccessDenied:
            self._reports.fail_generation(generation, "access_denied")
            return {"denied": True}
        return {
            "generation_id": str(generation.id),
            "owner_id": generation.owner_id,
            "report_date": generation.report_date,
            "causation_key": generation.causation_key,
        }

    def execute_generation(self, principal: Principal, generation_id: UUID, *, lease_token: UUID) -> ReportGeneratedResult:
        generation = self._reports.generation(generation_id, lock=True)
        if generation is None:
            raise DailyReportNotFound("daily report generation was not found")
        self._require_generation_lease(generation, lease_token)
        self._validate_generation(principal, generation.report_date)
        generated = self._workflow.run(
            principal,
            generation.report_date,
            run_id=generation.workflow_run_id,
            on_run_created=lambda run_id: self._reports.record_generation_workflow_run(generation, run_id),
        )
        # The workflow commits before its provider call. Refresh and lock the domain fence before the draft/result write.
        generation = self._reports.generation(generation_id, lock=True)
        if generation is None:
            raise DailyReportNotFound("daily report generation was not found")
        self._require_generation_lease(generation, lease_token)
        draft = self._reports.create_draft(
            generation.owner_id,
            generation.report_date,
            generated["source_refs"],
            generated["run_id"],
            generated["definition_version_id"],
            generated["body"],
            f"report-generation:{generation.id}",
        )
        self._reports.complete_generation(generation, draft)
        return self._generated_view(draft, generated["state"])

    def heartbeat_generation(
        self,
        generation_id: UUID,
        *,
        lease_token: UUID,
        stage_timeout_seconds: int,
        total_timeout_seconds: int,
    ) -> bool:
        generation = self._reports.generation(generation_id, lock=True)
        if generation is None:
            raise DailyReportNotFound("daily report generation was not found")
        self._require_generation_lease(generation, lease_token)
        return self._reports.heartbeat_generation(
            generation,
            stage_timeout_seconds=stage_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
        )

    def retry_generation(self, generation_id: UUID, *, lease_token: UUID, code: str) -> None:
        generation = self._owned_generation(generation_id, lease_token)
        self._reports.retry_generation(generation, code)

    def fail_generation(self, generation_id: UUID, *, lease_token: UUID, code: str) -> None:
        generation = self._owned_generation(generation_id, lease_token)
        self._reports.fail_generation(generation, code)

    def mark_generation_needs_verification(
        self, generation_id: UUID, *, lease_token: UUID, code: str
    ) -> None:
        generation = self._owned_generation(generation_id, lease_token)
        self._reports.mark_generation_needs_verification(generation, code)

    def abandon_generation(self, generation_id: UUID, *, lease_token: UUID) -> str:
        """결과 없이 끝난 시도를 닫는다.

        외부 호출이 이미 시작됐다면 결과를 모르는 것이므로 확인 필요로 고정한다. 호출에 닿기 전에
        끝났다면 아무 일도 일어나지 않았으므로 평범한 재시도로 되돌린다.
        """
        generation = self._owned_generation(generation_id, lease_token)
        if self._reports.generation_started_external_call(generation):
            self._reports.mark_generation_needs_verification(generation, "provider_result_uncertain")
            return "needs_verification"
        self._reports.retry_generation(generation, "worker_attempt_lost")
        return "retry"

    def _owned_generation(self, generation_id: UUID, lease_token: UUID) -> Any:
        generation = self._reports.generation(generation_id, lock=True)
        if generation is None:
            raise DailyReportNotFound("daily report generation was not found")
        self._require_generation_lease(generation, lease_token)
        return generation

    @staticmethod
    def _require_generation_lease(generation: Any, lease_token: UUID) -> None:
        if generation.state != "running" or generation.lease_token != lease_token:
            raise DailyReportGenerationConflict("daily report generation lease is stale")

    def _validate_generation(self, principal: Principal, report_date: str) -> None:
        self._require(principal, DAILY_REPORT_GENERATE)
        parsed_date = date.fromisoformat(report_date)
        if parsed_date > date.today():
            raise ValueError("report date cannot be in the future")

    @staticmethod
    def _generated_view(draft: Any, workflow_state: str) -> ReportGeneratedResult:
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
    ) -> ReportDraftResult:
        self._require(principal, DAILY_REPORT_EDIT)
        command = ReportEditCommand(report_id=report_id, draft_id=draft_id, expected_version=expected_version, body=body, include_source_refs=include_source_refs, exclude_source_refs=exclude_source_refs)
        draft = self._reports.edit_draft(str(principal.id), **command.values())
        return self._draft_view(draft)

    def submit(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None,
    ) -> ReportSubmissionResult:
        self._require(principal, DAILY_REPORT_SUBMIT)
        command = ReportSubmitCommand(report_id=report_id, draft_id=draft_id, expected_version=expected_version, reason=reason)
        submission = self._reports.submit(str(principal.id), **command.model_dump(mode='json'))
        self._publish_submission(submission.id)
        return {
            "report_id": str(submission.report_id),
            "submission_id": str(submission.id),
            "submission_version": submission.submission_version,
            "status": "submitted",
            "body": submission.body,
            "source_refs": submission.source_refs,
            "submitted_at": submission.submitted_at.isoformat(),
        }

    def submission_preview(self, principal: Principal, report_id: str, draft_id: str) -> ReportSubmissionPreview:
        """The exact owned draft the submit capability will publish, without a write."""
        self._require(principal, DAILY_REPORT_SUBMIT)
        history = self._reports.history(str(principal.id), report_id)
        draft = next((item for item in history['drafts'] if item['draft_id'] == draft_id), None)
        if draft is None:
            raise DailyReportNotFound('daily report draft was not found')
        return {'report_id': history['report_id'], 'report_date': history['report_date'], 'draft_id': draft['draft_id'], 'draft_version': draft['version'], 'body': draft['body'], 'source_refs': draft['source_refs'], 'status': history['status']}

    def material_revisions(self, principal: Principal, *, report_id: str | None = None, include_history: bool = False) -> list[dict[str, Any]]:
        self._require(principal, DAILY_REPORT_READ)
        return self._reports.material_revisions(str(principal.id), report_id=report_id, include_history=include_history)

    def recent(self, principal: Principal, *, limit: int = 3) -> list[ReportRecentView]:
        """A person's own recent reports. Reading a report is reading one's own; nobody else's is listed here."""
        self._require(principal, DAILY_REPORT_READ)
        return self._reports.recent(str(principal.id), limit=limit)

    def history(self, principal: Principal, report_id: str) -> ReportHistoryResult:
        self._require(principal, DAILY_REPORT_READ)
        return self._reports.history(str(principal.id), report_id)

    def status_for_date(self, principal: Principal, report_date: str) -> ReportStatusResult:
        self._require(principal, DAILY_REPORT_READ)
        date.fromisoformat(report_date)
        return self._reports.status_for_date(str(principal.id), report_date)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise DailyReportAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _draft_view(draft: Any) -> ReportDraftResult:
        return {
            "report_id": str(draft.report_id),
            "draft_id": str(draft.id),
            "draft_version": draft.version,
            "body": draft.body,
            "source_refs": draft.source_refs,
            "status": draft.status,
        }
