"""The baseline plus explicit deltas must cover the current product surface.

Registration evidence detects drift; it does not stand in for actor/effect tests.
"""
import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace

from ax_workspace.bootstrap.operation_inventory import (
    HTTP_TOOL_TARGET_OVERRIDES,
    tool_targets_for_http,
)
from ax_workspace.entrypoints.mcp import _create_bound_persona_server
from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG
from ax_workspace.modules.organization_access.catalog import CAPABILITY_IDS

ROOT = Path(__file__).resolve().parents[3]
APPLICATION_SOURCE = ROOT / 'backend/src/ax_workspace/bootstrap/application.py'


def _workflow_owner_calls(operation: str) -> list[str]:
    tree = ast.parse(APPLICATION_SOURCE.read_text())
    workflow = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'WorkflowApplication'
    )
    functions = {
        node.name: node
        for node in workflow.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def owner_base(expression: ast.expr, aliases: dict[str, str]) -> str | None:
        if isinstance(expression, ast.Name):
            return aliases.get(expression.id)
        if isinstance(expression, ast.Call):
            function = expression.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == 'self'
                and function.attr.startswith('_')
            ):
                return ast.unparse(expression)
            if (
                isinstance(function, ast.Name)
                and function.id.endswith(
                    ('Application', 'Administration', 'Repository', 'Owners', 'Storage')
                )
            ):
                return ast.unparse(expression)
        if (
            isinstance(expression, ast.Attribute)
            and isinstance(expression.value, ast.Name)
            and expression.value.id == 'self'
            and expression.attr.startswith('_')
        ):
            return ast.unparse(expression)
        return None

    def extract(name: str, seen: tuple[str, ...] = ()) -> set[str]:
        if name in seen or name not in functions:
            return set()
        node = functions[name]
        aliases: dict[str, str] = {}
        for item in ast.walk(node):
            if not isinstance(item, (ast.Assign, ast.AnnAssign)) or item.value is None:
                continue
            base = owner_base(item.value, aliases)
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            if base:
                aliases.update(
                    (target.id, base) for target in targets if isinstance(target, ast.Name)
                )

        calls: set[str] = set()
        for item in ast.walk(node):
            if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Attribute):
                continue
            function = item.func
            if (
                isinstance(function.value, ast.Name)
                and function.value.id == 'self'
                and function.attr in functions
            ):
                returns = functions[function.attr].returns
                return_name = ast.unparse(returns) if returns else ''
                if not return_name.endswith(
                    ('Application', 'Queue', 'Repository', 'ActionServices')
                ):
                    calls.update(extract(function.attr, (*seen, name)))
                continue
            base = owner_base(function.value, aliases)
            if base:
                calls.add(f'{base}.{function.attr}')
        return calls

    return sorted(extract(operation))


def test_inventory_schemas_match_the_actual_registered_tools():
    inventory = json.loads((ROOT / 'docs/unified-operations-inventory.json').read_text())
    reader = SimpleNamespace(principal=SimpleNamespace(display_name='inventory', capabilities=frozenset(CAPABILITY_IDS)))
    tools = asyncio.run(_create_bound_persona_server(reader).list_tools())
    actual = {
        tool.name: {
            'name': tool.name,
            'title': tool.title,
            'description': tool.description,
            'adapter_operation': TOOL_CATALOG[tool.name].adapter_operation,
            'exposure': {
                'any_capabilities': list(TOOL_CATALOG[tool.name].any_capabilities),
                'all_capabilities': list(TOOL_CATALOG[tool.name].all_capabilities),
                'authenticated_only': TOOL_CATALOG[tool.name].authenticated_only,
                'requires_confirmation': TOOL_CATALOG[tool.name].requires_confirmation,
            },
            'annotations': tool.annotations.model_dump() if tool.annotations else None,
            'input_schema': tool.input_schema,
            'output_schema': tool.output_schema,
        }
        for tool in tools
    }
    captured = {tool['name']: tool for tool in inventory['current_runtime']['tools']}
    assert actual == captured
    assert inventory['current_runtime']['tool_count'] == len(actual)


def test_inventory_includes_each_declared_http_operation():
    inventory = json.loads((ROOT / 'docs/unified-operations-inventory.json').read_text())
    tree = ast.parse((ROOT / 'backend/src/ax_workspace/entrypoints/http.py').read_text())
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def signature(node) -> str:
        """라우트가 실제로 받고 돌려주는 타입. 느슨해지면 이 문장이 달라진다."""
        arguments = node.args
        parts = [
            f'{argument.arg}: {ast.unparse(argument.annotation)}' if argument.annotation else argument.arg
            for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        ]
        returns = ast.unparse(node.returns) if node.returns else 'None'
        return f"({', '.join(parts)}) -> {returns}"

    def application_calls(name: str, seen: tuple[str, ...] = ()) -> set[str]:
        if name in seen or name not in functions:
            return set()
        node = functions[name]
        calls = {
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and isinstance(item.func.value, ast.Attribute)
            and item.func.value.attr == 'workflow_application'
        }
        local_calls = {
            item.func.id
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id in functions
        }
        for local_call in local_calls:
            calls.update(application_calls(local_call, (*seen, name)))
        return calls

    actual = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == 'app'
                    and decorator.func.attr in {'get', 'post', 'put', 'patch', 'delete'}):
                assert decorator.args and isinstance(decorator.args[0], ast.Constant), node.name
                route = f'{decorator.func.attr.upper()} {decorator.args[0].value}'
                calls = sorted(application_calls(node.name))
                actual[route] = {
                    'http_handler': node.name,
                    **({'http_application_calls': calls} if calls else {}),
                    'http_signature': signature(node),
                }
    captured = {
        row['http']: {
            'http_handler': row['http_handler'],
            **(
                {'http_application_calls': row['http_application_calls']}
                if row.get('http_application_calls')
                else {}
            ),
            'http_signature': row['http_signature'],
        }
        for row in [*inventory['http'], *inventory['current_runtime']['added_operations']]
    }
    assert actual == captured
    assert inventory['current_runtime']['http_count'] == len(actual)


def test_final_inventory_has_no_implicit_or_unverified_product_surface():
    inventory = json.loads((ROOT / 'docs/unified-operations-inventory.json').read_text())
    rows = [*inventory['http'], *inventory['current_runtime']['added_operations']]
    assert {row['status'] for row in rows} <= {'verified', 'excluded'}
    assert all(row.get('acceptance_evidence') for row in rows if row['status'] == 'verified')
    verified = [row for row in rows if row['status'] == 'verified']
    assert all(
        row.get('http_handler')
        and row.get('http_application_calls')
        and row.get('owning_calls')
        and row.get('target_tools')
        and row.get('policy')
        for row in verified
    )
    for row in verified:
        assert sorted(row['target_tools']) == sorted(
            tool_targets_for_http(row['http'], row['http_application_calls'])
        ), row['http']
        actual_owners = sorted(
            {
                owner
                for operation in row['http_application_calls']
                for owner in _workflow_owner_calls(operation)
            }
        )
        assert row['owning_calls'] == actual_owners, row['http']
    routes = {row['http'] for row in verified}
    assert HTTP_TOOL_TARGET_OVERRIDES.keys() <= routes
    excluded = [row for row in rows if row['status'] == 'excluded']
    assert excluded
    assert all(
        row.get('exclusion', {}).get('decision')
        and row['exclusion'].get('reason')
        and row['exclusion'].get('alternative')
        and row['exclusion'].get('reconsider_when')
        for row in excluded
    )

    registered = {tool['name'] for tool in inventory['current_runtime']['tools']}
    accepted = {
        row['name'] for row in inventory['current_runtime']['tool_acceptance']
        if row.get('status') == 'verified'
    }
    assert accepted == registered


def test_discovery_matches_each_catalog_exposure_policy(monkeypatch):
    capabilities = sorted(CAPABILITY_IDS)
    matrix = [frozenset(), frozenset(capabilities)]
    matrix.extend(frozenset({capability}) for capability in capabilities)
    matrix.extend(frozenset(set(capabilities) - {capability}) for capability in capabilities)

    for delegated in (False, True):
        if delegated:
            monkeypatch.setenv('AX_MCP_CAUSATION_ID', 'inventory-delegated')
        else:
            monkeypatch.delenv('AX_MCP_CAUSATION_ID', raising=False)
        reader = SimpleNamespace(
            principal=SimpleNamespace(display_name='inventory', capabilities=frozenset())
        )
        server = _create_bound_persona_server(reader)
        for granted in matrix:
            reader.principal = SimpleNamespace(
                display_name='inventory', capabilities=granted
            )
            actual = {tool.name for tool in asyncio.run(server.list_tools())}
            expected = {
                tool_id
                for tool_id, definition in TOOL_CATALOG.items()
                if definition.visible(granted, delegated=delegated)
            }
            assert actual == expected
