"""Independent files have an explicit personal/team folder, with live organization membership as the boundary."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from ax_workspace.modules.work.materials import MaterialNotFound
from ax_workspace.platform.persistence import MembershipRecord, OrganizationUnitRecord, make_session_factory
from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository
from test_material_search import MINA, JIHO, _stack

# 이 파일의 테스트는 **진짜 자식 프로세스**를 띄우고(자료·보고서 워커의 `IsolatedWork` spawn ·
# MCP `stdio_client` · `subprocess`) 그 진행을 초 단위 실시간 창으로 잰다 — 그래서 병렬 패스가 아니라
# `-n0` 직렬 패스에서 돈다. 기준과 걸개는 `tests/conftest.py`, 가르는 자리는 `Makefile` 의 `test-serial`.
pytestmark = pytest.mark.serial


def test_personal_and_team_folder_uploads_search_without_tasks_and_open_through_their_owner(tmp_path):
    client, application, worker, _ = _stack(tmp_path)
    personal = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "내 자료"})
    assert personal.status_code == 201, personal.text
    team = client.post("/api/material-folders", headers=MINA, json={"kind": "team", "organization_id": "product", "title": "제품팀 자료"})
    assert team.status_code == 201, team.text
    uploaded = []
    for folder in (personal.json(), team.json()):
        response = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
                               files={"file": ("independent.txt", b"independentfoldertoken", "text/plain")})
        assert response.status_code == 201, response.text
        uploaded.append(response.json())
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "independentfoldertoken")
    assert found["searched_materials"] == 2
    assert {hit["material_id"] for hit in found["results"]} == {row["material_id"] for row in uploaded}
    assert {hit["source_resource_type"] for hit in found["results"]} == {"personal_folder", "team_folder"}
    for hit in found["results"]:
        opened = client.get(hit["origin"], headers=MINA)
        assert opened.status_code == 200 and opened.content == b"independentfoldertoken"
    peer = application.search_materials(application.authenticated_principal("jiho"), "independentfoldertoken")
    assert peer["searched_materials"] == 1 and peer["results"][0]["material_id"] == uploaded[1]["material_id"]
    assert client.get(f"/api/material-folders/{personal.json()['folder_id']}/materials", headers=JIHO).status_code == 404
    assert client.get("/api/tasks", headers=MINA).json() == []


def test_ended_team_membership_revokes_folder_upload_open_and_search_even_with_an_old_principal(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "team", "organization_id": "product", "title": "회수 팀 자료"})
    assert folder.status_code == 201, folder.text
    fid = folder.json()["folder_id"]
    uploaded = client.post(f"/api/material-folders/{fid}/materials", headers=MINA,
                           files={"file": ("revoked.txt", b"teamrevocationtoken", "text/plain")}).json()
    assert asyncio.run(worker.run_once())
    stale_principal = application.authenticated_principal("mina")
    assert application.search_materials(stale_principal, "teamrevocationtoken")["searched_materials"] == 1
    with make_session_factory(settings.database_url)() as session:
        membership = session.scalar(select(MembershipRecord).where(MembershipRecord.member_id == "mina", MembershipRecord.organization_id == "product"))
        membership.valid_until = datetime.now(UTC)
        session.commit()
    result = application.search_materials(stale_principal, "teamrevocationtoken")
    assert result["results"] == [] and result["unavailable_materials_count"] == 0
    assert client.get(uploaded["origin"], headers=MINA).status_code == 404
    assert client.post(f"/api/material-folders/{fid}/materials", headers=MINA,
                       files={"file": ("new.txt", b"not allowed", "text/plain")}).status_code == 404
    # The former uploader does not own team access; a current member can still read it.
    assert client.get(uploaded["origin"], headers=JIHO).status_code == 200


def test_folder_owner_is_explicit_and_organization_wide_authority_does_not_make_someone_a_team_member(tmp_path):
    client, application, _, _ = _stack(tmp_path)
    team = client.post("/api/material-folders", headers=MINA, json={"kind": "team", "organization_id": "product", "title": "직접 소속 자료"}).json()
    uploaded = client.post(f"/api/material-folders/{team['folder_id']}/materials", headers=MINA,
                           files={"file": ("waiting.txt", b"privatependingtoken", "text/plain")}).json()
    admin = {"X-Demo-Persona": "yuna"}
    assert client.get("/api/material-folders", headers=admin).json() == []
    denied = application.search_materials(application.authenticated_principal("yuna"), "privatependingtoken")
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0
    assert client.get(uploaded["origin"], headers=admin).status_code == 404
    for team_id in ("product", "absent-organization"):
        response = client.post("/api/material-folders", headers=admin, json={"kind": "team", "organization_id": team_id, "title": "접근 불가"})
        assert response.status_code == 404 and response.json() == {"detail": "organization was not found"}


def test_shared_artifact_has_only_readable_folder_contexts_and_detach_preserves_the_other_owner(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    personal = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "개인 연결"}).json()
    team = client.post("/api/material-folders", headers=MINA, json={"kind": "team", "organization_id": "product", "title": "공유 연결"}).json()
    fid = personal["folder_id"]
    uploaded = client.post(f"/api/material-folders/{fid}/materials", headers=MINA,
                           files={"file": ("shared.txt", b"foldersharedtoken", "text/plain")}).json()
    mid = uploaded["material_id"]
    with make_session_factory(settings.database_url)() as session:
        SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(mid), context_type="material_folder", context_id=team["folder_id"], role="input", bound_by="mina")
        session.commit()
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    found = application.search_materials(principal, "foldersharedtoken")
    assert found["searched_materials"] == 1 and len(found["results"]) == 1
    assert len(found["results"][0]["source_contexts"]) == 2
    peer = application.search_materials(application.authenticated_principal("jiho"), "foldersharedtoken")
    assert [context["resource_id"] for context in peer["results"][0]["source_contexts"]] == [team["folder_id"]]
    assert client.post(f"/api/material-folders/{team['folder_id']}/materials/{mid}/detach", headers=JIHO).status_code == 422
    assert client.post(f"/api/material-folders/{fid}/materials/{mid}/detach", headers=MINA).status_code == 200
    assert client.get(uploaded["origin"], headers=MINA).status_code == 404
    remaining = application.search_materials(principal, "foldersharedtoken")
    assert [context["resource_id"] for context in remaining["results"][0]["source_contexts"]] == [team["folder_id"]]
    assert client.get(remaining["results"][0]["origin"], headers=MINA).content == b"foldersharedtoken"
    assert client.post(f"/api/material-folders/{team['folder_id']}/archive", headers=JIHO).status_code == 422
    assert client.post(f"/api/material-folders/{team['folder_id']}/archive", headers=MINA).status_code == 200
    empty = application.search_materials(principal, "foldersharedtoken")
    assert empty["results"] == [] and empty["unavailable_materials_count"] == 0
    for missing_id in (team["folder_id"], str(uuid4())):
        with pytest.raises(MaterialNotFound, match="resource was not found"):
            application.search_materials(principal, "foldersharedtoken", resource_type="team_folder", resource_id=missing_id)


def test_abolished_team_is_not_a_readable_folder_owner_even_if_membership_was_not_cleaned_up(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "team", "organization_id": "product", "title": "폐지 팀"}).json()
    uploaded = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
                           files={"file": ("closed.txt", b"closedteamtoken", "text/plain")}).json()
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    with make_session_factory(settings.database_url)() as session:
        unit = session.get(OrganizationUnitRecord, "product")
        unit.abolished_at = datetime.now(UTC)
        session.commit()
    assert application.search_materials(principal, "closedteamtoken")["results"] == []
    assert client.get(uploaded["origin"], headers=MINA).status_code == 404
    assert client.post("/api/material-folders", headers=MINA, json={"kind": "team", "organization_id": "product", "title": "새 자료함"}).status_code == 404
