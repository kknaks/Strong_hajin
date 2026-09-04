from sqlalchemy import create_engine, select

from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import WorkflowDefinitionVersionRecord


def test_product_reset_installs_only_the_daily_report_generation_definition(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    with create_engine(database_url).connect() as connection:
        versions = connection.execute(select(WorkflowDefinitionVersionRecord.version)).scalars().all()
    assert versions == ["1"]
