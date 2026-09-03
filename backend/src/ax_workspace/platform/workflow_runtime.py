from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ax_workspace.modules.organization_access.domain import PersonaId, Principal, SEED_PERSONAS
from ax_workspace.modules.meetings.domain import pending_assignment_result
from ax_workspace.modules.reports.domain import confirmed_submission_snapshot
from ax_workspace.modules.work.domain import my_work_item
from ax_workspace.platform.persistence import (
    AuditEventRecord,
    ContractApprovalRecord,
    DailyReportSubmissionRecord,
    HumanDecisionRecord,
    MeetingEvidenceRecord,
    TaskAssignmentRecord,
    ToolExecutionRecord,
    WorkRecord,
    WorkflowDefinitionVersionRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
)
from ax_workspace.modules.ax_execution.domain import NodeKind, WorkflowDefinitionVersion, WorkflowNode
from ax_workspace.modules.ax_execution.application import (
    AccessDenied,
    InvalidDecision,
    InvalidWorkflowInput,
    RunNotFound,
    WorkflowRunStarter as ApplicationWorkflowRunStarter,
)


class RunState(StrEnum):
    RUNNING = "running"
    WAITING_FOR_DECISION = "waiting_for_decision"
    COMPLETED = "completed"
    REJECTED = "rejected"


class NodeState(StrEnum):
    RUNNING = "running"
    WAITING_FOR_DECISION = "waiting_for_decision"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"


class RuntimeErrorBase(Exception):
    pass


class LegacyAccessDenied(RuntimeErrorBase):
    pass


class LegacyRunNotFound(RuntimeErrorBase):
    pass


class LegacyInvalidWorkflowInput(RuntimeErrorBase):
    pass


class LegacyInvalidDecision(RuntimeErrorBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class SqlAlchemyWorkflowRepository:
    """Persistence adapter; all state mutation passes through a unit of work."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def definition_for_workflow(self, workflow_id: str) -> WorkflowDefinitionVersionRecord | None:
        return self.session.scalar(
            select(WorkflowDefinitionVersionRecord)
            .where(WorkflowDefinitionVersionRecord.workflow_id == workflow_id)
            .order_by(WorkflowDefinitionVersionRecord.created_at.desc())
        )

    def definition_for_run(self, run: WorkflowRunRecord) -> WorkflowDefinitionVersionRecord:
        definition = self.session.get(WorkflowDefinitionVersionRecord, run.definition_version_id)
        if definition is None:
            raise RunNotFound(f"Definition version for run {run.id} no longer exists")
        return definition

    def run(self, run_id: UUID) -> WorkflowRunRecord:
        run = self.session.get(WorkflowRunRecord, run_id)
        if run is None:
            raise RunNotFound(f"Workflow run {run_id} was not found")
        return run

    def locked_run(self, run_id: UUID) -> WorkflowRunRecord:
        """Serialize a decision transition for one run on database backends that support row locks."""
        run = self.session.scalar(
            select(WorkflowRunRecord).where(WorkflowRunRecord.id == run_id).with_for_update()
        )
        if run is None:
            raise RunNotFound(f"Workflow run {run_id} was not found")
        return run

    def create_run(self, definition: WorkflowDefinitionVersionRecord, initiator_id: str, input_snapshot: dict[str, Any]) -> WorkflowRunRecord:
        now = _now()
        run = WorkflowRunRecord(
            definition_version_id=definition.id,
            workflow_id=definition.workflow_id,
            initiator_id=initiator_id,
            state=RunState.RUNNING,
            input_snapshot=input_snapshot,
            created_at=now,
            updated_at=now,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def node_executions(self, run_id: UUID) -> list[WorkflowNodeExecutionRecord]:
        return list(
            self.session.scalars(
                select(WorkflowNodeExecutionRecord)
                .where(WorkflowNodeExecutionRecord.run_id == run_id)
                .order_by(WorkflowNodeExecutionRecord.created_at)
            )
        )

    def node_execution(self, run_id: UUID, node_id: str) -> WorkflowNodeExecutionRecord | None:
        return self.session.scalar(
            select(WorkflowNodeExecutionRecord).where(
                WorkflowNodeExecutionRecord.run_id == run_id,
                WorkflowNodeExecutionRecord.node_id == node_id,
            )
        )

    def create_node_execution(self, run_id: UUID, node_id: str, state: NodeState) -> WorkflowNodeExecutionRecord:
        execution = WorkflowNodeExecutionRecord(
            run_id=run_id,
            node_id=node_id,
            state=state,
            result=None,
            created_at=_now(),
            completed_at=None,
        )
        self.session.add(execution)
        self.session.flush()
        return execution

    def append_audit(self, run_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.session.add(
            AuditEventRecord(
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                occurred_at=_now(),
            )
        )

    def record_tool_result(
        self, node_execution_id: UUID, tool_name: str, result: dict[str, Any]
    ) -> None:
        self.session.add(
            ToolExecutionRecord(
                node_execution_id=node_execution_id,
                tool_name=tool_name,
                result=result,
                executed_at=_now(),
            )
        )

    def record_decision(
        self,
        node_execution_id: UUID,
        principal_id: str,
        decision: str,
        rationale: str | None,
        payload: dict[str, Any],
    ) -> None:
        self.session.add(
            HumanDecisionRecord(
                node_execution_id=node_execution_id,
                principal_id=principal_id,
                decision=decision,
                rationale=rationale,
                payload=payload,
                decided_at=_now(),
            )
        )

    def decision_for_node(self, execution_id: UUID) -> HumanDecisionRecord | None:
        return self.session.scalar(
            select(HumanDecisionRecord)
            .where(HumanDecisionRecord.node_execution_id == execution_id)
            .order_by(HumanDecisionRecord.decided_at.desc())
        )

    def assignment_for_run(self, run_id: UUID) -> TaskAssignmentRecord | None:
        return self.session.scalar(select(TaskAssignmentRecord).where(TaskAssignmentRecord.run_id == run_id))

    def events_for_run(self, run_id: UUID) -> list[AuditEventRecord]:
        return list(
            self.session.scalars(
                select(AuditEventRecord)
                .where(AuditEventRecord.run_id == run_id)
                .order_by(AuditEventRecord.occurred_at, AuditEventRecord.id)
            )
        )

    def tool_results_for_run(self, run_id: UUID) -> list[ToolExecutionRecord]:
        return list(
            self.session.scalars(
                select(ToolExecutionRecord)
                .join(WorkflowNodeExecutionRecord)
                .where(WorkflowNodeExecutionRecord.run_id == run_id)
                .order_by(ToolExecutionRecord.executed_at)
            )
        )

    def waiting_executions(self) -> Iterator[tuple[WorkflowRunRecord, WorkflowNodeExecutionRecord]]:
        rows = self.session.execute(
            select(WorkflowRunRecord, WorkflowNodeExecutionRecord)
            .join(WorkflowNodeExecutionRecord)
            .where(WorkflowNodeExecutionRecord.state == NodeState.WAITING_FOR_DECISION)
            .order_by(WorkflowNodeExecutionRecord.created_at)
        )
        yield from rows

    def active_assignments_for(self, principal_id: str) -> list[TaskAssignmentRecord]:
        return list(
            self.session.scalars(
                select(TaskAssignmentRecord)
                .where(TaskAssignmentRecord.assignee_id == principal_id)
                .where(TaskAssignmentRecord.state == "active")
                .order_by(TaskAssignmentRecord.created_at)
            )
        )


class SqlAlchemyUnitOfWork(AbstractContextManager["SqlAlchemyUnitOfWork"]):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.workflows: SqlAlchemyWorkflowRepository | None = None

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self.workflows = SqlAlchemyWorkflowRepository(self.session)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        assert self.session is not None
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()
        self.session.close()


class LocalDemoToolDispatcher:
    """Tool boundary used by every demo workflow; it never simulates a UI-only result."""

    def __init__(self, repository: SqlAlchemyWorkflowRepository) -> None:
        self.repository = repository

    def dispatch(
        self, run: WorkflowRunRecord, node: WorkflowNode, execution: WorkflowNodeExecutionRecord
    ) -> tuple[str, dict[str, Any]]:
        if run.workflow_id == "daily-report" and node.id == "collect":
            records = list(
                self.repository.session.scalars(
                    select(WorkRecord).where(WorkRecord.owner_id == run.initiator_id)
                )
            )
            return "work_record.lookup", {
                "records": [{"id": str(record.id), "title": record.title, "status": record.status} for record in records]
            }
        if run.workflow_id == "daily-report" and node.id == "effect":
            snapshot = confirmed_submission_snapshot(run.input_snapshot)
            self.repository.session.add(
                DailyReportSubmissionRecord(
                    run_id=run.id,
                    submitter_id=run.initiator_id,
                    snapshot=snapshot,
                    submitted_at=_now(),
                )
            )
            return "daily_report.submit_snapshot", snapshot
        if run.workflow_id == "meeting-followups" and node.id == "collect":
            evidence = list(self.repository.session.scalars(select(MeetingEvidenceRecord)))
            return "meeting_evidence.lookup", {
                "evidence": [{"id": str(item.id), "title": item.title, "candidate_task": item.candidate_task} for item in evidence]
            }
        if run.workflow_id == "meeting-followups" and node.id == "create-assignment":
            choice = self.repository.node_execution(run.id, "choose-assignment")
            assert choice is not None
            decision = self.repository.decision_for_node(choice.id)
            assert decision is not None
            assignee_id = decision.payload.get("assignee_id", run.initiator_id)
            try:
                assignee = SEED_PERSONAS[PersonaId(assignee_id)]
            except (KeyError, ValueError) as error:
                raise InvalidDecision("assignee_id must be a seeded demo persona") from error
            if "task.accept" not in assignee.capabilities:
                raise InvalidDecision("assignee_id must have task.accept capability")
            evidence = self.repository.session.scalar(select(MeetingEvidenceRecord))
            assert evidence is not None
            assignment = TaskAssignmentRecord(
                run_id=run.id,
                assignee_id=str(assignee.id),
                title=evidence.candidate_task,
                state="pending_acceptance",
                created_at=_now(),
                accepted_at=None,
            )
            self.repository.session.add(assignment)
            self.repository.session.flush()
            return "task_assignment.request", pending_assignment_result(assignment.id, str(assignee.id), assignment.state)
        if run.workflow_id == "meeting-followups" and node.id == "effect":
            assignment = self.repository.assignment_for_run(run.id)
            assert assignment is not None
            assignment.state = "active"
            assignment.accepted_at = _now()
            return "task_assignment.activate", {"assignment_id": str(assignment.id), "state": assignment.state}
        if run.workflow_id == "contract-review" and node.id == "extract":
            return "contract.extract", {"contract_id": run.input_snapshot["contract_id"], "source": "local_demo_adapter"}
        if run.workflow_id == "contract-review" and node.id == "effect":
            approval = ContractApprovalRecord(
                run_id=run.id,
                contract_id=run.input_snapshot["contract_id"],
                state="approved",
                approved_at=_now(),
            )
            self.repository.session.add(approval)
            return "contract.approve", {"contract_id": approval.contract_id, "state": approval.state}
        return "local_demo_adapter.execute", {"workflow_id": run.workflow_id, "node_id": node.id, "mode": "local_demo_adapter"}


class WorkflowRunStarter:
    """Application operation shared by HTTP/UI/MCP callers for starting and advancing a run."""

    def __init__(self, repository: SqlAlchemyWorkflowRepository) -> None:
        self.repository = repository
        self.dispatcher = LocalDemoToolDispatcher(repository)

    def start(self, workflow_id: str, principal: Principal, input_data: dict[str, Any]) -> dict[str, Any]:
        definition_record = self.repository.definition_for_workflow(workflow_id)
        if definition_record is None:
            raise RunNotFound(f"Workflow {workflow_id} was not found")
        definition = WorkflowDefinitionVersion.model_validate(definition_record.definition)
        if not definition.is_visible_to(principal):
            raise AccessDenied("Principal cannot start this workflow")
        self._validate_input(definition, input_data)
        run = self.repository.create_run(definition_record, str(principal.id), input_data)
        self.repository.append_audit(
            run.id,
            "workflow_run.started",
            {"workflow_id": workflow_id, "definition_version": definition.version, "initiator_id": str(principal.id)},
        )
        self._advance(run, definition)
        return self.summary(run.id)

    def decide(
        self,
        run_id: UUID,
        node_id: str,
        principal: Principal,
        decision: str,
        rationale: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if decision not in {"accept", "reject"}:
            raise InvalidDecision("decision must be 'accept' or 'reject'")
        run = self.repository.locked_run(run_id)
        if run.state != RunState.WAITING_FOR_DECISION:
            raise InvalidDecision("run is not waiting for a decision")
        definition = WorkflowDefinitionVersion.model_validate(self.repository.definition_for_run(run).definition)
        node = next((candidate for candidate in definition.nodes if candidate.id == node_id), None)
        if node is None or node.kind is not NodeKind.HUMAN_GATE:
            raise InvalidDecision("node is not a human decision gate")
        execution = self.repository.node_execution(run.id, node_id)
        if execution is None or execution.state != NodeState.WAITING_FOR_DECISION:
            raise InvalidDecision("node is not waiting for a decision")
        if not self._can_decide(run, node, principal):
            raise AccessDenied("Principal cannot decide this inbox item")
        self.repository.record_decision(execution.id, str(principal.id), decision, rationale, payload)
        execution.result = {"decision": decision, "principal_id": str(principal.id), "payload": payload}
        execution.completed_at = _now()
        if decision == "reject":
            execution.state = NodeState.REJECTED
            if run.workflow_id == "meeting-followups" and node_id == "accept-assignment":
                assignment = self.repository.assignment_for_run(run.id)
                assert assignment is not None
                assignment.state = "rejected"
            run.state = RunState.REJECTED
            run.updated_at = _now()
            self.repository.append_audit(run.id, "human_decision.rejected", {"node_id": node_id, "principal_id": str(principal.id)})
            self.repository.append_audit(run.id, "workflow_run.rejected", {"node_id": node_id})
            return self.summary(run.id)
        execution.state = NodeState.SUCCEEDED
        self.repository.append_audit(run.id, "human_decision.accepted", {"node_id": node_id, "principal_id": str(principal.id)})
        self._advance(run, definition)
        return self.summary(run.id)

    def summary(self, run_id: UUID) -> dict[str, Any]:
        run = self.repository.run(run_id)
        definition = WorkflowDefinitionVersion.model_validate(self.repository.definition_for_run(run).definition)
        executions = self.repository.node_executions(run.id)
        waiting = [execution.node_id for execution in executions if execution.state == NodeState.WAITING_FOR_DECISION]
        return {
            "run_id": str(run.id),
            "workflow_id": run.workflow_id,
            "definition_version": definition.version,
            "state": run.state,
            "waiting_on": waiting,
            "tool_results": [
                {"tool_name": item.tool_name, "result": item.result} for item in self.repository.tool_results_for_run(run.id)
            ],
            "audit": [
                {"event_type": item.event_type, "payload": item.payload} for item in self.repository.events_for_run(run.id)
            ],
        }

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for run, execution in self.repository.waiting_executions():
            definition = WorkflowDefinitionVersion.model_validate(self.repository.definition_for_run(run).definition)
            node = next(candidate for candidate in definition.nodes if candidate.id == execution.node_id)
            if self._can_decide(run, node, principal):
                items.append({"run_id": str(run.id), "workflow_id": run.workflow_id, "node_id": node.id, "label": node.label})
        return items

    def can_view(self, run_id: UUID, principal: Principal) -> bool:
        """A starter or an assigned human gate participant may inspect the persisted run."""
        run = self.repository.run(run_id)
        if run.initiator_id == str(principal.id):
            return True
        definition = WorkflowDefinitionVersion.model_validate(self.repository.definition_for_run(run).definition)
        if definition.is_visible_to(principal):
            return True
        for node in definition.nodes:
            if node.kind is not NodeKind.HUMAN_GATE:
                continue
            if node.required_capability and node.required_capability in principal.capabilities:
                if node.id != "accept-assignment":
                    return True
                assignment = self.repository.assignment_for_run(run.id)
                if assignment is not None and assignment.assignee_id == str(principal.id):
                    return True
        return False

    def my_work(self, principal: Principal) -> list[dict[str, Any]]:
        return [my_work_item(item) for item in self.repository.active_assignments_for(str(principal.id))]

    def meeting_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Expose only real seed principals that can complete an assignment acceptance gate."""
        definition_record = self.repository.definition_for_workflow("meeting-followups")
        assert definition_record is not None
        definition = WorkflowDefinitionVersion.model_validate(definition_record.definition)
        if not definition.is_visible_to(principal):
            raise AccessDenied("Principal cannot request a meeting assignment")
        return [
            {"id": str(candidate.id), "display_name": candidate.display_name}
            for candidate in SEED_PERSONAS.values()
            if "task.accept" in candidate.capabilities
        ]

    def _advance(self, run: WorkflowRunRecord, definition: WorkflowDefinitionVersion) -> None:
        while True:
            existing = {item.node_id: item for item in self.repository.node_executions(run.id)}
            changed = False
            for node in definition.nodes:
                if node.id in existing:
                    continue
                predecessors = [edge.source for edge in definition.edges if edge.target == node.id]
                if node.kind is not NodeKind.START and not predecessors:
                    continue
                if node.kind is not NodeKind.START and not all(
                    existing.get(predecessor) and existing[predecessor].state == NodeState.SUCCEEDED
                    for predecessor in predecessors
                ):
                    continue
                execution = self.repository.create_node_execution(run.id, node.id, NodeState.RUNNING)
                self.repository.append_audit(run.id, "workflow_node.started", {"node_id": node.id, "node_kind": node.kind})
                if node.kind is NodeKind.HUMAN_GATE:
                    execution.state = NodeState.WAITING_FOR_DECISION
                    run.state = RunState.WAITING_FOR_DECISION
                    run.updated_at = _now()
                    self.repository.append_audit(run.id, "human_decision.requested", {"node_id": node.id})
                elif node.kind is NodeKind.TOOL:
                    tool_name, result = self.dispatcher.dispatch(run, node, execution)
                    self.repository.record_tool_result(execution.id, tool_name, result)
                    self._succeed_node(run, execution, {"tool_name": tool_name, "result": result})
                else:
                    self._succeed_node(run, execution, {})
                    if node.kind is NodeKind.END:
                        run.state = RunState.COMPLETED
                        run.updated_at = _now()
                        self.repository.append_audit(run.id, "workflow_run.completed", {})
                changed = True
            if not changed:
                break

    def _succeed_node(
        self, run: WorkflowRunRecord, execution: WorkflowNodeExecutionRecord, result: dict[str, Any]
    ) -> None:
        execution.state = NodeState.SUCCEEDED
        execution.result = result
        execution.completed_at = _now()
        self.repository.append_audit(run.id, "workflow_node.completed", {"node_id": execution.node_id})

    def _can_decide(self, run: WorkflowRunRecord, node: WorkflowNode, principal: Principal) -> bool:
        if node.required_capability and node.required_capability not in principal.capabilities:
            return False
        if node.id == "accept-assignment":
            assignment = self.repository.assignment_for_run(run.id)
            return assignment is not None and assignment.assignee_id == str(principal.id)
        return node.required_capability is not None or run.initiator_id == str(principal.id)

    @staticmethod
    def _validate_input(definition: WorkflowDefinitionVersion, input_data: dict[str, Any]) -> None:
        required = definition.input_schema.get("required", [])
        missing = [name for name in required if name not in input_data]
        if missing:
            raise InvalidWorkflowInput(f"input is missing required fields: {', '.join(missing)}")
        if definition.input_schema.get("additionalProperties") is False and input_data:
            raise InvalidWorkflowInput("input does not allow additional properties")


def workflow_service(repository: SqlAlchemyWorkflowRepository) -> ApplicationWorkflowRunStarter:
    """Compose the application use case with this platform's persistence and tool adapters."""
    return ApplicationWorkflowRunStarter(repository, LocalDemoToolDispatcher(repository))
