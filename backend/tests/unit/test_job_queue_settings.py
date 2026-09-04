"""AX_JOB_QUEUE_BACKEND replaces the PGMQ-era variable; legacy or removed values fail fast with an actionable message."""
import pytest

from ax_workspace.bootstrap.settings import Settings


def test_default_environment_uses_the_postgres_job_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AX_JOB_QUEUE_BACKEND", raising=False)
    monkeypatch.delenv("AX_CONVERSATION_QUEUE_BACKEND", raising=False)
    assert Settings.from_environment().job_queue_backend == "postgres"
    monkeypatch.setenv("AX_JOB_QUEUE_BACKEND", "memory")
    assert Settings.from_environment().job_queue_backend == "memory"


def test_legacy_variable_and_removed_pgmq_value_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AX_JOB_QUEUE_BACKEND", raising=False)
    monkeypatch.setenv("AX_CONVERSATION_QUEUE_BACKEND", "pgmq")
    with pytest.raises(RuntimeError, match="AX_CONVERSATION_QUEUE_BACKEND is no longer supported"):
        Settings.from_environment()
    monkeypatch.delenv("AX_CONVERSATION_QUEUE_BACKEND")
    monkeypatch.setenv("AX_JOB_QUEUE_BACKEND", "pgmq")
    with pytest.raises(RuntimeError, match="pgmq transport was removed"):
        Settings.from_environment()
    monkeypatch.setenv("AX_JOB_QUEUE_BACKEND", "null")
    with pytest.raises(RuntimeError, match="explicit in-code test configuration"):
        Settings.from_environment()


def test_in_code_settings_default_to_the_memory_fake_for_tests() -> None:
    assert Settings("test", "sqlite:///:memory:").job_queue_backend == "memory"
