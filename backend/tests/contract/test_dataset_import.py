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
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
    Base,
    MemberCredentialRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    RoleCapabilityRecord,
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
        {"key": "ds-han", "display_name": "한", "employment_state": "active", "primary_unit_key": "ds-team", "role_key": "member", "grade_key": "ds-manager", "employed_from": "2024-03-04", "employed_until": ""},
        # 소속 행을 따로 쓰지 않아도 primary_unit_key가 그 사람의 자리다.
        {"key": "ds-noh", "display_name": "노", "employment_state": "active", "primary_unit_key": "ds-team", "role_key": "member", "grade_key": "", "employed_from": "", "employed_until": ""},
    ])
    _write(target, "memberships", [{"member_key": "ds-han", "unit_key": "ds-team", "kind": "primary", "valid_from": "", "valid_until": ""}])
    _write(target, "appointments", [{"member_key": "ds-han", "unit_key": "ds-team", "position_key": "ds-lead", "kind": "primary", "valid_from": "", "valid_until": ""}])
    _write(target, "job_assignments", [{"member_key": "ds-han", "job_key": "ds-growth", "kind": "primary"}])
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
        memberships = session.scalars(select(MembershipRecord).where(MembershipRecord.member_id == "ds-noh")).all()
        assert [row.organization_id for row in memberships] == ["ds-team"] and memberships[0].is_primary


def test_what_a_person_may_do_comes_from_their_role_and_not_from_having_a_login(tmp_path) -> None:
    database_url = _database(tmp_path)
    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    with make_session_factory(database_url)() as session:
        appointment = session.scalar(select(AppointmentRecord).where(AppointmentRecord.member_id == "ds-han"))
        # 보직이 정하는 역할이 그 보직의 역할이다. 로그인 표는 역할을 말하지 않는다.
        assert appointment.role_id == "role:team-lead" and appointment.organization_id == "ds-team"
        lead = session.scalar(select(AccessGrantRecord).where(AccessGrantRecord.member_id == "ds-han"))
        assert lead.role_id == "role:team-lead" and lead.scope_ref == "ds-team" and lead.include_descendants
        assert lead.origin_rule_id == "standard:ds-lead:role:team-lead"
        # 보직 없는 사람도 자기 역할은 갖고, 그 역할은 자기가 속한 곳까지 닿는다.
        plain = session.scalar(select(AccessGrantRecord).where(AccessGrantRecord.member_id == "ds-noh"))
        assert plain.role_id == "role:member" and plain.scope_ref == "ds-team"
        # 로그인은 적힌 사람에게만 생기고, 주소는 어떻게 적었든 한 가지 모양으로 남는다.
        assert session.get(MemberCredentialRecord, "ds-han").email == "han@example.test"
        assert session.get(MemberCredentialRecord, "ds-noh") is None


def test_an_imported_person_can_sign_in_and_see_their_own_work(tmp_path) -> None:
    """The point of an import is that afterwards someone can just use the product."""
    database_url = _database(tmp_path)
    import_into(database_url, read_tables(_dataset(tmp_path)), password=PASSWORD)

    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    assert client.post("/api/auth/login", json={"email": "han@example.test", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "han@example.test", "password": PASSWORD}).status_code == 200
    assert client.get("/api/my-work").status_code == 200


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
        from ax_workspace.platform.persistence import OrganizationUnitTypeRecord

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
