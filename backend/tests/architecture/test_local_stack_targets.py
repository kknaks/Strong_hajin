"""Every integrated local run path (manual stack and acceptance) must start all required processes.

A missing worker leaves AX turns or material extraction silently pending, which is exactly the regression this guards.
"""
from pathlib import Path
import re

MAKEFILE = Path(__file__).resolve().parents[3] / "Makefile"
REQUIRED_PROCESS_TARGETS = ("api-e2e", "conversation-worker", "material-worker", "frontend-e2e")


def _recipe(name: str) -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}:\n((?:\t.*\n?)+)", text, re.M)
    assert match, f"Makefile target {name} is missing"
    return match.group(1)


def test_stack_and_acceptance_targets_start_every_required_process() -> None:
    for target in ("local-stack", "acceptance-e2e"):
        recipe = _recipe(target)
        missing = [process for process in REQUIRED_PROCESS_TARGETS if not re.search(rf"\$\(MAKE\)[^\n;]*\b{re.escape(process)}\b", recipe)]
        assert not missing, f"{target} does not start {missing}"
    assert "reset-demo" not in _recipe("local-stack"), "local-stack must never reset the database"
    assert "postgres-up" in _recipe("local-stack")


def test_stack_target_is_declared_phony_and_documented() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    phony = next(line for line in text.splitlines() if line.startswith(".PHONY:"))
    assert "local-stack" in phony.split()
    readme = (MAKEFILE.parent / "README.md").read_text(encoding="utf-8")
    assert "make local-stack" in readme
