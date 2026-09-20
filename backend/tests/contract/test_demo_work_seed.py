"""The browser's read APIs see fixtures after the actual reset-demo entry point."""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.seed import DEMO_PASSWORD, demo_email
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import main, reset_database
from ax_workspace.platform.persistence import TaskRecord, MemberRecord, make_session_factory
from sqlalchemy import func, select


def test_reset_demo_populates_browser_reads_and_rebuilds_from_scratch(tmp_path, monkeypatch):
    database = tmp_path / "demo.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("AX_PROFILE", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    assert not database.exists()

    # A second invocation must replace rather than duplicate the fixtures.
    for _ in range(2):
        before = datetime.now(UTC).date()
        main([])
        after = datetime.now(UTC).date()
        with TestClient(create_app(Settings(RuntimeProfile.TEST, url, materials_dir=str(tmp_path / "materials")))) as client:
            login = client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})
            assert login.status_code == 200, login.text

            def read(path):
                response = client.get(path)
                assert response.status_code == 200, response.text
                return response.json()

            mine = {row["title"]: row for row in read("/api/my-work")}
            assert mine["제품 사용성 조사 정리"]["state"] == "in_progress"
            assert mine["연동 규격 확인"]["state"] == "blocked"
            assert mine["지난주 고객 의견 분류"]["due_date"] < before.isoformat()
            assert "다음 배포 안내 검토 요청" not in mine  # No invented acceptance.

            # CalendarPage merges these same two APIs; there is no separate calendar endpoint.
            all_tasks = {row["title"]: row for row in read("/api/tasks?include_closed=true")}
            with make_session_factory(url)() as session:
                assert session.scalar(select(func.count()).select_from(TaskRecord)) == 12
            assert all_tasks["제품 안내 문구 정리"]["state"] == "done"
            calendar = all_tasks["오늘 데모 점검"]
            assert before.isoformat() <= calendar["start_date"] <= after.isoformat()
            assert calendar["start_date"] == calendar["due_date"]
            assert mine["제품 사용성 조사 정리"]["due_date"] == (
                datetime.fromisoformat(calendar["due_date"]).date() + timedelta(days=2)
            ).isoformat()

            sent = [row for row in read("/api/work-requests") if row["requester_id"] == "mina"]
            assert {row["title"] for row in sent} == {"제품 지표 검토 요청", "지표 정의 결과 확인"}
            inbox = read("/api/work-requests/inbox")
            work = [row for row in inbox if row["category"] == "work"]
            references = [row for row in inbox if row["category"] == "reference"]
            assert [row["title"] for row in work] == ["다음 배포 안내 검토 요청"]
            assert work[0]["state"] == "pending" and work[0]["task_id"]
            assert {(row["title"], row["state"]) for row in references} == {
                ("제품 운영 점검 참조", "pending"), ("분기 계획 검토 참조", "accepted"),
            }
            pending = all_tasks["제품 지표 검토 요청"]
            assert pending["derived"]["assignment"] == "awaiting_acceptance"

            assert mine["사용성 조사 결과 보고"]["derived"]["approval"] == "awaiting_review"
            revised = mine["배포 체크리스트 보완"]
            assert revised["state"] == "in_progress"
            assert revised["derived"]["approval"] == "awaiting_revision"
            detail = read(f"/api/tasks/{revised['task_id']}")
            assert detail["delivery"]["last_reason"] == "실패 시 복구 절차를 추가해 주세요."

            actions = read("/api/action-items")
            delivery = next(row for row in actions if row["kind"] == "task.delivery")
            assert delivery["subject"] == "지표 정의 결과 확인"
            assert delivery["waiting_on"]["member_id"] == "mina"
            assert {command["id"] for command in delivery["allowed_commands"]} == {"accept", "request_changes"}
            assert any(row["subject"] == "다음 배포 안내 검토 요청" for row in actions)


@pytest.mark.parametrize("catalog_only", [False, True])
def test_demo_work_does_not_leak_into_shared_reset_or_catalog_only(tmp_path, monkeypatch, catalog_only):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    if catalog_only:
        monkeypatch.setenv("AX_PROFILE", "test")
        monkeypatch.setenv("DATABASE_URL", url)
        main(["--catalog-only"])
    else:
        reset_database(url)
    with make_session_factory(url)() as session:
        assert session.scalar(select(func.count()).select_from(TaskRecord)) == 0
        assert session.scalar(select(func.count()).select_from(MemberRecord)) == (0 if catalog_only else 6)
