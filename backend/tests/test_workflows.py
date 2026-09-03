import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select

from ax_workspace.entrypoints.http import create_app
from ax_workspace.platform.persistence import WorkflowDefinitionVersionRecord
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.ax_execution.domain import WorkflowDefinitionVersion, WorkflowEdge, WorkflowNode, NodeKind, catalog_definitions


def test_catalog_contains_nine_version_pinned_valid_definitions() -> None:
    definitions = catalog_definitions()
    assert len(definitions) == 9
    assert {definition.version for definition in definitions} == {"2026-09-demo.1"}
    assert next(item for item in definitions if item.workflow_id == "contract-review").nodes[4].join_policy == "all"


def test_graph_rejects_unknown_edge_target() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        WorkflowDefinitionVersion(
            workflow_id="invalid",
            version="2026-09-demo.1",
            title="Invalid",
            description="Invalid graph",
            required_scope="scax",
            input_schema={},
            nodes=(
                WorkflowNode(id="start", kind=NodeKind.START, label="start"),
                WorkflowNode(id="end", kind=NodeKind.END, label="end"),
            ),
            edges=(WorkflowEdge(source="start", target="missing"),),
        )


def test_personas_see_different_capability_filtered_catalogs(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    mina = client.get("/api/catalog", headers={"X-Demo-Persona": "mina"}).json()
    admin = client.get("/api/catalog", headers={"X-Demo-Persona": "demo-admin"}).json()
    assert len(mina) < len(admin) == 9
    assert "contract-review" not in {item["workflow_id"] for item in mina}
    contract = next(item for item in admin if item["workflow_id"] == "contract-review")
    assert contract["input_schema"]["required"] == ["contract_id"]


def test_explicit_reset_creates_and_seeds_the_nine_definition_versions(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    with create_engine(database_url).connect() as connection:
        versions = connection.execute(select(WorkflowDefinitionVersionRecord.version)).scalars().all()
    assert versions == ["2026-09-demo.1"] * 9
