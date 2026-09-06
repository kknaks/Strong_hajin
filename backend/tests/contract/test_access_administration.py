"""권한을 바꾸는 것도 기록되는 행위다.

Roles and grants can be changed at runtime, but not in ways that leave the organization unable to manage itself: the
last person who can administer access cannot be removed, nobody can take away their own last authority by accident,
and two people editing the same role do not silently overwrite each other. Every change names the administrator who
made it.
"""
from uuid import UUID

import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.organization_access.administration import (
    AccessAdministrationDenied,
    AccessAdministrationError,
    AccessVersionConflict,
)
from ax_workspace.modules.organization_access.catalog import UnknownCapability
from ax_workspace.platform.persistence import AccessGrantRecord, ActivityEventRecord, make_session_factory


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    application = create_workflow_application(Settings(RuntimeProfile.TEST, database_url))
    return application, make_session_factory(database_url)


def test_an_administrator_can_widen_and_take_back_authority_and_the_record_says_who_did(tmp_path) -> None:
    application, factory = _stack(tmp_path)
    yuna = application.authenticated_principal("yuna")

    granted = application.grant_access_role(
        yuna, member_id="mina", role_id="role:team-lead", scope_kind="unit", scope_ref="product", reason="팀장 대행"
    )
    assert granted["role_id"] == "role:team-lead" and granted["scope_ref"] == "product"
    mina = application.authenticated_principal("mina")
    assert mina.allows("task.assign", unit="product") and not mina.allows("task.assign", unit="engineering")

    application.revoke_access_grant(yuna, UUID(granted["grant_id"]), reason="대행 종료")
    assert "task.assign" not in application.authenticated_principal("mina").capabilities

    with factory() as session:
        events = [
            event
            for event in session.scalars(select(ActivityEventRecord).where(ActivityEventRecord.target_type == "member"))
            if event.target_id == "mina"
        ]
    assert [event.event_kind for event in events] == ["access.grant_added", "access.grant_revoked"]
    # The administrator is the actor; the person whose access changed is the target.
    assert {event.actor_id for event in events} == {"yuna"} and {event.reason for event in events} == {"팀장 대행", "대행 종료"}


def test_only_someone_whose_authority_covers_that_person_may_change_their_access(tmp_path) -> None:
    application, _ = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    with pytest.raises(AccessAdministrationDenied):
        application.grant_access_role(
            jiho, member_id="mina", role_id="role:team-lead", scope_kind="unit", scope_ref="product", reason="권한 없음"
        )


def test_the_organization_cannot_be_left_with_nobody_to_administer_it(tmp_path) -> None:
    application, factory = _stack(tmp_path)
    yuna = application.authenticated_principal("yuna")
    with factory() as session:
        grant_id = session.scalar(
            select(AccessGrantRecord.id).where(AccessGrantRecord.member_id == "yuna", AccessGrantRecord.role_id == "role:executive")
        )

    # 현우 also administers access, but only within 피플팀 — removing 유나 would leave nobody covering the company.
    with pytest.raises(AccessAdministrationError, match="관리"):
        application.revoke_access_grant(yuna, grant_id, reason="스스로 회수")
    assert application.authenticated_principal("yuna").allows("organization.manage", unit="scax")


def test_two_people_editing_the_same_role_do_not_overwrite_each_other(tmp_path) -> None:
    application, _ = _stack(tmp_path)
    yuna = application.authenticated_principal("yuna")
    role = application.set_role_capabilities(
        yuna, "role:member", ["task.read", "task.self_manage", "work.read"], expected_version=1, reason="정리"
    )
    assert role["version"] == 2 and role["customized"] is True
    assert application.authenticated_principal("mina").capabilities == frozenset({"task.read", "task.self_manage", "work.read"})

    with pytest.raises(AccessVersionConflict):
        application.set_role_capabilities(yuna, "role:member", ["task.read"], expected_version=1, reason="늦은 편집")


def test_a_role_cannot_be_given_a_capability_the_product_does_not_implement(tmp_path) -> None:
    application, _ = _stack(tmp_path)
    yuna = application.authenticated_principal("yuna")
    with pytest.raises(UnknownCapability, match="capability"):
        application.set_role_capabilities(yuna, "role:member", ["task.read", "budget.approve"], expected_version=1, reason="오타")
    # The failed edit changed nothing, including the role's version.
    assert application.authenticated_principal("mina").allows("task.self_manage")


def test_the_same_refusals_hold_over_http(tmp_path) -> None:
    """관리 command도 다른 판단과 같은 문을 쓴다: 권한 없으면 403, 늦은 편집은 409, 마지막 관리자는 422."""
    from fastapi.testclient import TestClient

    from ax_workspace.entrypoints.http import create_app

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    yuna, jiho = {"X-Demo-Persona": "yuna"}, {"X-Demo-Persona": "jiho"}

    body = {"member_id": "mina", "role_id": "role:team-lead", "scope_kind": "unit", "scope_ref": "product", "reason": "대행"}
    assert client.post("/api/access/grants", headers=jiho, json=body).status_code == 403
    granted = client.post("/api/access/grants", headers=yuna, json=body)
    assert granted.status_code == 201

    stale = client.patch(
        "/api/access/roles/role:member",
        headers=yuna,
        json={"expected_version": 99, "capabilities": ["task.read"], "reason": "늦은 편집"},
    )
    assert stale.status_code == 409

    invented = client.patch(
        "/api/access/roles/role:member",
        headers=yuna,
        json={"expected_version": 1, "capabilities": ["task.read", "budget.approve"], "reason": "오타"},
    )
    assert invented.status_code == 422 and "capability" in invented.json()["detail"]

    with make_session_factory(database_url)() as session:
        own = session.scalar(
            select(AccessGrantRecord.id).where(
                AccessGrantRecord.member_id == "yuna", AccessGrantRecord.role_id == "role:executive"
            )
        )
    last = client.post(f"/api/access/grants/{own}/revoke", headers=yuna, json={"reason": "스스로 회수"})
    assert last.status_code == 422 and "관리" in last.json()["detail"]
    assert client.get("/api/organization/me", headers=yuna).status_code == 200
