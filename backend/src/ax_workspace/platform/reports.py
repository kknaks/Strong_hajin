from __future__ import annotations

from ax_workspace.modules.reports.results import ReportHistoryResult, ReportStatusResult, ReportRecentView

from ax_workspace.modules.reports.application import DailyReportNotFound

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ax_workspace.modules.ax_execution.ai import (
    AiGenerationRequest,
    AiProvider,
    ProviderFailure,
)
from ax_workspace.modules.reports.workflow_metadata import ALLOWED_OPERATIONS, DEFAULT_OUTPUTS, validate_definition
from ax_workspace.platform.persistence import (
    DailyReportSubmissionRecord,
    DailyReportRecord,
    DailyReportGenerationAttemptRecord,
    DailyReportGenerationRecord,
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

    def request_generation(
        self,
        owner_id: str,
        report_date: str,
        causation_key: str | None,
    ) -> DailyReportGenerationRecord:
        if causation_key:
            existing = self._session.scalar(
                select(DailyReportGenerationRecord).where(
                    DailyReportGenerationRecord.owner_id == owner_id,
                    DailyReportGenerationRecord.causation_key == causation_key,
                )
            )
            if existing is not None:
                return existing
        active = self._session.scalar(
            select(DailyReportGenerationRecord)
            .where(
                DailyReportGenerationRecord.owner_id == owner_id,
                DailyReportGenerationRecord.report_date == report_date,
                DailyReportGenerationRecord.state.in_(("queued", "running", "needs_verification")),
            )
            .order_by(DailyReportGenerationRecord.created_at.desc())
            .limit(1)
        )
        if active is not None:
            return active
        failed = self._session.scalar(
            select(DailyReportGenerationRecord)
            .where(
                DailyReportGenerationRecord.owner_id == owner_id,
                DailyReportGenerationRecord.report_date == report_date,
                DailyReportGenerationRecord.state == "failed",
            )
            .order_by(DailyReportGenerationRecord.created_at.desc())
            .limit(1)
        )
        now = datetime.now(UTC)
        generation = DailyReportGenerationRecord(
            id=uuid4(),
            owner_id=owner_id,
            report_date=report_date,
            causation_key=causation_key,
            retry_of_generation_id=failed.id if failed is not None else None,
            state="queued",
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(generation)
                self._session.flush()
            return generation
        except IntegrityError:
            statement = select(DailyReportGenerationRecord).where(
                DailyReportGenerationRecord.owner_id == owner_id,
            )
            if causation_key:
                same = self._session.scalar(statement.where(
                    DailyReportGenerationRecord.causation_key == causation_key
                ))
                if same is not None:
                    return same
            winner = self._session.scalar(
                statement.where(
                    DailyReportGenerationRecord.report_date == report_date,
                    DailyReportGenerationRecord.state.in_(("queued", "running", "needs_verification")),
                ).order_by(DailyReportGenerationRecord.created_at.desc()).limit(1)
            )
            if winner is None:
                raise
            return winner

    def generation(self, generation_id: UUID, *, lock: bool = False) -> DailyReportGenerationRecord | None:
        statement = select(DailyReportGenerationRecord).where(DailyReportGenerationRecord.id == generation_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def _generation_attempt(
        self, generation: DailyReportGenerationRecord
    ) -> DailyReportGenerationAttemptRecord | None:
        return self._session.scalar(
            select(DailyReportGenerationAttemptRecord).where(
                DailyReportGenerationAttemptRecord.generation_id == generation.id,
                DailyReportGenerationAttemptRecord.attempt_number == generation.attempt_count,
            )
        )

    def generation_started_external_call(
        self, generation: DailyReportGenerationRecord
    ) -> bool:
        """이 실행이 외부 provider 를 이미 불렀을 수 있는가.

        `run()` 은 외부 호출 직전에 노드를 `running` 으로 커밋한다. 끝나지 않은 호출 노드가 남아
        있다는 것은 결과를 아무도 모른다는 뜻이고, 그때 같은 프롬프트를 다시 보내면 중복 실행이 된다.
        노드 execution 이 아예 없으면 호출에 닿기 전에 끝난 것이므로 평범한 재시도로 남는다.
        """
        if generation.workflow_run_id is None:
            return False
        run = self._session.get(WorkflowRunRecord, generation.workflow_run_id)
        if run is None:
            return False
        definition = self._session.get(WorkflowDefinitionVersionRecord, run.definition_version_id)
        if definition is None:
            return False
        external = [
            str(node["id"])
            for node in definition.definition.get("nodes", [])
            if node.get("type") == "llm.generate"
        ]
        if not external:
            return False
        return self._session.scalar(
            select(WorkflowNodeExecutionRecord.id)
            .where(
                WorkflowNodeExecutionRecord.run_id == run.id,
                WorkflowNodeExecutionRecord.node_id.in_(external),
                WorkflowNodeExecutionRecord.state != "completed",
            )
            .limit(1)
        ) is not None

    def claim_generation(
        self,
        generation: DailyReportGenerationRecord,
        *,
        attempt: int,
        lease_token: UUID,
        stale_after_seconds: int,
    ) -> str:
        now = datetime.now(UTC)
        if generation.state == "running":
            heartbeat = generation.heartbeat_at or generation.started_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            if heartbeat is not None and now - heartbeat < timedelta(seconds=stale_after_seconds):
                return "contended"
            previous = self._generation_attempt(generation)
            if previous is not None and previous.state == "running":
                # 이 실행이 provider 를 이미 불렀을 수 있다면, 시간이 지났다는 사실만으로
                # 외부 미실행을 단정하지 않는다. 다시 부르는 대신 확인 필요로 고정한다.
                if self.generation_started_external_call(generation):
                    self.mark_generation_needs_verification(generation, "provider_result_uncertain")
                    return "needs_verification"
                previous.state = "abandoned"
                previous.failure_reason = "lease_expired"
                previous.completed_at = now
        if generation.state != "queued" and generation.state != "running":
            return "terminal"
        generation.state = "running"
        generation.attempt_count = attempt
        generation.lease_token = lease_token
        generation.stage = "workflow"
        generation.stage_started_at = now
        generation.started_at = generation.started_at or now
        generation.heartbeat_at = now
        generation.error_code = None
        generation.updated_at = now
        self._session.add(
            DailyReportGenerationAttemptRecord(
                id=uuid4(),
                generation_id=generation.id,
                attempt_number=attempt,
                actor_id=generation.owner_id,
                owner_token=lease_token,
                stage="workflow",
                state="running",
                started_at=now,
                heartbeat_at=now,
            )
        )
        self._session.flush()
        return "claimed"

    def heartbeat_generation(
        self,
        generation: DailyReportGenerationRecord,
        *,
        stage_timeout_seconds: int,
        total_timeout_seconds: int,
    ) -> bool:
        now = datetime.now(UTC)
        stage_started = generation.stage_started_at
        started = generation.started_at
        if stage_started is not None and stage_started.tzinfo is None:
            stage_started = stage_started.replace(tzinfo=UTC)
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if (
            stage_started is not None
            and now - stage_started >= timedelta(seconds=stage_timeout_seconds)
        ) or (started is not None and now - started >= timedelta(seconds=total_timeout_seconds)):
            self.mark_generation_needs_verification(generation, "provider_time_limit_uncertain")
            return False
        generation.heartbeat_at = now
        generation.updated_at = now
        attempt = self._generation_attempt(generation)
        if attempt is not None and attempt.state == "running":
            attempt.heartbeat_at = now
        self._session.flush()
        return True

    def _finish_generation_attempt(
        self, generation: DailyReportGenerationRecord, state: str, reason: str | None = None
    ) -> None:
        attempt = self._generation_attempt(generation)
        if attempt is not None and attempt.state == "running":
            now = datetime.now(UTC)
            attempt.state = state
            attempt.failure_reason = reason
            attempt.heartbeat_at = now
            attempt.completed_at = now

    def complete_generation(
        self, generation: DailyReportGenerationRecord, draft: ReportDraftRecord
    ) -> None:
        self._finish_generation_attempt(generation, "completed")
        now = datetime.now(UTC)
        generation.state = "completed"
        generation.report_id = draft.report_id
        generation.draft_id = draft.id
        generation.error_code = None
        generation.lease_token = None
        generation.heartbeat_at = None
        generation.completed_at = now
        generation.updated_at = now
        self._session.flush()

    def record_generation_workflow_run(
        self, generation: DailyReportGenerationRecord, workflow_run_id: UUID
    ) -> None:
        if generation.workflow_run_id not in {None, workflow_run_id}:
            raise ValueError("daily report generation workflow run differs")
        generation.workflow_run_id = workflow_run_id
        generation.updated_at = datetime.now(UTC)
        self._session.flush()

    def retry_generation(self, generation: DailyReportGenerationRecord, code: str) -> None:
        self._finish_generation_attempt(generation, "retry", code)
        generation.state = "queued"
        generation.error_code = code[:80]
        generation.lease_token = None
        generation.heartbeat_at = None
        generation.updated_at = datetime.now(UTC)
        self._session.flush()

    def fail_generation(self, generation: DailyReportGenerationRecord, code: str) -> None:
        self._finish_generation_attempt(generation, "failed", code)
        now = datetime.now(UTC)
        generation.state = "failed"
        generation.error_code = code[:80]
        generation.lease_token = None
        generation.heartbeat_at = None
        generation.completed_at = now
        generation.updated_at = now
        self._session.flush()

    def mark_generation_needs_verification(
        self, generation: DailyReportGenerationRecord, code: str
    ) -> None:
        self._finish_generation_attempt(generation, "needs_verification", code)
        now = datetime.now(UTC)
        generation.state = "needs_verification"
        generation.error_code = code[:80]
        generation.lease_token = None
        generation.heartbeat_at = None
        generation.completed_at = now
        generation.updated_at = now
        self._session.flush()

    @contextmanager
    def generation_causation(self, owner_id: str, causation_key: str):
        """Hold a report causation guard outside the report transaction.

        The connection is session-scoped and AUTOCOMMIT so the provider call
        cannot be killed by an idle-in-transaction timeout. If that connection
        fails PostgreSQL releases the guard; the unique report-draft causation
        key still makes the business result idempotent, while the provider call
        itself remains intentionally at-least-once around connection loss.
        """
        if self._session.bind is None or self._session.bind.dialect.name != "postgresql":
            yield
            return
        digest = hashlib.blake2b(
            f"daily-report:{owner_id}:{causation_key}".encode(), digest_size=8
        ).digest()
        lock_key = int.from_bytes(digest, byteorder="big", signed=True)
        with self._session.bind.execution_options(isolation_level="AUTOCOMMIT").connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
            try:
                yield
            finally:
                if not connection.closed:
                    try:
                        connection.execute(
                            text("SELECT pg_advisory_unlock(:lock_key)"),
                            {"lock_key": lock_key},
                        )
                    except DBAPIError:
                        pass

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

    def history(self, owner_id: str, report_id: str) -> ReportHistoryResult:
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
                    "workflow_run_id": str(draft.workflow_run_id),
                    "definition_version_id": str(draft.definition_version_id),
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

    def material_revisions(self, owner_id: str, *, report_id: str | None = None, include_history: bool = False) -> list[dict[str, Any]]:
        if report_id is not None:
            self._owned_report(owner_id, report_id)
        latest = select(func.max(DailyReportSubmissionRecord.submission_version)).where(
            DailyReportSubmissionRecord.report_id == DailyReportRecord.id).correlate(DailyReportRecord).scalar_subquery()
        statement = select(DailyReportRecord.id, DailyReportRecord.report_date, DailyReportSubmissionRecord.id,
            DailyReportSubmissionRecord.submission_version, (DailyReportSubmissionRecord.submission_version == latest).label("is_current")).join(
            DailyReportSubmissionRecord, DailyReportSubmissionRecord.report_id == DailyReportRecord.id).where(DailyReportRecord.owner_id == owner_id)
        if report_id is not None:
            statement = statement.where(DailyReportRecord.id == UUID(report_id))
        if not include_history:
            statement = statement.where(DailyReportSubmissionRecord.submission_version == latest)
        statement = statement.order_by(DailyReportRecord.report_date.desc(), DailyReportSubmissionRecord.submission_version.desc())
        return [{"report_id": str(report), "report_date": report_date, "revision_id": str(submission),
                 "revision": version, "is_current_revision": current} for report, report_date, submission, version, current in self._session.execute(statement)]

    def recent(self, owner_id: str, *, limit: int = 3) -> list[ReportRecentView]:
        """The last few reports this person wrote, each with the work its newest draft was written from."""
        reports = list(
            self._session.scalars(
                select(DailyReportRecord)
                .where(DailyReportRecord.owner_id == owner_id)
                .order_by(DailyReportRecord.report_date.desc())
                .limit(max(1, limit))
            )
        )
        answer: list[dict[str, Any]] = []
        for report in reports:
            draft = self._session.scalar(
                select(ReportDraftRecord)
                .where(ReportDraftRecord.report_id == report.id)
                .order_by(ReportDraftRecord.version.desc())
                .limit(1)
            )
            answer.append(
                {
                    "report_id": str(report.id),
                    "report_date": report.report_date,
                    "status": report.status,
                    "source_refs": list(draft.source_refs or []) if draft is not None else [],
                }
            )
        return answer

    def status_for_date(self, owner_id: str, report_date: str) -> ReportStatusResult:
        report = self._session.scalar(
            select(DailyReportRecord).where(
                DailyReportRecord.owner_id == owner_id,
                DailyReportRecord.report_date == report_date,
            )
        )
        generation = self._session.scalar(
            select(DailyReportGenerationRecord)
            .where(
                DailyReportGenerationRecord.owner_id == owner_id,
                DailyReportGenerationRecord.report_date == report_date,
            )
            .order_by(DailyReportGenerationRecord.created_at.desc())
            .limit(1)
        )
        return {
            "report_date": report_date,
            "status": report.status if report is not None else "not_started",
            "report_id": str(report.id) if report is not None else None,
            "generation_id": str(generation.id) if generation is not None else None,
            "generation_status": generation.state if generation is not None else None,
            "generation_error_code": generation.error_code if generation is not None else None,
        }

    def _owned_report(self, owner_id: str, report_id: str, *, lock: bool = False) -> DailyReportRecord:
        statement = select(DailyReportRecord).where(
            DailyReportRecord.id == UUID(report_id), DailyReportRecord.owner_id == owner_id
        )
        report = self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)
        if report is None:
            raise DailyReportNotFound("daily report was not found")
        return report

    def _owned_draft(self, report_id: UUID, draft_id: str, *, lock: bool = False) -> ReportDraftRecord:
        statement = select(ReportDraftRecord).where(
            ReportDraftRecord.id == UUID(draft_id), ReportDraftRecord.report_id == report_id
        )
        draft = self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)
        if draft is None:
            raise DailyReportNotFound("daily report draft was not found")
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

    def run(
        self,
        principal: Any,
        report_date: str,
        *,
        run_id: UUID | None = None,
        on_run_created: Callable[[UUID], None] | None = None,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        if run_id is None:
            # The first attempt pins the currently published definition for every retry of this generation.
            definition = self._session.scalar(
                select(WorkflowDefinitionVersionRecord)
                .where(
                    WorkflowDefinitionVersionRecord.workflow_id == "daily-report-generation",
                    WorkflowDefinitionVersionRecord.status == "published",
                )
                .order_by(WorkflowDefinitionVersionRecord.published_at.desc(), WorkflowDefinitionVersionRecord.created_at.desc())
            )
            if definition is None:
                raise RuntimeError("no published daily-report-generation workflow is installed")
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
            if on_run_created is not None:
                on_run_created(run.id)
        else:
            run = self._session.get(WorkflowRunRecord, run_id)
            if (
                run is None
                or run.initiator_id != str(principal.id)
                or run.input_snapshot.get("report_date") != report_date
            ):
                raise ValueError("daily report workflow retry input differs")
            definition = self._session.get(WorkflowDefinitionVersionRecord, run.definition_version_id)
            if definition is None:
                raise RuntimeError("daily report workflow definition was not found")
            completed = self._session.scalars(
                select(WorkflowNodeExecutionRecord).where(
                    WorkflowNodeExecutionRecord.run_id == run.id,
                    WorkflowNodeExecutionRecord.state == "completed",
                )
            ).all()
            results = {row.node_id: dict(row.result or {}) for row in completed}
            run.state = "running"
            run.updated_at = datetime.now(UTC)
        validate_definition(definition.definition)

        try:
            for node in definition.definition["nodes"]:
                if node["id"] in results:
                    continue
                execution = self._start_node(run.id, node, results)
                if node["type"] == "llm.generate":
                    # Persist the run and preceding node provenance before the
                    # external call, then release this session's transaction.
                    # The causation guard remains on its separate connection.
                    self._session.commit()
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

        outputs = definition.definition.get("outputs", DEFAULT_OUTPUTS)
        return {
            "run_id": str(run.id),
            "definition_version_id": str(definition.id),
            "state": run.state,
            # Which node holds the answer is the definition's decision, not a name written into the runtime.
            **{name: self._resolve_output(results, reference) for name, reference in outputs.items()},
        }

    @staticmethod
    def _resolve_output(results: dict[str, Any], reference: str) -> Any:
        node_id, _, field = str(reference).partition(".")
        produced = results.get(node_id)
        if produced is None or field not in produced:
            raise ValueError(f"workflow output {reference} was not produced")
        return produced[field]

    def _start_node(
        self,
        run_id: UUID,
        node: dict[str, Any],
        results: dict[str, Any],
    ) -> WorkflowNodeExecutionRecord:
        execution = self._session.scalar(
            select(WorkflowNodeExecutionRecord).where(
                WorkflowNodeExecutionRecord.run_id == run_id,
                WorkflowNodeExecutionRecord.node_id == node["id"],
            )
        )
        if execution is not None:
            execution.state = "running"
            execution.input_snapshot = {input_id: results[input_id] for input_id in node.get("inputs", [])}
            execution.result = None
            execution.normalized_error = None
            execution.retry_count += 1
            execution.completed_at = None
            self._session.flush()
            return execution
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
        # A node reads what its declared inputs produced. The runtime knows the building blocks; the definition
        # knows the wiring, so renaming or reordering nodes is a change to the data and not to this code.
        inputs = [results[input_id] for input_id in node.get("inputs", [])]
        if node_type == "operation.query":
            if node.get("operation") not in ALLOWED_OPERATIONS:
                raise ValueError("workflow metadata references an unregistered operation")
            return {"source_refs": self._sources.list(principal, report_date)}
        if node_type == "template.render":
            source_refs = self._require_field(inputs, "source_refs", node)
            return {"rendered_prompt": self._render_prompt(report_date, source_refs)}
        if node_type == "llm.generate":
            return self._generate(execution, self._require_field(inputs, "rendered_prompt", node))
        if node_type == "output.validate":
            body = str(self._require_field(inputs, "body", node)).strip()
            if not body:
                raise ValueError("daily report provider returned an empty draft")
            return {"body": body}
        raise ValueError(f"unregistered node type: {node_type}")

    @staticmethod
    def _require_field(inputs: list[dict[str, Any]], field: str, node: dict[str, Any]) -> Any:
        """What this node needs, from whichever declared input actually produced it."""
        for produced in inputs:
            if field in produced:
                return produced[field]
        raise ValueError(f"workflow node {node['id']} has no input producing {field}")

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
