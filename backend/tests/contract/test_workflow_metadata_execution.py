"""등록된 조합은 코드를 고치지 않고 실행된다.

A workflow definition is data. The runtime may only run node types and operations it has registered, but within that
allow-list the definition decides: which nodes exist, what they are called, what feeds what, and which node's field
is the answer. Renaming a node or reordering the graph is a change to the data, not to the code.
"""
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.reports.workflow_metadata import (
    DAILY_REPORT_PROVIDER_PROFILE,
    content_hash,
    validate_definition,
)
from ax_workspace.platform.persistence import (
    WorkflowDefinitionVersionRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}


class StubProvider:
    """Test injection only; production composition always uses the Codex CLI adapter."""

    def generate(self, request):
        from ax_workspace.modules.ax_execution.ai import AiGeneration

        assert "daily work report" in request.prompt  # the template the definition named
        return AiGeneration(
            provider_run_ref="run-metadata-test",
            provider_session_ref=None,
            body="오늘 한 일을 정리했습니다.",
            requested_model="test",
            observed_model="test",
            requested_tier="test",
            observed_tier=None,
            latency_ms=1,
            usage=None,
        )


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings, report_provider=StubProvider())
    return TestClient(app), database_url


def _publish(database_url: str, version: str, definition: dict) -> None:
    """Install another published version of the same workflow, the way an operator would."""
    validate_definition(definition)
    now = datetime.now(UTC)
    from sqlalchemy import select

    with make_session_factory(database_url)() as session:
        for row in session.scalars(
            select(WorkflowDefinitionVersionRecord).where(
                WorkflowDefinitionVersionRecord.workflow_id == "daily-report-generation"
            )
        ):
            row.status = "superseded"
        session.add(
            WorkflowDefinitionVersionRecord(
                workflow_id="daily-report-generation",
                version=version,
                definition=definition,
                schema_version=definition["schema_version"],
                status="published",
                content_hash=content_hash(definition),
                created_at=now,
                published_at=now,
            )
        )
        session.commit()


def _renamed_definition() -> dict:
    """The same registered building blocks, wired under different names and in a different order."""
    return {
        "schema_version": 1,
        "outputs": {"body": "final.body", "source_refs": "gather.source_refs"},
        "nodes": [
            {"id": "gather", "type": "operation.query", "operation": "work_record.list", "inputs": []},
            {"id": "compose", "type": "template.render", "template_ref": "daily-report-v1", "inputs": ["gather"]},
            {
                "id": "write",
                "type": "llm.generate",
                "provider_profile": DAILY_REPORT_PROVIDER_PROFILE,
                "inputs": ["compose"],
            },
            {"id": "final", "type": "output.validate", "schema": "daily-report-body-v1", "inputs": ["write"]},
        ],
    }


def test_a_renamed_graph_runs_without_touching_the_runtime(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    _publish(database_url, "2", _renamed_definition())

    generated = client.post("/api/daily-reports/generate-draft", headers=MINA, json={"report_date": "2026-09-06"})
    assert generated.status_code in {200, 201}, generated.text
    body = generated.json()
    assert body["workflow_state"] == "completed"
    assert body["body"] == "오늘 한 일을 정리했습니다."

    # The run records the nodes the definition declared, under the names it gave them.
    with make_session_factory(database_url)() as session:
        run = session.get(WorkflowRunRecord, UUID(body["workflow_run_id"]))
        from sqlalchemy import select as _select

        executions = list(
            session.scalars(
                _select(WorkflowNodeExecutionRecord)
                .where(WorkflowNodeExecutionRecord.run_id == run.id)
                .order_by(WorkflowNodeExecutionRecord.created_at)
            )
        )
    assert [row.node_id for row in executions] == ["gather", "compose", "write", "final"]
    assert {row.state for row in executions} == {"completed"}


def test_the_answer_comes_from_the_definition_not_from_a_hardcoded_node_name(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    definition = _renamed_definition()
    definition["outputs"] = {"body": "final.body", "source_refs": "gather.source_refs"}
    _publish(database_url, "3", definition)

    body = client.post("/api/daily-reports/generate-draft", headers=MINA, json={"report_date": "2026-09-06"}).json()
    assert body["body"] == "오늘 한 일을 정리했습니다."
    assert isinstance(body["source_refs"], list)


def test_only_registered_building_blocks_may_be_named(tmp_path) -> None:
    """The definition decides the wiring; it never decides what the runtime is willing to do."""
    with pytest.raises(ValueError):
        validate_definition({
            "schema_version": 1,
            "nodes": [{"id": "danger", "type": "process.spawn", "inputs": []}],
        })
    with pytest.raises(ValueError):
        validate_definition({
            "schema_version": 1,
            "nodes": [{"id": "sources", "type": "operation.query", "operation": "secrets.read", "inputs": []}],
        })
    # An output that points at a node nobody declared is refused before anything runs.
    with pytest.raises(ValueError):
        validate_definition({
            "schema_version": 1,
            "outputs": {"body": "nowhere.body"},
            "nodes": [{"id": "sources", "type": "operation.query", "operation": "work_record.list", "inputs": []}],
        })
