"""Persona-bound MCP adapter for SCAX public product operations.

The persona is process configuration, never a tool argument.  The server reads
the current principal from the same Organization & Access projection used by
HTTP and invokes local operations in-process.
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from ax_workspace.bootstrap.application import WorkflowApplication, create_workflow_application
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.organization_access.domain import Principal
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
        return self._application.generate_daily_report_draft(self._principal, report_date)

    def edit_daily_report(
        self,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]] | None = None,
        exclude_source_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        return self._application.submit_daily_report(
            self._principal, report_id, draft_id, expected_version, reason
        )

    def daily_report_history(self, report_id: str) -> dict[str, Any]:
        return self._application.daily_report_history(self._principal, report_id)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    bound_persona = os.getenv("AX_MCP_PERSONA")
    if not bound_persona:
        raise RuntimeError("AX_MCP_PERSONA must bind this MCP server to an allow-listed demo persona")
    facade = McpReportsFacade(settings or Settings.from_environment(), bound_persona)
    return _create_bound_persona_server(facade)


def _create_bound_persona_server(facade: McpReportsFacade) -> FastMCP:
    principal = facade.principal
    server = FastMCP(
        f"SCAX — {principal.display_name}",
        instructions=(
            "This server is bound to one delegated persona. Daily-report commands are direct "
            "Reports operations and never expose workflow-run controls."
        ),
    )
    if "work.read" in principal.capabilities:
        _register_daily_report_tools(server, facade)
    return server


def _register_daily_report_tools(server: FastMCP, facade: McpReportsFacade) -> None:
    @server.tool(description="Generate a personal daily-report draft from authorized Work activity for one date.")
    def daily_report_generate_draft(report_date: str) -> dict[str, Any]:
        return facade.generate_daily_report_draft(report_date)

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

    @server.tool(description="Submit an immutable version of a daily report draft.")
    def daily_report_submit(
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return facade.submit_daily_report(report_id, draft_id, expected_version, reason)

    @server.tool(description="Read the draft and immutable submission history of a daily report.")
    def daily_report_history(report_id: str) -> dict[str, Any]:
        return facade.daily_report_history(report_id)


def main() -> None:
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
