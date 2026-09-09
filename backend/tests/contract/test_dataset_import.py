"""dataset을 넣는 것은 조직을 다시 만드는 두 번째 길이 아니다 — 같은 원장에, 두 번 넣어도 한 번과 같이.

An import writes what the product already writes. What a person may do comes from the role their organization gave
them, not from the fact that a login row exists; and a login without a secret is refused out loud rather than
half-made.
"""
import csv

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.dataset_import import DatasetImportError, ImportResult, import_into
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.dataset import initialize, read_tables
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.datasets.schema import TABLES
from ax_workspace.modules.organization_access.catalog import ROLE_TEMPLATES_BY_KEY
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
    Base,
    MemberCredentialRecord,
    MemberRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    OrganizationUnitTypeRecord,
    RoleCapabilityRecord,
    RoleRecord,
    make_session_factory,
)

PASSWORD = "dataset-local-1234"


def _write(target, table: str, rows: list[dict[str, str]]) -> None:
    header = next(item for item in TABLES if item.name == table).header
    with (target / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _dataset(tmp_path):
    """작은 조직 하나: 본부 아래 팀, 팀장 한 명과 팀원 한 명, 로그인은 팀장만."""
    target = tmp_path / "dataset"
    target.mkdir()
    initialize(target, name="fixture", as_of="2026-09-02")
    _write(target, "organization_units", [
        # 자식이 부모보다 먼저 적혀 있어도 트리로 들어간다.
        {"key": "ds-team", "name": "성장팀", "unit_type": "team", "parent_key": "ds-div", "display_order": "1"},
        {"key": "ds-div", "name": "성장본부", "unit_type": "division", "parent_key": "", "display_order": "0"},
    ])
    _write(target, "grades", [{"key": "ds-manager", "name": "과장", "display_order": "2"}])
    _write(target, "jobs", [{"key": "ds-growth", "name": "그로스"}])
    _write(target, "positions", [{"key": "ds-lead", "name": "팀장", "unit_type": "team", "slot": "head", "role_key": "team-lead"}])
    _write(target, "members", [
        {"key": "ds-han", "display_name": "한", "employment_state": "active", "employment_type": "regular", "primary_unit_key": "ds-team", "role_key": "member", "grade_key": "ds-manager", "employed_from": "2024-03-04", "employed_until": ""},
        # 소속 행을 따로 쓰지 않아도 primary_unit_key가 그 사람의 자리다.
        {"key": "ds-noh", "display_name": "노", "employment_state": "active", "employment_type": "", "primary_unit_key": "ds-team", "role_key": "member", "grade_key": "", "employed_from": "", "employed_until": ""},
    ])
    _write(target, "memberships", [{"member_key": "ds-han", "unit_key": "ds-team", "kind": "primary", "valid_from": "", "valid_until": ""}])
    _write(target, "appointments", [{"member_key": "ds-han", "unit_key": "ds-team", "position_key": "ds-lead", "kind": "primary", "valid_from": "", "valid_until": ""}])
    _write(target, "job_assignments", [{"member_key": "ds-han", "job_key": "ds-growth", "kind": "primary"}])
    _write(target, "projects", [
        {"key": "ds-hanbit", "name": "한빛 통합 마케팅", "state": "active", "starts_on": "2026-09-01"},
    ])
    # 담당 기간은 원문이 말하지 않으면 비워 둔다.
    _write(target, "project_assignments", [
        {"member_key": "ds-han", "project_key": "ds-hanbit", "kind": "lead"},
        {"member_key": "ds-noh", "project_key": "ds-hanbit", "kind": "member"},
    ])
    _write(target, "logins", [{"member_key": "ds-han", "email": "Han@Example.Test"}])
    return target


def _database(tmp_path) -> str:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return database_url


def _counted(result: ImportResult) -> int:
    return sum(result.created.values())


def test_the_same_dataset_twice_leaves_the_same_organization(tmp_path) -> None:
    database_url = _database(tmp_path)
    rows = read_tables(_dataset(tmp_path))

    first = import_into(database_url, rows, password=PASSWORD)
    assert first.created["organization_units"] == 2 and first.created["members"] == 2
    # 표에 없던 소속도 그 사람의 자리에서 만들어진다.
    assert first.created["memberships"] == 2

    second = import_into(database_url, read_tables(tmp_path / "dataset"), password=PASSWORD)
    assert _counted(second) == 0, second.as_dict()
    assert second.unchanged["members"] == 2 and second.unchanged["organization_units"] == 2

    with make_session_factory(database_url)() as session:
        assert session.get(OrganizationUnitRecord, "ds-team").parent_id == "ds-div"
        # 원문이 말한 고용 형태만 남고, 말하지 않은 사람은 비어 있다.
        assert session.get(MemberRecord, "ds-han").employment_type == "regular"
        assert session.get(MemberRecord, "ds-noh").employment_type is None
        memberships = session.scalars(select(MembershipRecord).where(MembershipRecord.member_id == "ds-noh")).all()
        assert [row.organization_id for row in memberships] == ["ds-team"] and memberships[0].is_primary


def test_a_dry_run_says_what_would_happen_and_leaves_the_database_alone(tmp_path) -> None:
    """예상 결과는 세어 본 것이 아니라 실제로 넣어 본 것이다 — 그리고 되돌린다."""
    database_url = _database(tmp_path)
    rows = read_tables(_dataset(tmp_path))

    preview = import_into(database_url, rows, password=PASSWORD, dry_run=True)

    assert preview.created["organization_units"] == 2 and preview.created["members"] == 2
    with make_session_factory(database_url)() as session:
        # 되돌렸으므로 아무것도 남지 않는다.
        assert session.get(MemberRecord, "ds-han") is None

    # 그리고 진짜로 넣으면 예상한 그대로다.
    applied = import_into(database_url, rows, password=PASSWORD)
    assert applied.created == preview.created

    # 이미 들어와 있는 것 위에서의 예상도 마찬가지로 정확하다.
    again = import_into(database_url, rows, password=PASSWORD, dry_run=True)
    assert _counted(again) == 0 and again.unchanged["members"] == 2


def test_what_a_person_may_do_comes_from_their_role_and_not_from_having_a_login(tmp_path) -> None:
    database_url = _database(tmp_path)
    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    with make_session_factory(database_url)() as session:
        appointment = session.scalar(select(AppointmentRecord).where(AppointmentRecord.member_id == "ds-han"))
        # 보직이 정하는 역할이 그 보직의 역할이다. 로그인 표는 역할을 말하지 않는다.
        assert appointment.role_id == "role:team-lead" and appointment.organization_id == "ds-team"
        held = {
            (grant.role_id, grant.scope_ref, grant.origin_rule_id)
            for grant in session.scalars(select(AccessGrantRecord).where(AccessGrantRecord.member_id == "ds-han"))
            # 프로젝트 배정이 만든 것은 조직 축이 아니다. 여기서 보는 것은 조직이 준 것뿐이다.
            if grant.scope_kind != "project"
        }
        # 보직이 주는 역할과 그 사람 자신의 역할이 함께 남는다. 하나가 다른 하나를 덮어쓰지 않는다.
        assert held == {
            ("role:team-lead", "ds-team", "standard:appointment:ds-lead:role:team-lead"),
            ("role:member", "ds-team", "standard:membership:member:role:member"),
        }
        # 보직 없는 사람도 자기 역할은 갖고, 그 역할은 자기가 속한 곳까지 닿는다.
        plain = session.scalar(select(AccessGrantRecord).where(AccessGrantRecord.member_id == "ds-noh"))
        assert plain.role_id == "role:member" and plain.scope_ref == "ds-team"
        # 로그인은 적힌 사람에게만 생기고, 주소는 어떻게 적었든 한 가지 모양으로 남는다.
        assert session.get(MemberCredentialRecord, "ds-han").email == "han@example.test"
        assert session.get(MemberCredentialRecord, "ds-noh") is None


def test_a_role_that_came_with_being_here_outlives_a_position(tmp_path) -> None:
    """보직이 끝나도 그 사람이 여기 있다는 사실로 받은 역할은 남는다.

    A grant that a standard rule created is not automatically an appointment's shadow. Whether it ends with a
    position is what the rule that made it says, and a membership rule says it does not.
    """
    database_url = _database(tmp_path)
    rows = read_tables(_dataset(tmp_path))
    # 보직 없는 사람. 소속만으로 자기 역할을 갖는다.
    import_into(database_url, rows, password=PASSWORD)

    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    plain = client.get("/api/organization/me", headers={"X-Demo-Persona": "ds-noh"})
    assert plain.status_code == 200, plain.text
    assert plain.json()["capabilities"], "소속으로 받은 역할이 화면에 닿지 않았습니다"


def test_the_product_does_not_know_what_the_company_is_called(tmp_path) -> None:
    """고객의 회사 이름은 제품이 미리 알고 있는 것이 아니다.

    The directory that puts a name to an id, the people one may name in a request, and the reach of an
    organization-wide grant all used to be measured from a unit literally called `scax`. In any real organization
    that unit does not exist, and each of those quietly returned nothing.
    """
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url, demo_organization=False)
    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    named = client.get("/api/organization/members", headers={"X-Demo-Persona": "ds-han"})
    assert named.status_code == 200, named.text
    assert {row["id"] for row in named.json()} == {"ds-han", "ds-noh"}

    with make_session_factory(database_url)() as session:
        assert SqlAlchemyOrganizationRepository(session).organization_root() == "ds-div"


def test_one_company_in_the_database_does_not_reach_into_another(tmp_path) -> None:
    """`조직 전체`는 그 사람의 조직 전체다. 옆에 다른 회사가 서 있어도 거기까지 닿지 않는다.

    A database can hold the example company and a real one at the same time — during an import, or while comparing
    two datasets. An organization-wide grant used to mean literally every unit in the database.
    """
    database_url = _database(tmp_path)  # 예시 회사가 있는 곳
    rows = read_tables(_dataset(tmp_path))
    rows["members"][0]["role_key"] = "executive"  # 이 회사의 대표
    import_into(database_url, rows, password=PASSWORD)

    with make_session_factory(database_url)() as session:
        principal = SqlAlchemyOrganizationRepository(session).principal_for("ds-han")
        assert principal is not None
        reach = {unit for grant in principal.grants for unit in grant.units}
        assert reach == {"ds-div", "ds-team"}, sorted(reach)
        # 예시 회사의 어떤 단위도 여기 없다.
        assert not any(unit.startswith("scax") or unit == "product" for unit in reach)


def test_nobody_is_asked_to_decide_something_they_could_never_open(tmp_path) -> None:
    """조직 전체가 원장에 있어도 로그인은 일부만 갖는다. 답할 수 없는 사람에게 판단을 맡기지 않는다.

    A request put on someone who cannot sign in waits forever. They stay in the directory, in past work and as a
    graph node — they are simply not offered as the person who will answer.
    """
    database_url = _database(tmp_path)
    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    named = {row["id"] for row in client.get("/api/organization/members", headers={"X-Demo-Persona": "ds-han"}).json()}
    assert {"ds-han", "ds-noh"} <= named, "로그인이 없는 사람도 이름은 부를 수 있어야 합니다"

    candidates = client.get("/api/work-request-assignee-candidates", headers={"X-Demo-Persona": "ds-han"})
    assert candidates.status_code == 200, candidates.text
    assert "ds-noh" not in {row["id"] for row in candidates.json()}

    # 화면에서 감추는 것으로 끝나지 않는다: 그렇게 만들려고 하면 원장이 거절한다.
    refused = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "ds-han"},
        json={"assignee_id": "ds-noh", "title": "답할 수 없는 사람에게"},
    )
    assert refused.status_code == 422, refused.text


def test_a_dataset_can_carry_projects_and_who_is_on_them(tmp_path) -> None:
    """프로젝트와 그 사람들도 dataset이 나른다. 권한은 여기서도 제품의 표준 규칙이 만든다."""
    database_url = _database(tmp_path)
    first = import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)
    assert first.created["projects"] == 1 and first.created["project_assignments"] == 2

    from ax_workspace.platform.persistence import ProjectAssignmentRecord, ProjectRecord

    with make_session_factory(database_url)() as session:
        project = session.scalar(select(ProjectRecord).where(ProjectRecord.external_key == "ds-hanbit"))
        assert project is not None and project.name == "한빛 통합 마케팅"
        joined = session.scalars(select(ProjectAssignmentRecord).where(ProjectAssignmentRecord.project_id == project.id)).all()
        assert {row.member_id: row.assignment_kind for row in joined} == {"ds-han": "lead", "ds-noh": "member"}
        # 원문이 담당 기간을 말하지 않았으므로 비어 있다.
        assert all(row.valid_from is None and row.valid_until is None for row in joined)
        grants = session.scalars(
            select(AccessGrantRecord).where(AccessGrantRecord.member_id == "ds-noh", AccessGrantRecord.scope_kind == "project")
        ).all()
        assert [grant.scope_ref for grant in grants] == [str(project.id)]

    second = import_into(database_url, read_tables(tmp_path / "dataset"), password=PASSWORD)
    assert "projects" not in second.created and "project_assignments" not in second.created


def test_an_imported_person_can_sign_in_and_see_their_own_work(tmp_path) -> None:
    """The point of an import is that afterwards someone can just use the product."""
    database_url = _database(tmp_path)
    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    assert client.post("/api/auth/login", json={"email": "han@example.test", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "han@example.test", "password": PASSWORD}).status_code == 200
    assert client.get("/api/my-work").status_code == 200


def test_a_real_organization_does_not_have_to_stand_next_to_the_example_one(tmp_path) -> None:
    """제품이 아는 것과 예시 회사는 따로 설치된다 — 실제 조직이 들어올 자리를 비워 두기 위해서.

    Catalog-only leaves the kinds of unit, the capabilities and the recommended roles, and nothing else. What arrives
    after that is one organization, not two, and the people who arrive with it get no demo password.
    """
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url, demo_organization=False)

    with make_session_factory(database_url)() as session:
        assert session.scalars(select(OrganizationUnitRecord.id)).all() == []
        assert session.scalars(select(MemberRecord.id)).all() == []
        # 제품 자신의 것은 남아 있다.
        assert session.get(OrganizationUnitTypeRecord, "team") is not None
        assert session.get(RoleRecord, "role:team-lead") is not None

    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    with make_session_factory(database_url)() as session:
        roots = session.scalars(select(OrganizationUnitRecord.id).where(OrganizationUnitRecord.parent_id.is_(None))).all()
        assert roots == ["ds-div"]
        # 이 사람들에게는 demo 비밀번호가 가지 않는다: 로그인은 dataset이 적은 사람에게만 있다.
        assert session.scalars(select(MemberCredentialRecord.member_id)).all() == ["ds-han"]


def test_a_role_arrives_with_the_things_it_lets_someone_do(tmp_path) -> None:
    """조직이 하나도 없는 곳에 넣어도 역할은 빈 껍데기로 들어가지 않는다.

    An import may be the first thing that ever happens to a database. A role installed without its capabilities would
    exist, be granted, and let its holder do nothing — the same installation the seed uses is the one used here.
    """
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    factory = make_session_factory(database_url)
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        # 조직 단위 종류만 있는, 아무도 없는 곳.
        for order, type_id in enumerate(("division", "team")):
            session.add(OrganizationUnitTypeRecord(id=type_id, name=type_id, display_order=order))
        session.commit()

    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    with make_session_factory(database_url)() as session:
        carried = set(
            session.scalars(
                select(RoleCapabilityRecord.capability_id).where(RoleCapabilityRecord.role_id == "role:team-lead")
            ).all()
        )
        assert carried == set(ROLE_TEMPLATES_BY_KEY["team-lead"].capabilities)


def test_an_address_without_a_secret_is_said_out_loud_rather_than_half_made(tmp_path) -> None:
    database_url = _database(tmp_path)
    result = import_into(database_url, read_tables(_dataset(tmp_path)), password=None)

    assert "logins" not in result.created
    assert any("ds-han" in note and "비밀번호" in note for note in result.skipped)
    with make_session_factory(database_url)() as session:
        assert session.get(MemberCredentialRecord, "ds-han") is None


def test_a_role_this_product_does_not_have_stops_the_whole_import(tmp_path) -> None:
    database_url = _database(tmp_path)
    target = _dataset(tmp_path)
    rows = read_tables(target)
    rows["members"][0]["role_key"] = "superuser"

    with pytest.raises(DatasetImportError, match="없는 역할"):
        import_into(database_url, rows, password=PASSWORD)

    # 절반만 들어가지 않는다: 조직도 사람도 생기지 않았다.
    with make_session_factory(database_url)() as session:
        assert session.get(OrganizationUnitRecord, "ds-div") is None


def test_an_organization_that_contains_itself_is_refused_before_anything_is_written(tmp_path) -> None:
    database_url = _database(tmp_path)
    rows = read_tables(_dataset(tmp_path))
    rows["organization_units"][1]["parent_key"] = "ds-team"

    with pytest.raises(DatasetImportError, match="자기 아래"):
        import_into(database_url, rows, password=PASSWORD)
    with make_session_factory(database_url)() as session:
        assert session.get(OrganizationUnitRecord, "ds-team") is None


def test_a_phone_and_a_birth_date_arrive_when_the_source_states_them_and_stay_empty_when_it_does_not(tmp_path) -> None:
    """원문이 말한 사람만 전화·생년월일을 갖는다. 말하지 않은 칸은 비워 두고 추정하지 않는다."""
    from datetime import date

    target = _dataset(tmp_path)
    _write(target, "members", [
        {"key": "ds-han", "display_name": "한", "employment_state": "active", "employment_type": "regular", "primary_unit_key": "ds-team", "role_key": "member", "grade_key": "ds-manager", "employed_from": "2024-03-04", "employed_until": "", "phone": "010-0000-0000", "birth_date": "1990-01-02"},
        {"key": "ds-noh", "display_name": "노", "employment_state": "active", "employment_type": "", "primary_unit_key": "ds-team", "role_key": "member", "grade_key": "", "employed_from": "", "employed_until": "", "phone": "", "birth_date": ""},
    ])
    database_url = _database(tmp_path)
    import_into(database_url, read_tables(target), password=PASSWORD)

    with make_session_factory(database_url)() as session:
        han, noh = session.get(MemberRecord, "ds-han"), session.get(MemberRecord, "ds-noh")
        assert (han.phone, han.birth_date) == ("010-0000-0000", date(1990, 1, 2))
        assert (noh.phone, noh.birth_date) == (None, None)

    # 두 번째 적재의 빈 칸은 지우는 말이 아니다 — 원문이 말하지 않는 것을 없앴다고 기록하지 않는다.
    import_into(database_url, read_tables(target), password=PASSWORD)
    with make_session_factory(database_url)() as session:
        han = session.get(MemberRecord, "ds-han")
        assert (han.phone, han.birth_date) == ("010-0000-0000", date(1990, 1, 2))
