"""Persona-bound discovery and invocation using the SDK's public hooks."""

from typing import Any, Callable, Protocol
from inspect import signature

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.ax_execution.actions import TurnProposalSlotTaken
from ax_workspace.modules.errors import ResourceNotFound, RESOURCE_NOT_FOUND_MESSAGE


class PrincipalReader(Protocol):
    @property
    def principal(self) -> Principal: ...


class PersonaMcpServer(MCPServer):
    def __init__(
        self,
        principal_reader: PrincipalReader,
        name: str,
        *,
        delegated: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self._principal_reader = principal_reader
        self._delegated = delegated
        self._registered: set[str] = set()
        self._input_fields: dict[str, frozenset[str]] = {}

    def tool(self, name: str | None = None, **kwargs: Any) -> Callable:
        def register(callback: Callable) -> Callable:
            tool_id = name or callback.__name__
            definition = TOOL_CATALOG[tool_id]
            if tool_id in self._registered:
                raise ValueError(f"duplicate SCAX tool registration: {tool_id}")
            if "title" in kwargs or "description" in kwargs:
                raise ValueError("SCAX tool metadata belongs in the tool catalog")
            if definition.adapter_operation not in callback.__code__.co_names:
                raise ValueError(
                    f"SCAX tool adapter binding differs: {tool_id} -> {definition.adapter_operation}"
                )
            bound = super(PersonaMcpServer, self).tool(
                name=tool_id,
                title=definition.title,
                description=definition.description,
                **kwargs,
            )(callback)
            self._registered.add(tool_id)
            self._input_fields[tool_id] = frozenset(signature(callback).parameters)
            return bound

        return register

    def validate_catalog(self) -> None:
        if self._registered != TOOL_CATALOG.keys():
            raise ValueError("SCAX tool catalog and runtime registrations differ")

    async def list_tools(self):
        capabilities = self._principal_reader.principal.capabilities
        visible = [
            tool
            for tool in await super().list_tools()
            if TOOL_CATALOG[tool.name].visible(capabilities, delegated=self._delegated)
        ]
        for tool in visible:
            tool.input_schema = {**tool.input_schema, "additionalProperties": False}
        return visible

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        definition = TOOL_CATALOG.get(name)
        capabilities = self._principal_reader.principal.capabilities
        if definition is None or not definition.visible(
            capabilities, delegated=self._delegated
        ):
            raise ToolError("Unknown tool")
        unknown = arguments.keys() - self._input_fields[name]
        if unknown:
            raise ToolError(f"Unknown arguments: {', '.join(sorted(unknown))}")
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as error:
            if isinstance(error.__cause__, ResourceNotFound):
                raise ToolError(RESOURCE_NOT_FOUND_MESSAGE) from error
            # A refusal the caller is meant to act on: say why, rather than masking it as a tool failure.
            if isinstance(error.__cause__, TurnProposalSlotTaken):
                raise ToolError(str(error.__cause__)) from error
            raise
