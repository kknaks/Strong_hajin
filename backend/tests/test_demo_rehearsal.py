from ax.demo_rehearsal import run_golden_rehearsal
from ax.reset_demo import reset_database
from ax.settings import RuntimeProfile, Settings


def test_golden_rehearsal_completes_daily_meeting_and_contract_flows(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)

    report = run_golden_rehearsal(Settings(RuntimeProfile.TEST, database_url))

    assert set(report["flows"]) == {"daily_report", "meeting_followups", "contract_review"}
    assert all(flow["state"] == "completed" for flow in report["flows"].values())
    assert report["flows"]["meeting_followups"]["my_work_state"] == "active"
    assert report["flows"]["contract_review"]["effect_after_legal"] is False


def test_golden_rehearsal_remains_valid_when_prior_active_demo_work_exists(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)

    run_golden_rehearsal(settings)
    repeated = run_golden_rehearsal(settings)

    assert repeated["flows"]["meeting_followups"]["state"] == "completed"
