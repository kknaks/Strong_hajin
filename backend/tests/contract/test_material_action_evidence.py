"""Action previews name only actual turn observations that remain readable through their owners."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.platform.persistence import RoleCapabilityRecord, make_session_factory
from test_report_material_search import _stack, _draft, _submit, MINA
from test_material_evidence_owners import _turn

# 이 파일의 테스트는 **진짜 자식 프로세스**를 띄우고(자료·보고서 워커의 `IsolatedWork` spawn ·
# MCP `stdio_client` · `subprocess`) 그 진행을 초 단위 실시간 창으로 잰다 — 그래서 병렬 패스가 아니라
# `-n0` 직렬 패스에서 돈다. 기준과 걸개는 `tests/conftest.py`, 가르는 자리는 `Makefile` 의 `test-serial`.
pytestmark = pytest.mark.serial


def _previews(client, conversation_id, action_id):
    return [
        client.get("/api/actions", headers=MINA).json()[0]["preview"],
        client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["actions"][0]["preview"],
        next(row for row in client.get("/api/action-items", headers=MINA).json() if row["resource"]["id"] == action_id)["preview"],
        client.get(f"/api/action-items/{action_id}", headers=MINA).json()["preview"],
    ]


def test_report_observation_reaches_every_action_preview_and_leaves_after_owner_revocation(tmp_path, monkeypatch):
    client, application, worker, settings = _stack(tmp_path)
    _submit(application, _draft(application, "actionreporttoken"))
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    found = McpReportsFacade(settings, "mina").search_materials("actionreporttoken")
    name = found["results"][0]["name"]
    proposed = application.propose_action(application.authenticated_principal("mina"), execution_id, "task.create_self", "후속 업무", {"title": "검토하기"})
    assert {row["id"]: row["value"] for row in proposed["preview"]}["evidence"] == name
    for preview in _previews(client, conversation_id, proposed["action_id"]):
        assert {row["id"]: row["value"] for row in preview}["evidence"] == name
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "daily_report.read"))
        session.commit()
    for preview in _previews(client, conversation_id, proposed["action_id"]):
        assert all(row["id"] != "evidence" for row in preview)
        assert name not in str(preview)


def test_detached_task_material_disappears_from_action_preview_even_if_task_stays_readable(tmp_path, monkeypatch):
    from test_material_search import _upload

    client, application, worker, settings = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "자료 업무"}).json()["task_id"]
    uploaded = _upload(client, task_id, "detached.txt", b"actiondetachphrase", "text/plain").json()
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(settings, "mina")
    facade.search_materials("actiondetachphrase", resource_type="task", resource_id=task_id)
    proposed = application.propose_action(application.authenticated_principal("mina"), execution_id, "task.create_self", "후속 업무", {"title": "검토하기"})
    assert any(row["id"] == "evidence" for row in proposed["preview"])
    assert client.post(f'/api/tasks/{task_id}/material-bindings/{uploaded['binding_id']}/detach', headers=MINA).status_code == 200
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).status_code == 200
    for preview in _previews(client, conversation_id, proposed["action_id"]):
        assert all(row["id"] != "evidence" for row in preview)


def test_preview_deduplicates_artifacts_across_repeated_search_observations(tmp_path, monkeypatch):
    from test_material_search import _upload

    client, application, worker, settings = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "동일 이름 자료"}).json()["task_id"]
    for body in (b"previewdeduptoken first", b"previewdeduptoken second"):
        _upload(client, task_id, "same.txt", body, "text/plain")
    assert asyncio.run(worker.run_once())
    _, _, execution_id = _turn(client, settings)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(settings, "mina")
    facade.search_materials('previewdeduptoken', resource_type='task', resource_id=task_id)
    facade.search_materials("previewdeduptoken")
    proposed = application.propose_action(application.authenticated_principal("mina"), execution_id, "task.create_self", "후속 업무", {"title": "검토하기"})
    assert {row["id"]: row["value"] for row in proposed["preview"]}["evidence"] == "same.txt, same.txt"


def test_a_later_action_cannot_claim_a_previous_turns_material_observation(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    _submit(application, _draft(application, "previousactiontoken"))
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    principal = application.authenticated_principal("mina")
    application.search_materials(principal, "previousactiontoken", execution_id=execution_id)
    from uuid import uuid4
    from ax_workspace.platform.persistence import ConversationTurnRecord
    next_execution = uuid4()
    with make_session_factory(settings.database_url)() as session:
        session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.execution_id == execution_id)).state = "completed"
        session.flush()
        session.add(ConversationTurnRecord(conversation_id=UUID(conversation_id), execution_id=next_execution, state="running", started_at=datetime.now(UTC)))
        session.commit()
    proposed = application.propose_action(principal, next_execution, "task.create_self", "다른 Turn", {"title": "새 제안"})
    assert all(row["id"] != "evidence" for row in proposed["preview"])
