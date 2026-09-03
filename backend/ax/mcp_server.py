from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from ax.auth import Principal, seeded_principal
from ax.database import make_session_factory
from ax.runtime import AccessDenied, SqlAlchemyUnitOfWork, WorkflowRunStarter
from ax.settings import Settings
from ax.workflows import catalog_definitions


class McpWorkflowFacade:
    """MCP adapter over the application operation; no HTTP or separate demo runtime is used."""

    def __init__(self, settings: Settings) -> None:
        if not settings.developer_auth_enabled:
            raise RuntimeError("MCP demo adapter is available only in development and test profiles")
        self._session_factory = make_session_factory(settings.database_url)

    @staticmethod
    def _principal(persona: str) -> Principal:
        return seeded_principal(persona)

    def list_allowed_workflows(self, persona: str) -> list[dict[str, Any]]:
        principal = self._principal(persona)
        return [
            {
                "workflow_id": definition.workflow_id,
                "version": definition.version,
                "title": definition.title,
                "description": definition.description,
                "required_scope": definition.required_scope,
            }
            for definition in catalog_definitions()
            if definition.is_visible_to(principal)
        ]

    def start_workflow(
        self, persona: str, workflow_id: str, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        principal = self._principal(persona)
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return WorkflowRunStarter(uow.workflows).start(workflow_id, principal, input_data)

    def get_workflow_run(self, persona: str, run_id: str) -> dict[str, Any]:
        principal = self._principal(persona)
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            service = WorkflowRunStarter(uow.workflows)
            result = service.summary(UUID(run_id))
            if not service.can_view(UUID(run_id), principal):
                raise AccessDenied("Principal cannot view this workflow run")
            return result

    def list_decision_inbox(self, persona: str) -> list[dict[str, Any]]:
        principal = self._principal(persona)
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return WorkflowRunStarter(uow.workflows).inbox(principal)

    def submit_decision(
        self,
        persona: str,
        run_id: str,
        node_id: str,
        decision: str,
        rationale: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        principal = self._principal(persona)
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return WorkflowRunStarter(uow.workflows).decide(
                UUID(run_id), node_id, principal, decision, rationale, payload or {}
            )


def create_mcp_server(settings: Settings | None = None, persona: str | None = None) -> FastMCP:
    facade = McpWorkflowFacade(settings or Settings.from_environment())
    bound_persona = persona or os.getenv("AX_MCP_PERSONA")
    if bound_persona:
        return _create_bound_persona_server(facade, bound_persona)
    server = FastMCP(
        "SCAX Workflow Catalog",
        instructions=(
            "Discover the selected demo persona's permitted workflows before starting one. "
            "Use the inbox and decision tools for human gates; do not assume approval."
        ),
    )

    @server.tool(
        description="List the WorkflowDefinitionVersion catalog this allow-listed demo persona may start."
    )
    def list_allowed_workflows(persona: str) -> list[dict[str, Any]]:
        return facade.list_allowed_workflows(persona)

    @server.tool(
        description="Start a version-pinned workflow run through the shared WorkflowRunStarter."
    )
    def start_workflow(
        persona: str, workflow_id: str, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        return facade.start_workflow(persona, workflow_id, input_data)

    @server.tool(description="Read persisted state, tool outputs, and append-only audit events for an allowed run.")
    def get_workflow_run(persona: str, run_id: str) -> dict[str, Any]:
        return facade.get_workflow_run(persona, run_id)

    @server.tool(description="List human-decision gates this demo persona may act on.")
    def list_decision_inbox(persona: str) -> list[dict[str, Any]]:
        return facade.list_decision_inbox(persona)

    @server.tool(description="Accept or reject a waiting human-decision gate through the shared runtime.")
    def submit_workflow_decision(
        persona: str,
        run_id: str,
        node_id: str,
        decision: str,
        rationale: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return facade.submit_decision(persona, run_id, node_id, decision, rationale, payload)

    return server


def _create_bound_persona_server(facade: McpWorkflowFacade, persona: str) -> FastMCP:
    principal = seeded_principal(persona)
    server = FastMCP(
        f"SCAX Workflow Catalog — {principal.display_name}",
        instructions=(
            "This server is bound to one allow-listed demo persona. "
            "Only its eligible workflow start tools are advertised; use the inbox tools for human gates."
        ),
    )
    for definition in catalog_definitions():
        if definition.is_visible_to(principal):
            _register_bound_start_tool(server, facade, persona, definition.workflow_id, definition.title)

    @server.tool(description="Read the persisted status and audit trail for a workflow run you may inspect.")
    def get_my_workflow_run(run_id: str) -> dict[str, Any]:
        return facade.get_workflow_run(persona, run_id)

    @server.tool(description="List decision gates currently assigned to this bound demo persona.")
    def get_my_workflow_inbox() -> list[dict[str, Any]]:
        return facade.list_decision_inbox(persona)

    @server.tool(description="Accept or reject a decision gate as this bound demo persona.")
    def submit_my_workflow_decision(
        run_id: str,
        node_id: str,
        decision: str,
        rationale: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return facade.submit_decision(persona, run_id, node_id, decision, rationale, payload)

    return server


def _register_bound_start_tool(
    server: FastMCP,
    facade: McpWorkflowFacade,
    persona: str,
    workflow_id: str,
    title: str,
) -> None:
    tool_name = f"start_{workflow_id.replace('-', '_')}"

    @server.tool(name=tool_name, description=f"Start the {title} workflow as the bound demo persona.")
    def start_bound_workflow(input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        return facade.start_workflow(persona, workflow_id, input_data or {})


def main() -> None:
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
