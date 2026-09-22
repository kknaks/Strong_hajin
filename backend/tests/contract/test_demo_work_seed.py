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
                # 업무·캘린더·수신함의 12 + 「프로젝트」 화면의 11.
                assert session.scalar(select(func.count()).select_from(TaskRecord)) == 23
            assert all_tasks["제품 안내 문구 정리"]["state"] == "done"
            calendar = all_tasks["오늘 데모 점검"]
            assert before.isoformat() <= calendar["start_date"] <= after.isoformat()
            assert calendar["start_date"] == calendar["due_date"]
            assert mine["제품 사용성 조사 정리"]["due_date"] == (
                datetime.fromisoformat(calendar["due_date"]).date() + timedelta(days=2)
            ).isoformat()

            sent = [row for row in read("/api/work-requests") if row["requester_id"] == "mina"]
            assert {row["title"] for row in sent} == {
                "제품 지표 검토 요청", "지표 정의 결과 확인",
                # 3층 트리의 1단계는 **실제 요청**이다 — 직접 배정으로 흉내내지 않는다.
                "보고서 디자인",
            }
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


def test_reset_demo_seeds_every_hard_spot_of_the_project_screen(tmp_path, monkeypatch):
    """「프로젝트」 화면의 어려운 자리가 **전부 한 번씩** 서 있는가 — 사용자가 이 데이터로 브라우저를 본다.

    A 프로젝트 둘 · B 3층 트리 · C 의존선 세 종류 · D 진행률 네 경우 · E 기간 네 갈래 · F 상태와 담당.
    빠지면 화면의 그 자리가 빈 채로 뜨고, 사람이 눌러 보기 전에는 아무도 모른다.
    """
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    monkeypatch.setenv("AX_PROFILE", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    main([])

    with TestClient(create_app(Settings(RuntimeProfile.TEST, url, materials_dir=str(tmp_path / "materials")))) as client:
        assert client.post(
            "/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD}
        ).status_code == 200

        def read(path):
            response = client.get(path)
            assert response.status_code == 200, response.text
            return response.json()

        # --- A. 프로젝트 둘. 민아가 둘 다 읽고, 하나는 **업무가 하나도 없다** ---
        projects = {row["name"]: row for row in read("/api/projects")}
        assert set(projects) == {"하반기 제품 개편", "브랜드 리뉴얼"}
        empty = read(f"/api/projects/{projects['브랜드 리뉴얼']['project_id']}")
        assert empty["tasks"] == [], "빈 본문·빈 간트를 볼 프로젝트가 비어 있지 않습니다"
        assert "mina" in {row["member_id"] for row in empty["members"]}

        detail = read(f"/api/projects/{projects['하반기 제품 개편']['project_id']}")
        assert {row["member_id"] for row in detail["members"]} == {"mina", "jiho"}
        rows = {row["title"]: row for row in detail["tasks"]}
        ids = {row["task_id"]: row["title"] for row in detail["tasks"]}

        def parent(title):
            return ids.get(rows[title]["parent_task_id"])

        def after(title):
            return {ids[item] for item in rows[title]["preceding_task_ids"]}

        # --- B. 3층 트리. 손자는 **상속으로** 프로젝트에 들어온다 ---
        assert parent("보고서 작성") is None
        assert parent("보고서 디자인") == "보고서 작성"
        assert parent("디자인 시안 조사") == "보고서 디자인"
        # 1단계는 **요청 발송 → 수락**이 세웠다. 직접 배정은 프로젝트를 싣지 못한다.
        design = read(f"/api/tasks/{rows['보고서 디자인']['task_id']}")
        assert design["origin"]["kind"] == "work_request"
        assert rows["보고서 디자인"]["assignee"]["member_id"] == "jiho"
        # 손자는 프로젝트를 말하지 않았는데도 이 프로젝트의 업무 목록에 있다.
        assert read(f"/api/tasks/{rows['디자인 시안 조사']['task_id']}")["project_id"] == detail["project_id"]

        # --- C. 의존선 셋 ---
        # 손자의 선행이 **다른 가지**에 있다 — 접으면 닻이 접힌 부모 바에 붙는다.
        assert after("디자인 시안 조사") == {"사용자 리서치 설문"}
        assert parent("사용자 리서치 설문") is None
        # **같은 접힌 가지 안쪽** — 형제끼리. 접으면 선이 아니라 건수로 나온다.
        assert after("시안 후보 정리") == {"디자인 시안 조사"}
        assert parent("시안 후보 정리") == parent("디자인 시안 조사") == "보고서 디자인"
        # 평범한 선 둘 — 최상위끼리.
        assert after("개편 범위 확정") == {"사용자 리서치 설문"}
        assert after("경쟁 제품 비교") == {"개편 범위 확정"}

        # --- D. 진행률 네 경우 ---
        assert rows["디자인 시안 조사"]["checklist_progress"] == {"done": 2, "total": 5}
        # **`total == 0` 은 0% 가 아니다** — fill 도 % 도 나지 않는 자리.
        assert rows["시안 후보 정리"]["checklist_progress"] == {"done": 0, "total": 0}
        assert rows["사용자 리서치 설문"]["state"] == "done"
        assert {ids[row["parent_task_id"]] for row in detail["tasks"] if row["parent_task_id"]} == {
            "보고서 작성", "보고서 디자인",
        }

        # --- E. 기간 네 갈래 ---
        assert (rows["보고서 작성"]["span_from"], rows["보고서 작성"]["span_to"]) != (None, None)
        assert rows["보고서 작성"]["span_from"] != rows["보고서 작성"]["span_to"]
        # 마감만 있는 업무는 **그 날 하루**로 접혀 온다 — 원값은 그대로 함께 온다.
        one_day = rows["경쟁 제품 비교"]
        assert one_day["start_date"] is None and one_day["due_date"]
        assert one_day["span_from"] == one_day["span_to"] == one_day["due_date"]
        # 기간이 아예 없는 업무 — 간트에 안 서고 좌 레일에만.
        assert (rows["접근성 점검"]["span_from"], rows["접근성 점검"]["span_to"]) == (None, None)
        # 기한이 지난 업무 — 지연 칸이 세는 것은 **값이 온 줄**뿐이다.
        assert rows["로그인 화면 개선"]["overdue_days"] and rows["로그인 화면 개선"]["overdue_days"] > 0
        assert [row["title"] for row in detail["tasks"] if row["overdue_days"]] == ["로그인 화면 개선"]

        # --- F. 상태 다섯과 담당 없음 ---
        assert {
            rows["경쟁 제품 비교"]["state"], rows["개편 범위 확정"]["state"], rows["결제 연동 점검"]["state"],
            rows["사용자 리서치 설문"]["state"], rows["구형 브라우저 대응"]["state"],
        } == {"open", "in_progress", "blocked", "done", "cancelled"}
        # 취소는 **하나뿐이다** — 취소선 바가 하나이고, 요약 모수에서 그 하나가 빠진다.
        assert [row["title"] for row in detail["tasks"] if row["state"] == "cancelled"] == ["구형 브라우저 대응"]
        # 막힘은 사유가 함께 선다.
        assert read(f"/api/tasks/{rows['결제 연동 점검']['task_id']}")["block_reason"]
        # **담당 없는 업무가 정상이다** — 서버가 「미정」을 지어내지 않는다.
        assert rows["접근성 점검"]["assignee"] is None
        assert [row["title"] for row in detail["tasks"] if row["assignee"] is None] == ["접근성 점검"]


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
