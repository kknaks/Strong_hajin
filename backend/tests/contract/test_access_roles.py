"""역할은 제품의 권고로 시작해 조직의 것이 된다.

The product installs a capability catalog and a few recommended roles. After that the roles belong to the
organization: installing again adds nothing, and a role someone here has changed is never rewritten by an update. A
role may only carry capabilities the product actually implements — an invented key fails loudly instead of granting
nothing quietly.
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.seed import seed_catalog
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.organization_access.catalog import (
    CAPABILITIES,
    ROLE_TEMPLATES_BY_KEY,
    RoleTemplate,
    UnknownCapability,
    validate,
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


def test_a_role_may_only_carry_capabilities_the_product_implements() -> None:
    with pytest.raises(UnknownCapability, match="capability"):
        validate(RoleTemplate("invented", "만든 역할", 1, ("task.read", "budget.approve")))


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
