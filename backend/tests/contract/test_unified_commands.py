"""Confirmed effects consume the final payload in the caller's transaction."""
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ActionItemRecord, AttachmentRecord, MaterialExtractionRecord
from test_mcp import ContractTestAiProvider


CURRENT_MEETING_COMMANDS = [
    "meeting_create", "meeting_quick_start", "meeting_update", "meeting_cancel",
    "meeting_note_delete", "meeting_start", "meeting_end", "meeting_finalize_retry",
    "meeting_todo_promote", "meeting_todo_remove", "meeting_agenda_add",
    "meeting_agenda_update", "meeting_agenda_remove", "meeting_memo_write",
    "meeting_material_detach", "meeting_share", "meeting_revoke_share",
]


@pytest.mark.parametrize("tool", CURRENT_MEETING_COMMANDS)
def test_meeting_screen_commands_are_discoverable_as_confirmable_tools(tmp_path, tool):
    import asyncio
    from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server

    _, application = _stack(tmp_path)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, "mina"))
    listed = {item.name: item for item in asyncio.run(server.list_tools())}
    assert listed[tool].annotations.read_only_hint is False


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("operation", ["meeting_update", "meeting_share", "meeting_revoke_share", "meeting_agenda_add"])
def test_meeting_tools_share_screen_effects_and_wait_for_delegated_approval(
    tmp_path, monkeypatch, operation, delegated
):
    import asyncio
    from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, application = _stack(tmp_path)
    headers = {"X-Demo-Persona": "mina"}
    detail = client.post(
        "/api/meetings",
        headers=headers,
        json={
            "title": "원래 회의",
            "purpose": "유지할 목적",
            "starts_at": "2026-09-11T01:00:00Z",
            "ends_at": "2026-09-11T02:00:00Z",
        },
    ).json()
    meeting_id = detail["meeting"]["meeting_id"]
    if operation == "meeting_revoke_share":
        shared = client.post(
            f"/api/meetings/{meeting_id}/shares", headers=headers, json={"member_ids": ["sora"]}
        )
        assert shared.status_code == 200, shared.text
    arguments = {"meeting_id": meeting_id}
    if operation == "meeting_update":
        arguments["request"] = {"title": "수정된 회의"}
    elif operation == "meeting_share":
        arguments["request"] = {"member_ids": ["sora"]}
    elif operation == "meeting_revoke_share":
        arguments["member_id"] = "sora"
    else:
        arguments["request"] = {"title": "새 안건"}
    if delegated:
        conversation = client.post("/api/conversations", headers=headers, json={"title": "변경 확인"}).json()
        accepted = client.post(
            f"/api/conversations/{conversation['conversation_id']}/messages",
            headers={**headers, "Idempotency-Key": operation},
            json={"body": "변경해줘", "context": []},
        ).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
        monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, "mina"))
    result = asyncio.run(server.call_tool(operation, arguments))
    assert not result.is_error, result
    if delegated:
        before = client.get(f"/api/meetings/{meeting_id}", headers=headers).json()
        assert before["meeting"]["title"] == "원래 회의"
        if operation == "meeting_share":
            assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 404
        elif operation == "meeting_revoke_share":
            assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 200
        elif operation == "meeting_agenda_add":
            assert before["agendas"] == []
        proposal = result.structured_content
        assert proposal["preview"]
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
    after = client.get(f"/api/meetings/{meeting_id}", headers=headers).json()
    if operation == "meeting_update":
        assert after["meeting"]["title"] == "수정된 회의"
        assert after["meeting"]["purpose"] == "유지할 목적"
    elif operation == "meeting_share":
        assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 200
    elif operation == "meeting_revoke_share":
        assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 404
    else:
        assert [row["title"] for row in after["agendas"]] == ["새 안건"]


@pytest.mark.parametrize("via_mcp", [False, True])
def test_meeting_edit_distinguishes_clear_from_omission_and_normalizes_times(tmp_path, via_mcp):
    import asyncio
    from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server

    client, application = _stack(tmp_path)
    headers = {"X-Demo-Persona": "mina"}
    detail = client.post(
        "/api/meetings",
        headers=headers,
        json={
            "title": "시간 유지", "purpose": "지울 목적",
            "starts_at": "2026-09-11T10:00:00+09:00", "ends_at": "2026-09-11T11:00:00+09:00",
        },
    ).json()
    meeting_id = detail["meeting"]["meeting_id"]
    request = {"purpose": None}
    if via_mcp:
        server = _create_bound_persona_server(McpReportsFacade(application._settings, "mina"))
        result = asyncio.run(server.call_tool("meeting_update", {"meeting_id": meeting_id, "request": request}))
        assert not result.is_error
    else:
        result = client.patch(f"/api/meetings/{meeting_id}", headers=headers, json=request)
        assert result.status_code == 200, result.text
    after = client.get(f"/api/meetings/{meeting_id}", headers=headers).json()["meeting"]
    assert after["purpose"] is None and after["title"] == "시간 유지"
    assert after["starts_at"] == "2026-09-11T01:00:00+00:00"
    assert after["ends_at"] == "2026-09-11T02:00:00+00:00"



def _stack(tmp_path):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(RuntimeProfile.TEST, url, materials_dir=str(tmp_path / 'materials'))
    client = TestClient(create_app(settings, report_provider=ContractTestAiProvider()))
    return client, client.app.state.workflow_application


@pytest.mark.parametrize('operation', ['task.update', 'task.checklist.update', 'task.transition', 'daily_report.edit'])
def test_executor_uses_the_final_approved_payload_not_the_original_proposal(tmp_path, operation):
    client, application = _stack(tmp_path)
    principal = application.authenticated_principal('mina')
    task = client.post('/api/tasks', headers={'X-Demo-Persona': 'mina'}, json={'title': '검증 업무', 'checklist': ['초기 단계']}).json()
    task = application.get_task(principal, UUID(task['task_id']))
    if operation == 'task.update':
        original = {'task_id': task['task_id'], 'expected_version': 1, 'changes': {'title': '폐기된 원안'}}
        final = {**original, 'changes': {'title': '승인된 제목'}}
    elif operation == 'task.checklist.update':
        item = task['checklist'][0]
        original = {'task_id': task['task_id'], 'item_id': item['item_id'], 'expected_version': item['version'], 'text': '폐기된 원안'}
        final = {**original, 'text': '승인된 단계'}
    elif operation == 'task.transition':
        original = {'task_id': task['task_id'], 'expected_version': 1, 'target': 'cancelled'}
        final = {**original, 'target': 'in_progress'}
    else:
        draft = application.generate_daily_report_draft(principal, '2026-09-11')
        original = {'report_id': draft['report_id'], 'draft_id': draft['draft_id'], 'expected_version': draft['draft_version'], 'body': '폐기된 원안'}
        final = {**original, 'body': '승인된 보고 본문'}
    proposed = ActionItemRecord(id=uuid4(), action_type=operation, payload=original)
    with application._session_factory() as session:
        executor = application._actions(session)._executor
        executor.execute(principal, proposed, payload=final)
        session.commit()
    if operation == 'daily_report.edit':
        history = application.daily_report_history(principal, draft['report_id'])
        assert '승인된 보고 본문' in str(history) and '폐기된 원안' not in str(history)
    else:
        result = application.get_task(principal, UUID(task['task_id']))
        if operation == 'task.update':
            assert result['title'] == '승인된 제목'
        elif operation == 'task.checklist.update':
            assert result['checklist'][0]['text'] == '승인된 단계'
        else:
            assert result['state'] == 'in_progress'


def test_confirmed_report_revision_registers_material_and_job_before_any_read(tmp_path):
    _, application = _stack(tmp_path)
    principal = application.authenticated_principal('mina')
    draft = application.generate_daily_report_draft(principal, '2026-09-11')
    payload = {'report_id': draft['report_id'], 'draft_id': draft['draft_id'], 'expected_version': draft['draft_version']}
    proposed = ActionItemRecord(id=uuid4(), action_type='daily_report.submit', payload=payload)
    with application._session_factory() as session:
        result = application._actions(session)._executor.execute(principal, proposed, payload=payload)
        revision_id = result['submission_id']
        session.commit()
    with application._session_factory() as session:
        artifact = session.query(AttachmentRecord).filter(AttachmentRecord.source_ref.endswith(revision_id)).one_or_none()
        assert artifact is not None, 'The approved command must publish its native material without a later search'
        extraction = session.query(MaterialExtractionRecord).filter_by(attachment_id=artifact.id).one()
        jobs = [job for job in application.memory_job_queue.snapshot() if job['ordering_key'] == str(extraction.id)]
        assert len(jobs) == 1 and jobs[0]['state'] == 'queued'


def _approval_request(client, headers, proposal, mode):
    """Exercise the current editor contract and retain an explicit legacy receipt case."""
    path = f"/api/action-items/{proposal['action_id']}"
    if mode != 'confirm':
        return path + '/commands/approve', {'expected_version': proposal['version']}
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    item = response.json()
    assert item['edit_contract']['editor'] == 'command'
    assert 'confirm' in {command['id'] for command in item['allowed_commands']}
    return path + '/commands/confirm', {
        'expected_version': item['expected_version'],
        'base_submission_version': item['submission_version'],
        'draft': item['edit_contract']['values'],
    }
