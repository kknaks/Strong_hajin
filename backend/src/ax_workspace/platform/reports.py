from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.ax_execution.ai import (
    AiGenerationRequest,
    AiProvider,
    ProviderFailure,
)
from ax_workspace.modules.reports.workflow_metadata import validate_definition
from ax_workspace.platform.persistence import (
    DailyReportSubmissionRecord,
    DailyReportRecord,
    ProviderCallRecord,
    ReportAuditEventRecord,
    ReportDraftRecord,
    WorkflowDefinitionVersionRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
)


class WorkRecordSourcePort(Protocol):
    """Canonical Work projection called by the workflow's operation.query node."""

    def list(self, principal: Any, report_date: str) -> list[dict[str, object]]: ...


class SqlAlchemyDailyReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_draft(
        self,
        owner_id: str,
        report_date: str,
        source_refs: list[dict[str, Any]],
        workflow_run_id: str,
        definition_version_id: str,
        body: str,
        causation_key: str | None = None,
    ) -> ReportDraftRecord:
        report = self._session.scalar(
            select(DailyReportRecord).where(
                DailyReportRecord.owner_id == owner_id,
                DailyReportRecord.report_date == report_date,
            )
        )
        if report is None:
            report = DailyReportRecord(
                id=uuid4(),
                owner_id=owner_id,
                report_date=report_date,
                status="draft",
            )
            self._session.add(report)
            self._session.flush()

        previous_version = self._session.scalar(
            select(ReportDraftRecord.version)
            .where(ReportDraftRecord.report_id == report.id)
            .order_by(ReportDraftRecord.version.desc())
            .limit(1)
        )
        draft = ReportDraftRecord(
            id=uuid4(),
            owner_id=owner_id,
            report_date=report_date,
            status="draft",
            report_id=report.id,
            source_refs=source_refs,
            version=(previous_version or 0) + 1,
            body=body,
            workflow_run_id=UUID(workflow_run_id),
            definition_version_id=UUID(definition_version_id),
            causation_key=causation_key,
        )
        self._session.add(draft)
        self._audit(report.id, owner_id, "daily_report.draft_generated", {"draft_id": str(draft.id), "version": draft.version})
        self._session.flush()
        return draft

    def generated_draft_for_causation(
        self,
        owner_id: str,
        causation_key: str,
    ) -> tuple[ReportDraftRecord, str] | None:
        draft = self._session.scalar(
            select(ReportDraftRecord).where(
                ReportDraftRecord.owner_id == owner_id,
                ReportDraftRecord.causation_key == causation_key,
            )
        )
        if draft is None:
            return None
        run = self._session.get(WorkflowRunRecord, draft.workflow_run_id)
        if run is None:
            raise ValueError("daily report generation provenance was not found")
        return draft, run.state

    def edit_draft(
        self,
        owner_id: str,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]],
        exclude_source_refs: list[dict[str, Any]],
    ) -> ReportDraftRecord:
        report = self._owned_report(owner_id, report_id, lock=True)
        draft = self._owned_draft(report.id, draft_id, lock=True)
        if draft.version != expected_version:
            raise ValueError("daily report draft version is stale")
        if not body.strip():
            raise ValueError("daily report body is required")

        source_refs = self._amend_sources(draft.source_refs, include_source_refs, exclude_source_refs)
        new_draft = ReportDraftRecord(
            id=uuid4(),
            owner_id=owner_id,
            report_date=report.report_date,
            status="draft",
            report_id=report.id,
            source_refs=source_refs,
            version=draft.version + 1,
            body=body.strip(),
            workflow_run_id=draft.workflow_run_id,
            definition_version_id=draft.definition_version_id,
        )
        report.status = "draft"
        self._session.add(new_draft)
        self._audit(
            report.id,
            owner_id,
            "daily_report.draft_edited",
            {"previous_draft_id": str(draft.id), "draft_id": str(new_draft.id), "version": new_draft.version},
        )
        self._session.flush()
        return new_draft

    def submit(
        self,
        owner_id: str,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None,
    ) -> DailyReportSubmissionRecord:
        report = self._owned_report(owner_id, report_id, lock=True)
        draft = self._owned_draft(report.id, draft_id, lock=True)
        if draft.version != expected_version:
            raise ValueError("daily report draft version is stale")
        previous_submission = self._session.scalar(
            select(DailyReportSubmissionRecord)
            .where(DailyReportSubmissionRecord.report_id == report.id)
            .order_by(DailyReportSubmissionRecord.submission_version.desc())
            .limit(1)
        )
        if previous_submission is not None and not reason:
            raise ValueError("a correction reason is required after the first submission")

        submission = DailyReportSubmissionRecord(
            id=uuid4(),
            report_id=report.id,
            submission_version=(previous_submission.submission_version if previous_submission else 0) + 1,
            submitter_id=owner_id,
            report_date=report.report_date,
            body=draft.body,
            source_refs=draft.source_refs,
            reason=reason,
            submitted_at=datetime.now(UTC),
        )
        report.status = "submitted"
        self._session.add(submission)
        self._audit(
            report.id,
            owner_id,
            "daily_report.submitted",
            {"draft_id": str(draft.id), "submission_version": submission.submission_version},
        )
        self._session.flush()
        return submission

    def history(self, owner_id: str, report_id: str) -> dict[str, Any]:
        report = self._owned_report(owner_id, report_id)
        drafts = list(
            self._session.scalars(
                select(ReportDraftRecord)
                .where(ReportDraftRecord.report_id == report.id)
                .order_by(ReportDraftRecord.version)
            )
        )
        submissions = list(
            self._session.scalars(
                select(DailyReportSubmissionRecord)
                .where(DailyReportSubmissionRecord.report_id == report.id)
                .order_by(DailyReportSubmissionRecord.submission_version)
            )
        )
        return {
            "report_id": str(report.id),
            "report_date": report.report_date,
            "status": report.status,
            "drafts": [
                {
                    "draft_id": str(draft.id),
                    "version": draft.version,
                    "body": draft.body,
                    "source_refs": draft.source_refs,
                }
                for draft in drafts
            ],
            "submissions": [
                {
                    "submission_id": str(submission.id),
                    "version": submission.submission_version,
                    "body": submission.body,
                    "source_refs": submission.source_refs,
                    "reason": submission.reason,
                    "submitted_at": submission.submitted_at.isoformat(),
                }
                for submission in submissions
            ],
        }

    def status_for_date(self, owner_id: str, report_date: str) -> dict[str, Any]:
        report = self._session.scalar(
            select(DailyReportRecord).where(
                DailyReportRecord.owner_id == owner_id,
                DailyReportRecord.report_date == report_date,
            )
        )
        return {
            "report_date": report_date,
            "status": report.status if report is not None else "not_started",
            "report_id": str(report.id) if report is not None else None,
        }

    def _owned_report(self, owner_id: str, report_id: str, *, lock: bool = False) -> DailyReportRecord:
        statement = select(DailyReportRecord).where(
            DailyReportRecord.id == UUID(report_id), DailyReportRecord.owner_id == owner_id
        )
        report = self._session.scalar(statement.with_for_update() if lock else statement)
        if report is None:
            raise ValueError("daily report was not found")
        return report

    def _owned_draft(self, report_id: UUID, draft_id: str, *, lock: bool = False) -> ReportDraftRecord:
        statement = select(ReportDraftRecord).where(
            ReportDraftRecord.id == UUID(draft_id), ReportDraftRecord.report_id == report_id
        )
        draft = self._session.scalar(statement.with_for_update() if lock else statement)
        if draft is None:
            raise ValueError("daily report draft was not found")
        return draft

    @staticmethod
    def _amend_sources(
        source_refs: list[dict[str, Any]],
        include_source_refs: list[dict[str, Any]],
        exclude_source_refs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        excluded = {SqlAlchemyDailyReportRepository._source_key(item) for item in exclude_source_refs}
        amended = [item for item in source_refs if SqlAlchemyDailyReportRepository._source_key(item) not in excluded]
        existing = {SqlAlchemyDailyReportRepository._source_key(item) for item in amended}
        for item in include_source_refs:
            key = SqlAlchemyDailyReportRepository._source_key(item)
            if key not in existing:
                amended.append(item)
                existing.add(key)
        return amended

    @staticmethod
    def _source_key(source: dict[str, Any]) -> tuple[str, int, str]:
        try:
            return (str(source["task_id"]), int(source["task_version"]), str(source["occurred_at"]))
        except KeyError as error:
            raise ValueError("source references require task_id, task_version, and occurred_at") from error

    def _audit(self, report_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._session.add(
            ReportAuditEventRecord(
                id=uuid4(),
                report_id=report_id,
                actor_id=actor_id,
                event_type=event_type,
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        )


class SqlAlchemyDailyReportDraftWorkflow:
    """Safe interpreter for the persisted daily-report-generation@1 metadata."""

    def __init__(
        self,
        session: Session,
        sources: WorkRecordSourcePort,
        provider: AiProvider,
    ) -> None:
        self._session = session
        self._sources = sources
        self._provider = provider

    def run(self, principal: Any, report_date: str) -> dict[str, Any]:
        definition = self._session.scalar(
            select(WorkflowDefinitionVersionRecord).where(
                WorkflowDefinitionVersionRecord.workflow_id == "daily-report-generation",
                WorkflowDefinitionVersionRecord.version == "1",
                WorkflowDefinitionVersionRecord.status == "published",
            )
        )
        if definition is None:
            raise RuntimeError("daily-report-generation@1 is not installed")
        validate_definition(definition.definition)

        now = datetime.now(UTC)
        run = WorkflowRunRecord(
            id=uuid4(),
            definition_version_id=definition.id,
            workflow_id=definition.workflow_id,
            initiator_id=str(principal.id),
            state="running",
            input_snapshot={"report_date": report_date},
            created_at=now,
            updated_at=now,
        )
        self._session.add(run)
        self._session.flush()

        results: dict[str, Any] = {}
        try:
            for node in definition.definition["nodes"]:
                execution = self._start_node(run.id, node, results)
                result = self._execute_node(node, principal, report_date, results, execution)
                execution.state = "completed"
                execution.result = result
                execution.completed_at = datetime.now(UTC)
                results[node["id"]] = result

            run.state = "completed"
            run.updated_at = datetime.now(UTC)
            self._session.flush()
        except ProviderFailure as error:
            run.state = "failed"
            run.updated_at = datetime.now(UTC)
            self._session.flush()
            raise error
        except Exception as error:
            run.state = "failed"
            run.updated_at = datetime.now(UTC)
            self._session.flush()
            raise RuntimeError(f"daily report workflow failed: {type(error).__name__}") from error

        return {
            "run_id": str(run.id),
            "definition_version_id": str(definition.id),
            "state": run.state,
            "body": results["validate"]["body"],
            "source_refs": results["sources"]["source_refs"],
        }

    def _start_node(
        self,
        run_id: UUID,
        node: dict[str, Any],
        results: dict[str, Any],
    ) -> WorkflowNodeExecutionRecord:
        execution = WorkflowNodeExecutionRecord(
            id=uuid4(),
            run_id=run_id,
            node_id=node["id"],
            state="running",
            input_snapshot={input_id: results[input_id] for input_id in node.get("inputs", [])},
            result=None,
            normalized_error=None,
            retry_count=0,
            created_at=datetime.now(UTC),
            completed_at=None,
        )
        self._session.add(execution)
        self._session.flush()
        return execution

    def _execute_node(
        self,
        node: dict[str, Any],
        principal: Any,
        report_date: str,
        results: dict[str, Any],
        execution: WorkflowNodeExecutionRecord,
    ) -> dict[str, Any]:
        node_type = node["type"]
        if node_type == "operation.query":
            return {"source_refs": self._sources.list(principal, report_date)}
        if node_type == "template.render":
            source_refs = results["sources"]["source_refs"]
            return {"rendered_prompt": self._render_prompt(report_date, source_refs)}
        if node_type == "llm.generate":
            return self._generate(execution, results["render"]["rendered_prompt"])
        if node_type == "output.validate":
            body = results["generate"]["body"].strip()
            if not body:
                raise ValueError("daily report provider returned an empty draft")
            return {"body": body}
        raise ValueError(f"unregistered node type: {node_type}")

    def _generate(self, execution: WorkflowNodeExecutionRecord, rendered_prompt: str) -> dict[str, Any]:
        try:
            generation = self._provider.generate(
                AiGenerationRequest(
                    prompt=rendered_prompt,
                    output_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"body": {"type": "string", "minLength": 1}},
                        "required": ["body"],
                    },
                )
            )
        except ProviderFailure as error:
            execution.state = "failed"
            execution.normalized_error = str(error)
            execution.completed_at = datetime.now(UTC)
            self._session.add(
                ProviderCallRecord(
                    id=uuid4(),
                    node_execution_id=execution.id,
                    provider_run_ref=error.provenance.provider_run_ref,
                    provider_session_ref=error.provenance.provider_session_ref,
                    requested_model=error.provenance.requested_model,
                    observed_model=error.provenance.observed_model,
                    requested_tier=error.provenance.requested_tier,
                    observed_tier=error.provenance.observed_tier,
                    latency_ms=error.provenance.latency_ms,
                    usage=error.provenance.usage,
                    status="failed",
                    normalized_error=str(error),
                )
            )
            self._session.flush()
            raise

        self._session.add(
            ProviderCallRecord(
                id=uuid4(),
                node_execution_id=execution.id,
                provider_run_ref=generation.provider_run_ref,
                provider_session_ref=generation.provider_session_ref,
                requested_model=generation.requested_model,
                observed_model=generation.observed_model,
                requested_tier=generation.requested_tier,
                observed_tier=generation.observed_tier,
                latency_ms=generation.latency_ms,
                usage=generation.usage,
                status="completed",
                normalized_error=None,
            )
        )
        return {
            "body": generation.body,
            "provider_run_ref": generation.provider_run_ref,
            "provider_session_ref": generation.provider_session_ref,
            "observed_model": generation.observed_model,
            "observed_tier": generation.observed_tier,
        }

    @staticmethod
    def _render_prompt(report_date: str, source_refs: list[dict[str, object]]) -> str:
        evidence = "\n".join(
            f"- task {item['task_id']} v{item['task_version']}: {item['state']} at {item['occurred_at']}"
            for item in source_refs
        ) or "- No authorized work activity was recorded."
        return (
            "Write a concise personal daily work report in Korean. "
            f"Report date: {report_date}. Use only this authorized evidence:\n{evidence}"
        )
