"""조직 화면이 한 사람을 여섯 축으로 읽는다 — 그리고 축마다 지나온 기간을 되짚는다.

SPEC-005 §2: 재직·소속·보직·직급·직무·권한은 서로 다른 사실이고 각각의 기간을 보존한다. 화면이 그것을 그리려면
현재값만으로는 모자라다. 여기서 여는 것은 **읽기뿐**이다 — 바꾸는 command는 따로 온다.

같은 절이 또 하나를 말한다: 결과 field는 현재 Principal의 권한에 맞게 제한한다. 계층·소속·직책·직급·직무·재직은
명부와 같은 기준으로 로그인한 누구에게나 열리고, 연락처와 권한은 본인이거나 그 사람을 관리할 수 있는 사람에게만
값이 온다. 감추는 방법으로 field를 지우지는 않는다 — 자리는 그대로 두고 값을 비운다.
"""

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import MemberRecord, make_session_factory

YUNA = {"X-Demo-Persona": "yuna"}      # 대표 — 조직 전체에 organization.manage
MINA = {"X-Demo-Persona": "mina"}      # 제품팀 구성원 — 관리 권한 없음
JIHO = {"X-Demo-Persona": "jiho"}      # 제품팀 팀장


def _client(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url))), database_url


def test_one_member_read_as_six_axes_at_once(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    # 연락처는 원문이 말할 때만 있는 것이라, 있는 경우를 만들어 두고 누구에게 보이는지를 본다.
    with make_session_factory(database_url)() as session:
        member = session.get(MemberRecord, "mina")
        member.phone, member.birth_date = "010-0000-0000", None
        session.commit()

    assert client.get("/api/organization/members/mina").status_code == 401
    answer = client.get("/api/organization/members/mina", headers=MINA)
    assert answer.status_code == 200, answer.text
    body = answer.json()

    assert body["member_id"] == "mina" and body["employment_state"] == "active"
    assert body["has_account"] is True
    # 계층은 회사에서 그 사람 자리까지 내려오는 길이다.
    assert [unit["unit_id"] for unit in body["hierarchy_path"]] == ["scax", "product-division", "platform-office", "product"]
    assert all({"unit_id", "name", "unit_type"} <= set(unit) for unit in body["hierarchy_path"])
    assert any(item["unit_id"] == "product" and item["kind"] == "primary" for item in body["memberships"])
    assert all({"unit_id", "unit_name", "kind", "valid_from", "valid_until"} <= set(item) for item in body["memberships"])
    assert body["grade"]["name"] == "대리" and [job["name"] for job in body["jobs"]] == ["기획"]
    # 직책 축은 직책을 말한다 — 역할을 조직에 붙들어 두는 발령 행은 여기 오지 않는다.
    assert body["appointments"] == []


def test_who_may_see_the_contact_and_the_access_axes(tmp_path) -> None:
    """본인과 그 사람을 관리할 수 있는 사람에게만 값이 온다. 나머지에게는 자리만 온다."""
    client, database_url = _client(tmp_path)
    with make_session_factory(database_url)() as session:
        session.get(MemberRecord, "mina").phone = "010-0000-0000"
        session.commit()

    mine = client.get("/api/organization/members/mina", headers=MINA).json()
    assert mine["phone"] == "010-0000-0000" and mine["grants"]

    manager = client.get("/api/organization/members/mina", headers=YUNA).json()
    assert manager["phone"] == "010-0000-0000" and manager["grants"]

    # 같은 팀 팀장이라도 조직 관리 권한이 아니면 남의 연락처와 권한은 보이지 않는다.
    stranger = client.get("/api/organization/members/mina", headers=JIHO)
    assert stranger.status_code == 200, stranger.text
    other = stranger.json()
    assert set(other) == set(mine), "권한이 없다고 응답의 모양이 달라지면 client가 모양으로 권한을 추측하게 된다"
    assert other["phone"] is None and other["birth_date"] is None
    assert other["grants"] == [] and other["revoked_grants"] == []
    # 기본 축은 그대로 보인다 — 명부와 같은 기준이다.
    assert other["memberships"] == mine["memberships"] and other["grade"] == mine["grade"]


def test_a_revoked_grant_is_still_something_the_record_can_say(tmp_path) -> None:
    """회수는 지우는 것이 아니다. 지금 닿지 않는다는 사실과 언제 거두었는지가 함께 남는다."""
    client, _ = _client(tmp_path)
    granted = client.post(
        "/api/access/grants",
        headers=YUNA,
        json={"member_id": "mina", "role_id": "role:team-lead", "scope_ref": "product", "reason": "팀장 대행"},
    )
    assert granted.status_code == 201, granted.text
    grant_id = granted.json()["grant_id"]
    assert client.post(f"/api/access/grants/{grant_id}/revoke", headers=YUNA, json={"reason": "대행 종료"}).status_code == 200

    body = client.get("/api/organization/members/mina", headers=YUNA).json()
    assert "role:team-lead" not in {item["role_id"] for item in body["grants"]}
    revoked = [item for item in body["revoked_grants"] if item["role_id"] == "role:team-lead"]
    assert len(revoked) == 1
    assert revoked[0]["scope_ref"] == "product" and revoked[0]["scope_name"] == "제품팀"
    assert revoked[0]["revoked_at"] and revoked[0]["valid_from"]


def test_each_axis_can_be_read_back_as_the_periods_it_went_through(tmp_path) -> None:
    client, _ = _client(tmp_path)
    for axis in ("membership", "appointment", "grade", "job", "grant"):
        answer = client.get(f"/api/organization/members/jiho/history?axis={axis}", headers=YUNA)
        assert answer.status_code == 200, f"{axis}: {answer.text}"
        rows = answer.json()
        assert all({"value", "valid_from", "valid_until", "reason"} <= set(row) for row in rows), axis
    memberships = client.get("/api/organization/members/jiho/history?axis=membership", headers=YUNA).json()
    # 지금 값도 이력의 한 행이다 — 아직 끝나지 않은 기간이다.
    assert {row["unit_name"] for row in memberships} == {"제품팀", "SCAX"}
    assert all(row["valid_until"] is None for row in memberships)
    appointments = client.get("/api/organization/members/jiho/history?axis=appointment", headers=YUNA).json()
    assert appointments and appointments[0]["value"] == "팀장"

    # 본인은 자기 이력을 본다. 남의 이력은 관리할 수 있는 사람만 본다.
    assert client.get("/api/organization/members/jiho/history?axis=membership", headers=JIHO).status_code == 200
    assert client.get("/api/organization/members/jiho/history?axis=membership", headers=MINA).status_code == 404
    assert client.get("/api/organization/members/jiho/history?axis=없는축", headers=YUNA).status_code == 422


def test_the_change_record_reads_as_axes_and_only_for_someone_who_administers(tmp_path) -> None:
    client, _ = _client(tmp_path)
    granted = client.post(
        "/api/access/grants",
        headers=YUNA,
        json={"member_id": "mina", "role_id": "role:team-lead", "scope_ref": "product", "reason": "팀장 대행"},
    )
    client.post(f"/api/access/grants/{granted.json()['grant_id']}/revoke", headers=YUNA, json={"reason": "대행 종료"})

    answer = client.get("/api/organization/activity", headers=YUNA)
    assert answer.status_code == 200, answer.text
    events = answer.json()
    assert len(events) >= 2
    assert all({"occurred_at", "axis", "summary", "reason", "actor_id", "actor_name", "target_id"} <= set(e) for e in events)
    assert {e["axis"] for e in events} == {"권한"}
    assert events[0]["occurred_at"] >= events[-1]["occurred_at"], "최신순이어야 한다"
    assert events[0]["actor_name"] and events[0]["actor_id"] == "yuna"
    assert "대행 종료" in {e["reason"] for e in events}

    # 업무와 요청의 사건은 조직 축이 아니다 — 여기 섞이지 않는다.
    assert all(not e["axis"].startswith("업무") for e in events)
    # 한 조직으로 좁히면 그 아래 사람의 사건만 남는다.
    scoped = client.get("/api/organization/activity?unit_id=product", headers=YUNA).json()
    assert {e["target_id"] for e in scoped} == {"mina"}
    assert client.get("/api/organization/activity?unit_id=legal", headers=YUNA).json() == []
    # 조직을 관리하지 못하는 사람에게는 열리지 않는다.
    assert client.get("/api/organization/activity", headers=MINA).status_code == 403
    assert client.get("/api/organization/activity").status_code == 401


def test_the_directory_says_who_can_actually_sign_in(tmp_path) -> None:
    """계정이 있다는 것은 권한이 아니라 들어올 문이 있다는 뜻이다 — 화면의 「계정 없음」 배지가 이것을 읽는다."""
    client, database_url = _client(tmp_path)
    rows = {row["id"]: row for row in client.get("/api/organization/members", headers=MINA).json()}
    assert rows["mina"]["has_account"] is True

    with make_session_factory(database_url)() as session:
        session.get(MemberRecord, "sora").account_ref = None
        session.commit()
    after = {row["id"]: row for row in client.get("/api/organization/members", headers=MINA).json()}
    assert after["sora"]["has_account"] is False and after["mina"]["has_account"] is True
