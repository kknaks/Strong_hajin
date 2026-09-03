import os

import pytest

from ax.demo_rehearsal import run_golden_rehearsal
from ax.reset_demo import reset_database
from ax.settings import RuntimeProfile, Settings


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
