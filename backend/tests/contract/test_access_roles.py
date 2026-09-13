"""역할은 제품의 권고로 시작해 조직의 것이 된다.

The product installs a capability catalog and a few recommended roles. After that the roles belong to the
organization: installing again adds nothing, and a role someone here has changed is never rewritten by an update. A
role may only carry capabilities the product actually implements — an invented key fails loudly instead of granting
nothing quietly.
"""
from datetime import UTC, datetime

from sqlalchemy import select

from ax_workspace.bootstrap.seed import seed_catalog
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.organization_access.catalog import (
    CAPABILITIES,
    ROLE_TEMPLATES_BY_KEY,
)
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
    CapabilityRecord,
    RoleCapabilityRecord,
    RoleRecord,
    make_session_factory,
)


def _seeded(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return make_session_factory(database_url)


def test_installed_roles_say_which_recommendation_they_came_from(tmp_path) -> None:
    factory = _seeded(tmp_path)
    with factory() as session:
        catalog = {record.id for record in session.scalars(select(CapabilityRecord))}
        assert catalog == {capability.id for capability in CAPABILITIES}
        lead = session.get(RoleRecord, "role:team-lead")
        assert lead is not None
        assert (lead.template_key, lead.template_version, lead.customized_at) == ("team-lead", 1, None)
        assert lead.label == "팀장"
        mapped = {
            row.capability_id
            for row in session.scalars(select(RoleCapabilityRecord).where(RoleCapabilityRecord.role_id == lead.id))
        }
        assert mapped == set(ROLE_TEMPLATES_BY_KEY["team-lead"].capabilities)


def test_installing_the_same_catalog_again_changes_nothing(tmp_path) -> None:
    factory = _seeded(tmp_path)
    with factory() as session:
        before = (
            len(list(session.scalars(select(RoleRecord)))),
            len(list(session.scalars(select(RoleCapabilityRecord)))),
            len(list(session.scalars(select(AccessGrantRecord)))),
            len(list(session.scalars(select(AppointmentRecord)))),
        )
        seed_catalog(session)
    with factory() as session:
        after = (
            len(list(session.scalars(select(RoleRecord)))),
            len(list(session.scalars(select(RoleCapabilityRecord)))),
            len(list(session.scalars(select(AccessGrantRecord)))),
            len(list(session.scalars(select(AppointmentRecord)))),
        )
    assert before == after


def test_a_role_the_organization_changed_is_not_rewritten_by_the_product(tmp_path) -> None:
    factory = _seeded(tmp_path)
    with factory() as session:
        role = session.get(RoleRecord, "role:member")
        session.execute(
            RoleCapabilityRecord.__table__.delete().where(
                RoleCapabilityRecord.role_id == "role:member",
                RoleCapabilityRecord.capability_id == "meeting.record",
            )
        )
        role.customized_at = datetime.now(UTC)
        session.commit()

    with factory() as session:
        seed_catalog(session)
    with factory() as session:
        mapped = {
            row.capability_id
            for row in session.scalars(select(RoleCapabilityRecord).where(RoleCapabilityRecord.role_id == "role:member"))
        }
    # The organization took a capability out of its own role; installing again does not put it back.
    assert "meeting.record" not in mapped


def test_how_far_a_role_reaches_comes_from_where_the_person_was_appointed(tmp_path) -> None:
    factory = _seeded(tmp_path)
    with factory() as session:
        grants = {
            grant.member_id: grant
            for grant in session.scalars(select(AccessGrantRecord).where(AccessGrantRecord.member_id.in_(["yuna", "jiho"])))
        }
    # 대표 is appointed at the company, so the same role covers the organization.
    assert (grants["yuna"].scope_kind, grants["yuna"].scope_ref, grants["yuna"].role_id) == ("organization", "scax", "role:executive")
    # 팀장's authority is their own team and what sits under it — not the company.
    assert (grants["jiho"].scope_kind, grants["jiho"].scope_ref, grants["jiho"].include_descendants) == ("unit", "product", True)


def test_a_leads_authority_stops_where_it_was_granted(tmp_path) -> None:
    """한 팀의 팀장 권한은 다른 팀으로 새지 않는다.

    Being a member of a team and having authority over it are different facts. 지호 leads 제품팀; putting him in
    개발팀 as well must not let him put work on 개발팀's people. Only a grant scoped there does that.
    """
    from fastapi.testclient import TestClient

    from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
    from ax_workspace.entrypoints.http import create_app
    from ax_workspace.platform.persistence import MembershipRecord

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    factory = make_session_factory(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    jiho = {"X-Demo-Persona": "jiho"}

    with factory() as session:
        # 민석 moves into 개발팀, and 지호 is added to it as an ordinary member — no authority there.
        session.add(MembershipRecord(member_id="minseok", organization_id="engineering", membership_kind="additional"))
        session.add(MembershipRecord(member_id="jiho", organization_id="engineering", membership_kind="additional"))
        session.commit()

    candidates = [item["id"] for item in client.get("/api/task-assignment-candidates", headers=jiho).json()]
    assert candidates == ["mina"], candidates
    refused = client.post("/api/tasks/assign", headers=jiho, json={"title": "다른 팀 일", "assignee_id": "minseok"})
    assert refused.status_code == 422 and "scope" in refused.json()["detail"]

    with factory() as session:
        # 개발팀 asks him to cover for them: an explicit grant, at that unit, is what changes the answer.
        session.add(
            AccessGrantRecord(
                member_id="jiho",
                role_id="role:team-lead",
                role_capability_version=1,
                scope_kind="unit",
                scope_organization_id="engineering",
                scope_ref="engineering",
                include_descendants=True,
                granted_by_member_id="yuna",
            )
        )
        session.commit()

    assert [item["id"] for item in client.get("/api/task-assignment-candidates", headers=jiho).json()] == ["mina", "minseok"]
    assert client.post("/api/tasks/assign", headers=jiho, json={"title": "다른 팀 일", "assignee_id": "minseok"}).status_code == 201


def test_the_executive_reads_the_organizations_work_but_never_decides_for_its_holder(tmp_path) -> None:
    """전체 조회는 읽기다. 남의 판단을 대신하는 권한이 아니다.

    대표 may open the work anyone in the company is holding, and see it listed. That is where it stops: the work is
    still its holder's, the judgement waiting on someone else is still theirs, and a personal conversation is not
    work at all.
    """
    from fastapi.testclient import TestClient

    from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
    from ax_workspace.entrypoints.http import create_app

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    mina, yuna, jiho = ({"X-Demo-Persona": name} for name in ("mina", "yuna", "jiho"))

    task = client.post("/api/tasks", headers=mina, json={"title": "민아가 들고 있는 일"}).json()
    conversation = client.post("/api/conversations", headers=mina, json={"title": "민아의 개인 대화"}).json()

    detail = client.get(f"/api/tasks/{task['task_id']}", headers=yuna)
    assert detail.status_code == 200 and detail.json()["access"] == "read_only"
    assert task["task_id"] in {row["task_id"] for row in client.get("/api/tasks", headers=yuna).json()}
    # 팀장 leads 제품팀 but has no organization-wide read: he still only sees what a relationship earns him.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=jiho).status_code == 404

    # Reading is not holding: the work cannot be driven or closed by the reader.
    moved = client.post(
        f"/api/tasks/{task['task_id']}/start", headers=yuna, json={"expected_version": task["version"]}
    )
    assert moved.status_code in {403, 404}
    assert client.get("/api/my-work", headers=yuna).json() == []
    # A personal conversation is not the organization's work, and is not listed or readable at all.
    assert client.get(f"/api/conversations/{conversation['conversation_id']}", headers=yuna).status_code in {403, 404, 422}
    assert client.get("/api/conversations", headers=yuna).json() == []
