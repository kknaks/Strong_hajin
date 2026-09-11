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
    recipe = _recipe("local-stack")
    assert not re.search(r"\$\(MAKE\)[^\n;]*\breset-demo\b", recipe), "local-stack must never reset the database"
    assert "postgres-up" in recipe
    # Fail fast on an uninitialized schema instead of starting an API that cannot serve.
    assert "to_regclass('durable_jobs')" in recipe and "not initialized" in recipe
    assert "to_regclass('assistant_character_preferences')" in recipe
    assert "to_regclass('action_material_drafts')" in recipe
    assert "to_regclass('notifications')" in recipe
    assert "grep -qx 'notifications'" in recipe
    assert "meeting_columns.column_name" in recipe
    for column in ("description", "source_action_item_id", "source_decision_item_id", "source_submission_id", "source_review_decision_id"):
        assert f"column_name = '{column}'" in recipe
    assert "meeting_note_columns.column_name" in recipe and "column_name = 'source_status'" in recipe
    assert "follow_up_candidates" in recipe
    assert "follow_up_candidate_id" in recipe
    assert "Run 'make sync-demo-schema'" in recipe
    # Supervise: an early exit of any required process stops the rest and fails the target (plain `wait` cannot see it).
    assert "check_alive" in recipe and "kill -0" in recipe and "while check_alive; do" in recipe


def test_acceptance_never_resets_the_database_someone_is_working_in() -> None:
    """Acceptance는 reset으로 시작한다. 그것이 사람이 조직과 자료를 넣어 둔 DATABASE_URL이면 안 된다."""
    recipe = _recipe("acceptance-e2e")
    touching = re.findall(r"\$\(MAKE\)([^\n;&>]*)\b(reset-demo|api-e2e|conversation-worker|material-worker|meeting-worker)\b", recipe)
    assert touching, "acceptance-e2e does not start anything"
    for arguments, target in touching:
        assert "ACCEPTANCE_DATABASE_URL" in arguments, f"acceptance-e2e runs {target} against the working database"
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "ACCEPTANCE_DATABASE_URL ?=" in text
    # 그 데이터베이스는 postgres-up이 만들어 둔다. 없으면 suite가 연결에서 죽는다.
    assert "ax_test_acceptance" in _recipe("postgres-up")


def test_stack_target_is_declared_phony_and_documented() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    phony = next(line for line in text.splitlines() if line.startswith(".PHONY:"))
    assert "local-stack" in phony.split()
    readme = (MAKEFILE.parent / "README.md").read_text(encoding="utf-8")
    assert "make local-stack" in readme
