"""Workflow execution application service and persistence/tool ports."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.ax_execution.domain import (
    NodeKind,
    WorkflowDefinitionVersion,
    WorkflowNode,
)
from ax_workspace.modules.organization_access.domain import Principal, SEED_PERSONAS
from ax_workspace.modules.work.domain import my_work_item


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


class AccessDenied(RuntimeErrorBase):
    pass


class RunNotFound(RuntimeErrorBase):
    pass


class InvalidWorkflowInput(RuntimeErrorBase):
    pass


class InvalidDecision(RuntimeErrorBase):
    pass


class WorkflowRepository(Protocol):
    def definition_for_workflow(self, workflow_id: str) -> Any: ...
    def definition_for_run(self, run: Any) -> Any: ...
    def run(self, run_id: UUID) -> Any: ...
    def locked_run(self, run_id: UUID) -> Any: ...
    def create_run(
        self, definition: Any, initiator_id: str, input_snapshot: dict[str, Any]
    ) -> Any: ...
    def node_executions(self, run_id: UUID) -> list[Any]: ...
    def node_execution(self, run_id: UUID, node_id: str) -> Any | None: ...
    def create_node_execution(
        self, run_id: UUID, node_id: str, state: NodeState
    ) -> Any: ...
    def append_audit(
        self, run_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> None: ...
    def record_tool_result(
        self, node_execution_id: UUID, tool_name: str, result: dict[str, Any]
    ) -> None: ...
    def record_decision(
        self,
        node_execution_id: UUID,
        principal_id: str,
        decision: str,
        rationale: str | None,
        payload: dict[str, Any],
    ) -> None: ...
    def assignment_for_run(self, run_id: UUID) -> Any | None: ...
    def events_for_run(self, run_id: UUID) -> list[Any]: ...
    def tool_results_for_run(self, run_id: UUID) -> list[Any]: ...
    def waiting_executions(self) -> Any: ...
    def active_assignments_for(self, principal_id: str) -> list[Any]: ...


class ToolDispatcher(Protocol):
    def dispatch(
        self, run: Any, node: WorkflowNode, execution: Any
    ) -> tuple[str, dict[str, Any]]: ...


def _now() -> datetime:
    return datetime.now(UTC)


class WorkflowRunStarter:
    """Public use case; all adapters call this with ports wired by bootstrap."""

    def __init__(
        self, repository: WorkflowRepository, dispatcher: ToolDispatcher
    ) -> None:
        self.repository, self.dispatcher = repository, dispatcher

    def start(
        self, workflow_id: str, principal: Principal, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        record = self.repository.definition_for_workflow(workflow_id)
        if record is None:
            raise RunNotFound(f"Workflow {workflow_id} was not found")
        definition = WorkflowDefinitionVersion.model_validate(record.definition)
        if not definition.is_visible_to(principal):
            raise AccessDenied("Principal cannot start this workflow")
        self._validate_input(definition, input_data)
        run = self.repository.create_run(record, str(principal.id), input_data)
        self.repository.append_audit(
            run.id,
            "workflow_run.started",
            {
                "workflow_id": workflow_id,
                "definition_version": definition.version,
                "initiator_id": str(principal.id),
            },
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
        definition = WorkflowDefinitionVersion.model_validate(
            self.repository.definition_for_run(run).definition
        )
        node = next((item for item in definition.nodes if item.id == node_id), None)
        if node is None or node.kind is not NodeKind.HUMAN_GATE:
            raise InvalidDecision("node is not a human decision gate")
        execution = self.repository.node_execution(run.id, node_id)
        if execution is None or execution.state != NodeState.WAITING_FOR_DECISION:
            raise InvalidDecision("node is not waiting for a decision")
        if not self._can_decide(run, node, principal):
            raise AccessDenied("Principal cannot decide this inbox item")
        self.repository.record_decision(
            execution.id, str(principal.id), decision, rationale, payload
        )
        execution.result, execution.completed_at = (
            {
                "decision": decision,
                "principal_id": str(principal.id),
                "payload": payload,
            },
            _now(),
        )
        if decision == "reject":
            execution.state, run.state, run.updated_at = (
                NodeState.REJECTED,
                RunState.REJECTED,
                _now(),
            )
            if (
                run.workflow_id == "meeting-followups"
                and node_id == "accept-assignment"
            ):
                assignment = self.repository.assignment_for_run(run.id)
                assert assignment is not None
                assignment.state = "rejected"
            self.repository.append_audit(
                run.id,
                "human_decision.rejected",
                {"node_id": node_id, "principal_id": str(principal.id)},
            )
            self.repository.append_audit(
                run.id, "workflow_run.rejected", {"node_id": node_id}
            )
            return self.summary(run.id)
        execution.state = NodeState.SUCCEEDED
        self.repository.append_audit(
            run.id,
            "human_decision.accepted",
            {"node_id": node_id, "principal_id": str(principal.id)},
        )
        self._advance(run, definition)
        return self.summary(run.id)

    def summary(self, run_id: UUID) -> dict[str, Any]:
        run = self.repository.run(run_id)
        definition = WorkflowDefinitionVersion.model_validate(
            self.repository.definition_for_run(run).definition
        )
        executions = self.repository.node_executions(run.id)
        return {
            "run_id": str(run.id),
            "workflow_id": run.workflow_id,
            "definition_version": definition.version,
            "state": run.state,
            "waiting_on": [
                item.node_id
                for item in executions
                if item.state == NodeState.WAITING_FOR_DECISION
            ],
            "tool_results": [
                {"tool_name": item.tool_name, "result": item.result}
                for item in self.repository.tool_results_for_run(run.id)
            ],
            "audit": [
                {"event_type": item.event_type, "payload": item.payload}
                for item in self.repository.events_for_run(run.id)
            ],
        }

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        result = []
        for run, execution in self.repository.waiting_executions():
            definition = WorkflowDefinitionVersion.model_validate(
                self.repository.definition_for_run(run).definition
            )
            node = next(
                item for item in definition.nodes if item.id == execution.node_id
            )
            if self._can_decide(run, node, principal):
                result.append(
                    {
                        "run_id": str(run.id),
                        "workflow_id": run.workflow_id,
                        "node_id": node.id,
                        "label": node.label,
                    }
                )
        return result

    def can_view(self, run_id: UUID, principal: Principal) -> bool:
        run = self.repository.run(run_id)
        if run.initiator_id == str(principal.id):
            return True
        definition = WorkflowDefinitionVersion.model_validate(
            self.repository.definition_for_run(run).definition
        )
        if definition.is_visible_to(principal):
            return True
        for node in definition.nodes:
            if (
                node.kind is NodeKind.HUMAN_GATE
                and node.required_capability in principal.capabilities
            ):
                assignment = self.repository.assignment_for_run(run.id)
                if node.id != "accept-assignment" or (
                    assignment is not None
                    and assignment.assignee_id == str(principal.id)
                ):
                    return True
        return False

    def my_work(self, principal: Principal) -> list[dict[str, Any]]:
        return [
            my_work_item(item)
            for item in self.repository.active_assignments_for(str(principal.id))
        ]

    def meeting_assignment_candidates(
        self, principal: Principal
    ) -> list[dict[str, str]]:
        record = self.repository.definition_for_workflow("meeting-followups")
        assert record is not None
        if not WorkflowDefinitionVersion.model_validate(
            record.definition
        ).is_visible_to(principal):
            raise AccessDenied("Principal cannot request a meeting assignment")
        return [
            {"id": str(item.id), "display_name": item.display_name}
            for item in SEED_PERSONAS.values()
            if "task.accept" in item.capabilities
        ]

    def _advance(self, run: Any, definition: WorkflowDefinitionVersion) -> None:
        while True:
            existing, changed = (
                {
                    item.node_id: item
                    for item in self.repository.node_executions(run.id)
                },
                False,
            )
            for node in definition.nodes:
                predecessors = [
                    edge.source for edge in definition.edges if edge.target == node.id
                ]
                if node.id in existing or (
                    node.kind is not NodeKind.START
                    and (
                        not predecessors
                        or not all(
                            existing.get(item)
                            and existing[item].state == NodeState.SUCCEEDED
                            for item in predecessors
                        )
                    )
                ):
                    continue
                execution = self.repository.create_node_execution(
                    run.id, node.id, NodeState.RUNNING
                )
                self.repository.append_audit(
                    run.id,
                    "workflow_node.started",
                    {"node_id": node.id, "node_kind": node.kind},
                )
                if node.kind is NodeKind.HUMAN_GATE:
                    execution.state, run.state, run.updated_at = (
                        NodeState.WAITING_FOR_DECISION,
                        RunState.WAITING_FOR_DECISION,
                        _now(),
                    )
                    self.repository.append_audit(
                        run.id, "human_decision.requested", {"node_id": node.id}
                    )
                elif node.kind is NodeKind.TOOL:
                    name, result = self.dispatcher.dispatch(run, node, execution)
                    self.repository.record_tool_result(execution.id, name, result)
                    self._succeed(run, execution, {"tool_name": name, "result": result})
                else:
                    self._succeed(run, execution, {})
                    if node.kind is NodeKind.END:
                        run.state, run.updated_at = RunState.COMPLETED, _now()
                        self.repository.append_audit(
                            run.id, "workflow_run.completed", {}
                        )
                changed = True
            if not changed:
                return

    def _succeed(self, run: Any, execution: Any, result: dict[str, Any]) -> None:
        execution.state, execution.result, execution.completed_at = (
            NodeState.SUCCEEDED,
            result,
            _now(),
        )
        self.repository.append_audit(
            run.id, "workflow_node.completed", {"node_id": execution.node_id}
        )

    def _can_decide(self, run: Any, node: WorkflowNode, principal: Principal) -> bool:
        if (
            node.required_capability
            and node.required_capability not in principal.capabilities
        ):
            return False
        if node.id == "accept-assignment":
            assignment = self.repository.assignment_for_run(run.id)
            return assignment is not None and assignment.assignee_id == str(
                principal.id
            )
        return node.required_capability is not None or run.initiator_id == str(
            principal.id
        )

    @staticmethod
    def _validate_input(
        definition: WorkflowDefinitionVersion, input_data: dict[str, Any]
    ) -> None:
        missing = [
            name
            for name in definition.input_schema.get("required", [])
            if name not in input_data
        ]
        if missing:
            raise InvalidWorkflowInput(
                f"input is missing required fields: {', '.join(missing)}"
            )
        if definition.input_schema.get("additionalProperties") is False and input_data:
            raise InvalidWorkflowInput("input does not allow additional properties")
