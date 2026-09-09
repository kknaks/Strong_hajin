"""The organization ledger follows the ERD: typed units in a hierarchy, positions, grades, jobs, and rules."""
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    MemberRecord,
    RoleCapabilityRecord,
    AccessGrantRecord,
    PositionDefinitionRecord,
    StandardGrantRuleRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}


def _client(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url))), database_url


def test_tree_exposes_typed_hierarchy_leaders_and_counts(tmp_path) -> None:
    client, _ = _client(tmp_path)
    assert client.get("/api/organization/tree").status_code == 401
    tree = {unit["id"]: unit for unit in client.get("/api/organization/tree", headers=MINA).json()}
    assert tree["scax"]["parent_id"] is None and tree["scax"]["unit_type"] == "회사"
    assert tree["product"]["parent_id"] == "platform-office" and tree["product"]["unit_type"] == "팀"
    assert tree["infra-part"]["parent_id"] == "engineering" and tree["infra-part"]["unit_type"] == "파트"
    assert tree["legal"]["parent_id"] == "management-division"
    # 인사 계열 팀은 본부 바로 아래에 있어도 된다: 깊이와 종류는 별개다.
    assert tree["people"]["parent_id"] == "management-division"
    assert tree["scax"]["member_count"] >= tree["product"]["member_count"] >= 2
    assert any(leader["position"] == "팀장" for leader in tree["product"]["leaders"])


def test_unit_members_show_membership_position_grade_and_job(tmp_path) -> None:
    client, _ = _client(tmp_path)
    members = {item["member_id"]: item for item in client.get("/api/organization/units/product/members", headers=MINA).json()}
    assert {"mina", "jiho"} <= set(members)
    jiho = members["jiho"]
    assert jiho["grade"] == "차장" and jiho["jobs"] == ["기획"]
    assert jiho["positions"] == [{"position": "팀장", "organization_name": "제품팀", "kind": "primary"}]
    assert any(item["organization_id"] == "product" and item["kind"] == "primary" for item in jiho["memberships"])
    assert members["mina"]["positions"] == []
    # 상위 조직을 고르면 하위 조직 구성원까지 함께 보인다.
    company = {item["member_id"] for item in client.get("/api/organization/units/scax/members", headers=MINA).json()}
    assert {"mina", "jiho", "sora", "minseok"} <= company


def test_positions_rules_and_grants_carry_erd_provenance(tmp_path) -> None:
    _, database_url = _client(tmp_path)
    with make_session_factory(database_url)() as session:
        positions = {item.id: item for item in session.scalars(select(PositionDefinitionRecord))}
        assert positions["team-lead"].organization_unit_type_id == "team" and positions["team-lead"].slot_key == "head"
        rule = session.scalar(select(StandardGrantRuleRecord).where(StandardGrantRuleRecord.trigger_source_ref == "team-lead"))
        assert rule is not None and rule.trigger_kind == "appointment" and rule.scope_template == "descendants"
        grant = session.scalar(select(AccessGrantRecord).where(AccessGrantRecord.member_id == "mina"))
        assert grant is not None and grant.scope_kind == "unit" and grant.include_descendants is True


def test_grant_evaluates_the_role_capability_snapshot_not_the_live_role(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    profile = client.get("/api/organization/me", headers=MINA).json()
    assert "task.assign" not in profile["capabilities"]
    assert profile["grants"][0]["role_capability_version"] == 1 and profile["grants"][0]["origin_rule_id"] == "standard:appointment:member:role:member"
    with make_session_factory(database_url)() as session:
        # The role gains a capability later (mapping version 2); mina's grant is pinned at version 1.
        session.add(RoleCapabilityRecord(role_id="role:member", capability_id="task.assign", mapping_version=2))
        session.commit()
    assert "task.assign" not in client.get("/api/organization/me", headers=MINA).json()["capabilities"]
    with make_session_factory(database_url)() as session:
        session.add(
            AccessGrantRecord(
                member_id="mina", role_id="role:member", role_capability_version=2,
                scope_kind="unit", scope_organization_id="product", scope_ref="product", include_descendants=True, granted_by_member_id="jiho",
            )
        )
        session.commit()
    refreshed = client.get("/api/organization/me", headers=MINA).json()
    assert "task.assign" in refreshed["capabilities"]
    assert [grant["role_capability_version"] for grant in refreshed["grants"]] == [1, 2]


def test_revoking_the_grant_removes_capabilities_while_the_appointment_remains(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    with make_session_factory(database_url)() as session:
        grant = session.scalar(select(AccessGrantRecord).where(AccessGrantRecord.member_id == "mina"))
        assert grant is not None and grant.capability_id is None and grant.role_id == "role:member"
        grant.revoked_at = datetime.now(UTC)
        session.commit()
    profile = client.get("/api/organization/me", headers=MINA).json()
    assert profile["capabilities"] == [] and profile["grants"] == []
    assert profile["roles"] == ["구성원"]  # the appointment still exists; only the grant is gone
    assert client.get("/api/tasks", headers=MINA).status_code == 403


def test_a_phone_and_a_birth_date_are_answered_only_to_someone_who_manages_the_organization(tmp_path) -> None:
    """명부는 누구에게나 이름을 말하지만 인사 정보는 아니다 — 권한이 없으면 필드는 남고 값이 비어 온다.

    SPEC-005 §2: 결과 field는 현재 Principal의 권한에 맞게 제한한다. 감추는 방법으로 필드를 지우지는 않는다 —
    client가 응답의 모양으로 권한을 추측하게 만들면 화면마다 다른 규칙이 생긴다.
    """
    client, database_url = _client(tmp_path)
    with make_session_factory(database_url)() as session:
        member = session.get(MemberRecord, "mina")
        member.phone, member.birth_date = "010-0000-0000", date(1990, 1, 2)
        session.commit()

    # 인사 담당자(organization.manage)에게는 값이 온다.
    manager = {row["id"]: row for row in client.get("/api/organization/members", headers={"X-Demo-Persona": "hyeon"}).json()}
    assert manager["mina"]["phone"] == "010-0000-0000" and manager["mina"]["birth_date"] == "1990-01-02"

    # 구성원에게는 필드가 있고 값이 없다.
    plain = {row["id"]: row for row in client.get("/api/organization/members", headers=MINA).json()}
    assert set(plain["mina"]) >= {"id", "display_name", "phone", "birth_date"}
    assert plain["mina"]["phone"] is None and plain["mina"]["birth_date"] is None
    assert plain["mina"]["display_name"] == manager["mina"]["display_name"]
