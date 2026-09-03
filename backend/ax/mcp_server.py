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

    def __init__(self, settings: Settings, principal: Principal) -> None:
        if not settings.developer_auth_enabled:
            raise RuntimeError("MCP demo adapter is available only in development and test profiles")
        self._session_factory = make_session_factory(settings.database_url)
        self._principal = principal

    def list_allowed_workflows(self) -> list[dict[str, Any]]:
        return [
            {
                "workflow_id": definition.workflow_id,
                "version": definition.version,
                "title": definition.title,
                "description": definition.description,
                "required_scope": definition.required_scope,
            }
            for definition in catalog_definitions()
            if definition.is_visible_to(self._principal)
        ]

    def start_workflow(self, workflow_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return WorkflowRunStarter(uow.workflows).start(workflow_id, self._principal, input_data)

    def get_workflow_run(self, run_id: str) -> dict[str, Any]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            service = WorkflowRunStarter(uow.workflows)
            result = service.summary(UUID(run_id))
            if not service.can_view(UUID(run_id), self._principal):
                raise AccessDenied("Principal cannot view this workflow run")
            return result

    def list_decision_inbox(self) -> list[dict[str, Any]]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return WorkflowRunStarter(uow.workflows).inbox(self._principal)

    def submit_decision(
        self,
        run_id: str,
        node_id: str,
        decision: str,
        rationale: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return WorkflowRunStarter(uow.workflows).decide(
                UUID(run_id), node_id, self._principal, decision, rationale, payload or {}
            )


def create_mcp_server(settings: Settings | None = None, persona: str | None = None) -> FastMCP:
    bound_persona = persona or os.getenv("AX_MCP_PERSONA")
    if not bound_persona:
        raise RuntimeError("AX_MCP_PERSONA must bind this MCP server to an allow-listed demo persona")
    principal = seeded_principal(bound_persona)
    facade = McpWorkflowFacade(settings or Settings.from_environment(), principal)
    return _create_bound_persona_server(facade, principal)


def _create_bound_persona_server(facade: McpWorkflowFacade, principal: Principal) -> FastMCP:
    server = FastMCP(
        f"SCAX Workflow Catalog — {principal.display_name}",
        instructions=(
            "This server is bound to one allow-listed demo persona. "
            "Only its eligible workflow start tools are advertised; use the inbox tools for human gates."
        ),
    )
    for definition in catalog_definitions():
        if definition.is_visible_to(principal):
            _register_bound_start_tool(server, facade, definition.workflow_id, definition.title)

    @server.tool(description="Read the persisted status and audit trail for a workflow run you may inspect.")
    def get_my_workflow_run(run_id: str) -> dict[str, Any]:
        return facade.get_workflow_run(run_id)

    @server.tool(description="List decision gates currently assigned to this bound demo persona.")
    def get_my_workflow_inbox() -> list[dict[str, Any]]:
        return facade.list_decision_inbox()

    @server.tool(description="Accept or reject a decision gate as this bound demo persona.")
    def submit_my_workflow_decision(
        run_id: str,
        node_id: str,
        decision: str,
        rationale: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return facade.submit_decision(run_id, node_id, decision, rationale, payload)

    return server


def _register_bound_start_tool(
    server: FastMCP,
    facade: McpWorkflowFacade,
    workflow_id: str,
    title: str,
) -> None:
    tool_name = f"start_{workflow_id.replace('-', '_')}"

    @server.tool(name=tool_name, description=f"Start the {title} workflow as the bound demo persona.")
    def start_bound_workflow(input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        return facade.start_workflow(workflow_id, input_data or {})


def main() -> None:
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
