"""`python -m`으로 띄우는 module은 마지막 줄에서 시작해야 한다.

MCP 서버는 `python -m ax_workspace.entrypoints.mcp`로 별도 process가 되어 실행된다. module은 위에서 아래로
읽히므로 서버를 띄우는 줄 아래에 남은 정의는 아직 존재하지 않고, 그것을 부르는 도구는 `NameError`로 실패한다.

그 실패는 protocol이 `Error executing tool`만 남기고 이유를 지운 채 전달하므로 오래 보이지 않을 수 있다.
실제로 그랬다 — 날짜를 다루는 도구 여섯이 그 아래의 helper를 부르고 있었다.
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ax_workspace"


def test_nothing_is_defined_after_a_module_starts_running() -> None:
    for module in (PACKAGE_ROOT / "entrypoints").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        if '__name__ == "__main__"' not in source:
            continue
        after = source.split('if __name__ == "__main__":', 1)[1]
        remaining = [
            line
            for line in after.splitlines()
            if line.startswith(("def ", "class ", "async def "))
        ]
        assert not remaining, f"{module.name}: 실행 뒤에 정의가 남아 있습니다 — {remaining}"


def test_catalog_and_explicit_callbacks_have_the_same_operation_binding():
    import ast
    from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG, LEGACY_TOOL_TITLES
    from ax_workspace.modules.organization_access.catalog import CAPABILITY_IDS

    tree = ast.parse((PACKAGE_ROOT / 'entrypoints' / 'mcp.py').read_text())
    bindings = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorated = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'tool'
                        for d in node.decorator_list)
        if decorated:
            calls = {n.func.attr for n in ast.walk(node)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and isinstance(n.func.value, ast.Name) and n.func.value.id == 'facade'}
            assert len(calls) == 1, node.name
            bindings[node.name] = next(iter(calls))
    for name, definition in TOOL_CATALOG.items():
        callback = 'task_transition' if name in {'task_start', 'task_block', 'task_resume', 'task_complete', 'task_cancel'} else name
        assert bindings[callback] == definition.adapter_operation
        assert set(definition.all_capabilities + definition.any_capabilities) <= CAPABILITY_IDS
        assert any('가' <= char <= '힣' for char in definition.title)
    assert not set(LEGACY_TOOL_TITLES) & TOOL_CATALOG.keys()
    assert not {'grant_access_role', 'revoke_access_grant', 'set_role_capabilities'} & TOOL_CATALOG.keys()
