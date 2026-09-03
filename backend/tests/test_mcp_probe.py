from ax.mcp_probe import run_protocol_probe
from ax.reset_demo import reset_database
from ax.settings import RuntimeProfile, Settings


def test_mcp_protocol_probe_records_discovery_structured_output_and_a_golden_flow(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)

    report = run_protocol_probe(Settings(RuntimeProfile.TEST, database_url))

    assert report["validation"]["tool_discovery"] is True
    assert report["validation"]["structured_output"] is True
    assert report["validation"]["golden_flow"] is True
    assert report["measurements_ms"]["tool_discovery"] >= 0
