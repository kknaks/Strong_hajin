"""Persona-bound MCP adapter for SCAX public product operations.

The persona is process configuration, never a tool argument.  The server reads
the current principal from the same Organization & Access projection used by
HTTP and invokes local operations in-process.
"""
from __future__ import annotations

import os
import hashlib
import json
from typing import Any
from uuid import UUID

from mcp.server import MCPServer

from ax_workspace.bootstrap.application import WorkflowApplication, create_workflow_application
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.organization_access.domain import (
    DAILY_REPORT_EDIT,
    DAILY_REPORT_GENERATE,
    DAILY_REPORT_READ,
    DAILY_REPORT_SUBMIT,
    Principal,
    TASK_READ,
    TASK_SELF_MANAGE,
)
from ax_workspace.modules.ax_execution.ai import AiProvider


class McpReportsFacade:
    """MCP transport adapter for canonical Reports application operations."""

    def __init__(
        self,
        settings: Settings,
        persona_id: str,
        report_provider: AiProvider | None = None,
    ) -> None:
        if not settings.developer_auth_enabled:
            raise RuntimeError("MCP developer adapter is available only in development and test profiles")
        self._application: WorkflowApplication = create_workflow_application(settings, report_provider)
        self._principal: Principal = self._application.authenticated_principal(persona_id)

    @property
    def principal(self) -> Principal:
        return self._principal

    def generate_daily_report_draft(self, report_date: str) -> dict[str, Any]:
        return self._application.generate_daily_report_draft(
            self._principal,
            report_date,
            self._mutation_key("daily_report.generate_draft", {"report_date": report_date}),
        )

    def edit_daily_report(
        self,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]] | None = None,
        exclude_source_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "report_id": report_id,
            "draft_id": draft_id,
            "expected_version": expected_version,
            "body": body,
            "include_source_refs": include_source_refs or [],
            "exclude_source_refs": exclude_source_refs or [],
        }
        action = self._propose_chat_action("daily_report.edit", "일일보고 초안 수정 확인", payload)
        if action is not None:
            return action
        return self._application.edit_daily_report(
            self._principal,
            report_id,
            draft_id,
            expected_version,
            body,
            include_source_refs or [],
            exclude_source_refs or [],
        )

    def submit_daily_report(
        self,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        action = self._propose_chat_action(
            "daily_report.submit", "일일보고 제출 확인",
            {"report_id": report_id, "draft_id": draft_id, "expected_version": expected_version, "reason": reason},
        )
        if action is not None:
            return action
        return self._application.submit_daily_report(
            self._principal, report_id, draft_id, expected_version, reason
        )

    def daily_report_history(self, report_id: str) -> dict[str, Any]:
        return self._application.daily_report_history(self._principal, report_id)

    def work_request_assignee_candidates(self) -> list[dict[str, str]]:
        return self._application.work_request_assignee_candidates(self._principal)

    def list_work_requests(self) -> list[dict[str, Any]]:
        return self._application.list_work_requests(self._principal)

    def get_work_request(self, request_id: str) -> dict[str, Any]:
        return self._application.get_work_request(self._principal, UUID(request_id))

    def create_work_request(self, title: str, assignee_id: str) -> dict[str, Any]:
        action = self._propose_chat_action(
            "work_request.create",
            "업무 요청 생성 확인",
            {"title": title, "assignee_id": assignee_id},
        )
        if action is not None:
            return action
        return self._application.create_work_request(
            self._principal,
            title,
            assignee_id,
            self._mutation_key("work_request.create", {"title": title, "assignee_id": assignee_id}),
        )

    def accept_work_request(self, request_id: str, expected_version: int) -> dict[str, Any]:
        action = self._propose_chat_action("work_request.accept", "업무 요청 수락 확인", {"request_id": request_id, "expected_version": expected_version})
        if action is not None:
            return action
        return self._application.accept_work_request(self._principal, UUID(request_id), expected_version)

    def negotiate_work_request(
        self, request_id: str, expected_version: int, conditions: dict[str, Any]
    ) -> dict[str, Any]:
        action = self._propose_chat_action("work_request.negotiate", "업무 요청 협의 확인", {"request_id": request_id, "expected_version": expected_version, "conditions": conditions})
        if action is not None:
            return action
        return self._application.negotiate_work_request(
            self._principal, UUID(request_id), expected_version, conditions
        )

    def reject_work_request(
        self, request_id: str, expected_version: int, reason: str
    ) -> dict[str, Any]:
        action = self._propose_chat_action("work_request.reject", "업무 요청 거절 확인", {"request_id": request_id, "expected_version": expected_version, "reason": reason})
        if action is not None:
            return action
        return self._application.reject_work_request(
            self._principal, UUID(request_id), expected_version, reason
        )

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._application.list_tasks(self._principal)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._application.get_task(self._principal, UUID(task_id))

    def create_self_task(self, title: str) -> dict[str, Any]:
        action = self._propose_chat_action("task.create_self", "업무 생성 확인", {"title": title})
        if action is not None:
            return action
        return self._application.create_self_task(
            self._principal,
            title,
            self._mutation_key("task.create_self", {"title": title}),
        )

    def _mutation_key(self, operation: str, payload: dict[str, Any]) -> str | None:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        if not causation_id:
            return None
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{causation_id}:{operation}:{canonical}".encode()).hexdigest()

    def _propose_chat_action(
        self,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        if not causation_id:
            return None
        return self._application.propose_action(
            self._principal,
            UUID(causation_id),
            action_type,
            title,
            payload,
        )

    def transition_task(
        self,
        task_id: str,
        target: str,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        action = self._propose_chat_action(
            "task.transition", "업무 상태 변경 확인",
            {"task_id": task_id, "target": target, "expected_version": expected_version, "reason": reason},
        )
        if action is not None:
            return action
        from ax_workspace.modules.work.application import TaskState

        return self._application.transition_task(
            UUID(task_id),
            self._principal,
            TaskState(target),
            reason,
            expected_version,
        )


def create_mcp_server(settings: Settings | None = None) -> MCPServer:
    bound_persona = os.getenv("AX_MCP_PERSONA")
    if not bound_persona:
        raise RuntimeError("AX_MCP_PERSONA must bind this MCP server to an allow-listed demo persona")
    facade = McpReportsFacade(settings or Settings.from_environment(), bound_persona)
    return _create_bound_persona_server(facade)


def _create_bound_persona_server(facade: McpReportsFacade) -> MCPServer:
    principal = facade.principal
    server = MCPServer(
        f"SCAX — {principal.display_name}",
        instructions=(
            "This server is bound to one delegated persona. Daily-report commands are direct "
            "Reports operations and never expose workflow-run controls."
        ),
    )
    _register_daily_report_tools(server, facade)
    _register_task_tools(server, facade)
    if "work_request.read" in principal.capabilities:
        _register_work_request_read_tools(server, facade)
    if "work_request.create" in principal.capabilities:
        _register_work_request_create_tools(server, facade)
    if "work_request.decide" in principal.capabilities:
        _register_work_request_decision_tools(server, facade)
    return server


def _register_daily_report_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    capabilities = facade.principal.capabilities
    if DAILY_REPORT_GENERATE in capabilities:
        @server.tool(description="Generate a personal daily-report draft from authorized Work activity for one date.")
        def daily_report_generate_draft(report_date: str) -> dict[str, Any]:
            return facade.generate_daily_report_draft(report_date)

    if DAILY_REPORT_EDIT in capabilities:
        @server.tool(description="Edit the current daily-report draft without rerunning generation.")
        def daily_report_edit(
            report_id: str,
            draft_id: str,
            expected_version: int,
            body: str,
            include_source_refs: list[dict[str, Any]] | None = None,
            exclude_source_refs: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            return facade.edit_daily_report(
                report_id,
                draft_id,
                expected_version,
                body,
                include_source_refs,
                exclude_source_refs,
            )

    if DAILY_REPORT_SUBMIT in capabilities:
        @server.tool(description="Submit an immutable version of a daily report draft.")
        def daily_report_submit(
            report_id: str,
            draft_id: str,
            expected_version: int,
            reason: str | None = None,
        ) -> dict[str, Any]:
            return facade.submit_daily_report(report_id, draft_id, expected_version, reason)

    if DAILY_REPORT_READ in capabilities:
        @server.tool(description="Read the draft and immutable submission history of a daily report.")
        def daily_report_history(report_id: str) -> dict[str, Any]:
            return facade.daily_report_history(report_id)


def _register_work_request_read_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(description="List WorkRequests that the delegated persona requested or must decide.")
    def work_request_list() -> list[dict[str, Any]]:
        return facade.list_work_requests()

    @server.tool(description="Read one WorkRequest visible to the delegated persona.")
    def work_request_get(request_id: str) -> dict[str, Any]:
        return facade.get_work_request(request_id)

def _register_work_request_create_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(description="List authorized organization-ledger assignee candidates for a new WorkRequest.")
    def work_request_assignee_candidates() -> list[dict[str, str]]:
        return facade.work_request_assignee_candidates()

    @server.tool(description="Create a WorkRequest; it creates no Task until the assignee accepts.")
    def work_request_create(title: str, assignee_id: str) -> dict[str, Any]:
        return facade.create_work_request(title, assignee_id)


def _register_work_request_decision_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(description="Accept a visible WorkRequest using its required expected version.")
    def work_request_accept(request_id: str, expected_version: int) -> dict[str, Any]:
        return facade.accept_work_request(request_id, expected_version)

    @server.tool(description="Return a WorkRequest for conditions negotiation using its required expected version.")
    def work_request_negotiate(
        request_id: str, expected_version: int, conditions: dict[str, Any]
    ) -> dict[str, Any]:
        return facade.negotiate_work_request(request_id, expected_version, conditions)

    @server.tool(description="Reject a WorkRequest using its required expected version and reason.")
    def work_request_reject(request_id: str, expected_version: int, reason: str) -> dict[str, Any]:
        return facade.reject_work_request(request_id, expected_version, reason)


def _register_task_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    if TASK_READ in facade.principal.capabilities:
        @server.tool(description="List the delegated principal's active Tasks.")
        def task_list() -> list[dict[str, Any]]:
            return facade.list_tasks()

        @server.tool(description="Read one delegated principal Task.")
        def task_get(task_id: str) -> dict[str, Any]:
            return facade.get_task(task_id)

    if TASK_SELF_MANAGE not in facade.principal.capabilities:
        return

    @server.tool(description="Create a self-owned Task.")
    def task_create_self(title: str) -> dict[str, Any]:
        return facade.create_self_task(title)

    def transition(
        name: str,
        target: str,
        description: str,
        requires_reason: bool = False,
    ) -> None:
        @server.tool(description=description, name=name)
        def task_transition(
            task_id: str,
            expected_version: int,
            reason: str | None = None,
        ) -> dict[str, Any]:
            if requires_reason and not reason:
                raise ValueError("reason is required")
            return facade.transition_task(task_id, target, expected_version, reason)

    transition("task_start", "in_progress", "Start an open Task.")
    transition("task_block", "blocked", "Block a Task with a reason.", True)
    transition("task_resume", "in_progress", "Resume a blocked Task.")
    transition("task_complete", "done", "Complete an in-progress Task.")
    transition("task_cancel", "cancelled", "Cancel an active Task.")


def main() -> None:
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
