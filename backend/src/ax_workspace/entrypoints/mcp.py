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
    WORK_REQUEST_CREATE,
    WORK_REQUEST_DECIDE,
)
from ax_workspace.modules.ax_execution.ai import AiProvider


DELEGATED_ACTION_CAPABILITIES = {
    "daily_report.edit": DAILY_REPORT_EDIT,
    "daily_report.submit": DAILY_REPORT_SUBMIT,
    "work_request.create": WORK_REQUEST_CREATE,
    "work_request.accept": WORK_REQUEST_DECIDE,
    "work_request.negotiate": WORK_REQUEST_DECIDE,
    "work_request.reject": WORK_REQUEST_DECIDE,
    "task.create_self": TASK_SELF_MANAGE,
    "task.transition": TASK_SELF_MANAGE,
    "task.update": TASK_SELF_MANAGE,
}


class McpDelegatedActionAccessDenied(RuntimeError):
    pass


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
        self._persona_id = persona_id

    @property
    def principal(self) -> Principal:
        return self._application.authenticated_principal(self._persona_id)

    def generate_daily_report_draft(self, report_date: str) -> dict[str, Any]:
        return self._application.generate_daily_report_draft(
            self.principal,
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
            self.principal,
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
            self.principal, report_id, draft_id, expected_version, reason
        )

    def daily_report_history(self, report_id: str) -> dict[str, Any]:
        return self._application.daily_report_history(self.principal, report_id)

    def work_request_assignee_candidates(self) -> list[dict[str, str]]:
        return self._application.work_request_assignee_candidates(self.principal)

    def list_work_requests(self) -> list[dict[str, Any]]:
        return self._application.list_work_requests(self.principal)

    def get_work_request(self, request_id: str) -> dict[str, Any]:
        return self._application.get_work_request(self.principal, UUID(request_id))

    def create_work_request(self, title: str, assignee_id: str, due_date: str | None = None, description: str | None = None) -> dict[str, Any]:
        action = self._propose_chat_action(
            "work_request.create",
            "업무 요청 생성 확인",
            {"title": title, "assignee_id": assignee_id, "due_date": due_date, "description": description},
        )
        if action is not None:
            return action
        return self._application.create_work_request(
            self.principal,
            title,
            assignee_id,
            self._mutation_key("work_request.create", {"title": title, "assignee_id": assignee_id, "due_date": due_date, "description": description}),
            description=description,
            due_date=_parse_iso_date(due_date),
        )

    def accept_work_request(self, request_id: str, expected_version: int) -> dict[str, Any]:
        action = self._propose_chat_action("work_request.accept", "업무 요청 수락 확인", {"request_id": request_id, "expected_version": expected_version})
        if action is not None:
            return action
        return self._application.accept_work_request(self.principal, UUID(request_id), expected_version)

    def negotiate_work_request(
        self, request_id: str, expected_version: int, conditions: dict[str, Any]
    ) -> dict[str, Any]:
        action = self._propose_chat_action("work_request.negotiate", "업무 요청 협의 확인", {"request_id": request_id, "expected_version": expected_version, "conditions": conditions})
        if action is not None:
            return action
        return self._application.negotiate_work_request(
            self.principal, UUID(request_id), expected_version, conditions
        )

    def reject_work_request(
        self, request_id: str, expected_version: int, reason: str
    ) -> dict[str, Any]:
        action = self._propose_chat_action("work_request.reject", "업무 요청 거절 확인", {"request_id": request_id, "expected_version": expected_version, "reason": reason})
        if action is not None:
            return action
        return self._application.reject_work_request(
            self.principal, UUID(request_id), expected_version, reason
        )

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._application.list_tasks(self.principal)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._application.get_task(self.principal, UUID(task_id))

    def create_self_task(self, title: str) -> dict[str, Any]:
        action = self._propose_chat_action("task.create_self", "업무 생성 확인", {"title": title})
        if action is not None:
            return action
        return self._application.create_self_task(
            self.principal,
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
        principal = self._principal_for_delegated_action(action_type)
        return self._application.propose_action(
            principal,
            UUID(causation_id),
            action_type,
            title,
            payload,
        )

    def _principal_for_delegated_action(self, action_type: str) -> Principal:
        capability = DELEGATED_ACTION_CAPABILITIES.get(action_type)
        if capability is None:
            raise RuntimeError(f"delegated Action capability is not mapped for {action_type}")
        principal = self.principal
        if capability not in principal.capabilities:
            raise McpDelegatedActionAccessDenied(f"{capability} capability is required")
        return principal

    def update_task(self, task_id: str, expected_version: int, changes: dict[str, Any]) -> dict[str, Any]:
        action = self._propose_chat_action(
            "task.update", "업무 내용·일정 수정 확인",
            {"task_id": task_id, "expected_version": expected_version, "changes": changes},
        )
        if action is not None:
            return action
        parsed = dict(changes)
        for field in ("start_date", "due_date"):
            if field in parsed:
                parsed[field] = _parse_iso_date(parsed[field])
        return self._application.update_task(self.principal, UUID(task_id), expected_version, parsed)

    def list_task_materials(self, task_id: str) -> list[dict[str, Any]]:
        return self._application.list_task_materials(self.principal, UUID(task_id))

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
            self.principal,
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

    @server.tool(description="Create a WorkRequest with an optional ISO due_date and description; it creates no Task until the assignee accepts.")
    def work_request_create(title: str, assignee_id: str, due_date: str | None = None, description: str | None = None) -> dict[str, Any]:
        return facade.create_work_request(title, assignee_id, due_date, description)


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

        @server.tool(description="List reference documents (input) and deliverables (output) attached to a Task.")
        def task_materials_list(task_id: str) -> list[dict[str, Any]]:
            return facade.list_task_materials(task_id)

    if TASK_SELF_MANAGE not in facade.principal.capabilities:
        return

    @server.tool(description="Create a self-owned Task.")
    def task_create_self(title: str) -> dict[str, Any]:
        return facade.create_self_task(title)

    @server.tool(description="Edit an owned Task's title, description, start_date, or due_date (ISO dates) using its required expected version.")
    def task_update(
        task_id: str,
        expected_version: int,
        title: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if description is not None:
            changes["description"] = description
        if start_date is not None:
            changes["start_date"] = start_date or None
        if due_date is not None:
            changes["due_date"] = due_date or None
        if not changes:
            raise ValueError("at least one field is required")
        return facade.update_task(task_id, expected_version, changes)

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
    transition("task_resume", "in_progress", "Resume a blocked Task or reopen a completed one.")
    transition("task_complete", "done", "Complete an in-progress Task.")
    transition("task_cancel", "cancelled", "Cancel an active Task.")


def main() -> None:
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()


def _parse_iso_date(value: Any):
    if value in (None, ""):
        return None
    from datetime import date

    return date.fromisoformat(str(value))
