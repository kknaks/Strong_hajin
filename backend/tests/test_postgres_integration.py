import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest

from ax_workspace.modules.organization_access.domain import seeded_principal
from ax_workspace.entrypoints.demo_rehearsal import run_golden_rehearsal
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.application import InvalidDecision, WorkflowRunStarter
from ax_workspace.platform.workflow_runtime import LocalDemoToolDispatcher, SqlAlchemyUnitOfWork, workflow_service
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings


@pytest.mark.integration
def test_postgres_golden_rehearsal_persists_all_domain_complete_flows() -> None:
    database_url = os.getenv("AX_POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("Set AX_POSTGRES_TEST_URL to run against a disposable PostgreSQL database")
    reset_database(database_url)

    report = run_golden_rehearsal(Settings(RuntimeProfile.TEST, database_url))

    assert {flow["state"] for flow in report["flows"].values()} == {"completed"}
    assert report["flows"]["daily_report"]["tool_names"][-1] == "daily_report.submit_snapshot"
    assert report["flows"]["meeting_followups"]["my_work_state"] == "active"
    assert report["flows"]["contract_review"]["effect_after_legal"] is False


@pytest.mark.integration
def test_postgres_serializes_competing_human_decisions_before_effect_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("AX_POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("Set AX_POSTGRES_TEST_URL to run against a disposable PostgreSQL database")
    reset_database(database_url)
    session_factory = make_session_factory(database_url)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.workflows is not None
        started = workflow_service(uow.workflows).start("daily-report", seeded_principal("mina"), {})

    effect_started = Event()
    release_effect = Event()
    rejection_finished = Event()
    original_dispatch = LocalDemoToolDispatcher.dispatch

    def block_first_effect(self, run, node, execution):
        if run.id.hex == started["run_id"].replace("-", "") and node.id == "effect" and not effect_started.is_set():
            effect_started.set()
            assert release_effect.wait(timeout=3), "test did not release the pending effect"
        return original_dispatch(self, run, node, execution)

    monkeypatch.setattr(LocalDemoToolDispatcher, "dispatch", block_first_effect)

    def decide(decision: str) -> str:
        try:
            with SqlAlchemyUnitOfWork(session_factory) as uow:
                assert uow.workflows is not None
                result = workflow_service(uow.workflows).decide(
                    UUID(started["run_id"]), "confirm", seeded_principal("mina"), decision, None, {}
                )
                return str(result["state"])
        except InvalidDecision:
            return "conflict"
        finally:
            if decision == "reject":
                rejection_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepting = executor.submit(decide, "accept")
        assert effect_started.wait(timeout=3), "accepting decision did not reach its effect"
        rejecting = executor.submit(decide, "reject")
        assert not rejection_finished.wait(timeout=0.25), "rejection bypassed the in-flight decision lock"
        release_effect.set()
        assert accepting.result(timeout=3) == "completed"
        assert rejecting.result(timeout=3) == "conflict"
