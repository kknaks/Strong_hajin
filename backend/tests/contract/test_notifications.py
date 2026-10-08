"""알림 API — 목록 · 요약 · 읽음 · 모두 읽음 · 점 · 설정 (SPEC-011 §4.4 · §4.5 · WORK-013 WP2-BE).

사건 × 관계 68행은 `test_notification_rows.py` 가 지킨다. 여기는 이미 선 알림을 받는 사람이 보는 쪽의 계약이다.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from legacy_acceptance import make_request_look_pending

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import NotificationRecord, make_session_factory


MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, settings


def _send(client: TestClient, title: str) -> dict:
    made = client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"})
    assert made.status_code == 201, made.text
    return made.json()


def test_work_request_delivery_and_acceptance_notify_the_other_party_once(tmp_path) -> None:
    """흡수(D-30) — 받은 쪽 W01 · 수락 W05 가 새 종류로 선다. 수락은 옛 행 모양(업무 없이 기다리던 요청)에도 붙는다."""
    client, _, settings = _stack(tmp_path)
    request = _send(client, "배포 체크 요청")
    [received] = client.get("/api/notifications", headers=JIHO).json()["items"]
    assert received["kind"] == "work.request_received" and received["relation"] == "assignee"
    assert received["subject"] == {"type": "work_request", "id": request["request_id"], "title": "배포 체크 요청"}
    assert received["actor"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert set(received) == {
        "notification_id", "seq", "kind", "theme", "item", "relation", "failure", "actor", "subject", "data", "target",
        "created_at", "updated_at", "read_at",
    }

    make_request_look_pending(settings.database_url, request["request_id"])
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept", headers=JIHO, json={"expected_version": request["version"]}
    )
    assert accepted.status_code == 200, accepted.text
    [answered] = client.get("/api/notifications", headers=MINA).json()["items"]
    assert answered["kind"] == "work.request_answered" and answered["data"] == {"answer": "accepted"}
    assert answered["relation"] == "requester" and answered["theme"] == "work" and answered["item"] == "answer"


def test_the_list_is_newest_seq_first_with_theme_filter_and_server_cursor(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    for index in range(5):
        _send(client, f"요청 {index}")
    first = client.get("/api/notifications", headers=JIHO, params={"limit": 2}).json()
    assert [row["subject"]["title"] for row in first["items"]] == ["요청 4", "요청 3"] and first["next_cursor"]
    second = client.get("/api/notifications", headers=JIHO, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [row["subject"]["title"] for row in second["items"]] == ["요청 2", "요청 1"]
    last = client.get("/api/notifications", headers=JIHO, params={"limit": 2, "cursor": second["next_cursor"]}).json()
    assert [row["subject"]["title"] for row in last["items"]] == ["요청 0"] and last["next_cursor"] is None
    assert len(client.get("/api/notifications", headers=JIHO, params={"theme": "work"}).json()["items"]) == 5
    assert client.get("/api/notifications", headers=JIHO, params={"theme": "meeting"}).json() == {"items": [], "next_cursor": None}
    assert len(client.get("/api/notifications", headers=JIHO).json()["items"]) == 5  # 기본 30


def test_bad_queries_are_422(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    for params in ({"theme": "chat"}, {"limit": 0}, {"limit": 101}, {"cursor": "not-ours"}, {"cursor": "czEy" + "!"}):
        assert client.get("/api/notifications", headers=JIHO, params=params).status_code == 422, params


def test_summary_read_and_read_all_count_every_theme(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    for index in range(3):
        _send(client, f"요청 {index}")
    assert client.get("/api/notifications/summary", headers=JIHO).json() == {"unread": {"all": 3, "work": 3, "message": 0, "meeting": 0}}
    rows = client.get("/api/notifications", headers=JIHO).json()["items"]
    read = client.post(f"/api/notifications/{rows[0]['notification_id']}/read", headers=JIHO)
    assert read.status_code == 200 and read.json()["read_at"] is not None
    again = client.post(f"/api/notifications/{rows[0]['notification_id']}/read", headers=JIHO)
    assert again.json()["read_at"] == read.json()["read_at"]  # 멱등
    assert client.get("/api/notifications/summary", headers=JIHO).json()["unread"]["all"] == 2
    # 남의 알림 · 없는 알림은 404
    assert client.post(f"/api/notifications/{rows[1]['notification_id']}/read", headers=MINA).status_code == 404
    assert client.post(f"/api/notifications/{uuid4()}/read", headers=JIHO).status_code == 404
    # 모두 읽음 — 본문 없음 · 그 회원의 안 읽은 알림 **전부**(D-40)
    assert client.post("/api/notifications/read-all", headers=JIHO).json() == {"read": 2}
    assert client.get("/api/notifications/summary", headers=JIHO).json()["unread"]["all"] == 0
    assert client.post("/api/notifications/read-all", headers=JIHO).json() == {"read": 0}


def test_badges_are_two_dots(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    assert client.get("/api/me/badges", headers=JIHO).json() == {"notifications": False, "inbox": False}
    _send(client, "점")
    assert client.get("/api/me/badges", headers=JIHO).json() == {"notifications": True, "inbox": False}
    client.post("/api/notifications/read-all", headers=JIHO)
    assert client.get("/api/me/badges", headers=JIHO).json()["notifications"] is False


def test_a_row_stays_with_its_stored_title_when_its_source_can_no_longer_be_opened(tmp_path) -> None:
    """읽기 인가 변경(§4.5-2-4) — 줄은 남고 `target` 만 null. 제목은 만들 때의 값이다."""
    client, _, settings = _stack(tmp_path)
    request = _send(client, "처음 제목")
    with make_session_factory(settings.database_url)() as session:
        row = session.query(NotificationRecord).filter_by(recipient_member_id="jiho").one()
        row.target = {"surface": "work", "task_id": str(uuid4())}  # 지금은 열 수 없는 대상
        session.commit()
    [item] = client.get("/api/notifications", headers=JIHO).json()["items"]
    assert item["target"] is None and item["subject"]["title"] == "처음 제목" and item["subject"]["id"] == request["request_id"]


def test_settings_default_save_conflict_and_exact_shape(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    current = client.get("/api/me/notification-settings", headers=JIHO).json()
    assert current["version"] == 0 and current["enabled"] is True
    assert {theme: sorted(group["items"]) for theme, group in current["themes"].items()} == {
        "work": sorted(["request", "assign", "answer", "report", "rework", "change", "comment", "unblock"]),
        "message": sorted(["mail", "slack", "kakao"]),
        "meeting": sorted(["invite", "change", "minutes", "minutes-fail", "share"]),
    }
    assert current["themes"]["work"]["items"]["comment"] is False  # 기본 꺼짐 — 시안 그대로
    assert sum(len(group["items"]) for group in current["themes"].values()) == 16
    current["themes"]["work"]["on"] = False
    saved = client.put("/api/me/notification-settings", headers=JIHO, json=current)
    assert saved.status_code == 200 and saved.json()["version"] == 1
    assert saved.json()["themes"]["work"]["items"]["request"] is True  # 상위를 꺼도 아래 값은 남는다(D-18)
    stale = client.put("/api/me/notification-settings", headers=JIHO, json=current)  # 옛 version 0
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "SETTINGS_VERSION_CONFLICT"
    fresh = client.get("/api/me/notification-settings", headers=JIHO).json()
    missing = {**fresh, "themes": {**fresh["themes"], "meeting": {"on": True, "items": {"invite": True}}}}
    assert client.put("/api/me/notification-settings", headers=JIHO, json=missing).status_code == 422
    extra = {**fresh, "themes": {**fresh["themes"], "chat": {"on": True, "items": {}}}}
    assert client.put("/api/me/notification-settings", headers=JIHO, json=extra).status_code == 422
    not_bool = {**fresh, "enabled": "yes"}
    assert client.put("/api/me/notification-settings", headers=JIHO, json=not_bool).status_code == 422
    assert client.put("/api/me/notification-settings", headers=JIHO, json={k: v for k, v in fresh.items() if k != "version"}).status_code == 422
    # 저장은 회원마다 한 벌 — 남의 설정은 그대로 기본값
    assert client.get("/api/me/notification-settings", headers=MINA).json()["version"] == 0


def test_legacy_rows_read_as_the_absorbed_kinds(tmp_path) -> None:
    """마이그레이션 전 옛 행(`work_request.received` · 테마 빈 칸)도 새 종류로 보인다(D-30)."""
    from datetime import UTC, datetime

    client, _, settings = _stack(tmp_path)
    request = _send(client, "옛 행")
    with make_session_factory(settings.database_url)() as session:
        session.query(NotificationRecord).delete()
        session.add(NotificationRecord(
            recipient_member_id="jiho", source_kind="work_request_audit_event", source_id="legacy-1", kind="work_request.received",
            resource_type="work_request", resource_id=request["request_id"], resource_title="옛 행", actor_member_id="mina",
            safe_summary="", created_at=datetime.now(UTC), seq=1, updated_at=datetime.now(UTC),
        ))
        session.commit()
    [item] = client.get("/api/notifications", headers=JIHO, params={"theme": "work"}).json()["items"]
    assert (item["kind"], item["theme"], item["item"], item["relation"]) == ("work.request_received", "work", "request", "assignee")
    assert item["target"] == {"surface": "work", "work_request_id": request["request_id"]}
    assert client.get("/api/notifications/summary", headers=JIHO).json()["unread"]["work"] == 1
    assert UUID(item["notification_id"])
