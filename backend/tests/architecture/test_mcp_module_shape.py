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
