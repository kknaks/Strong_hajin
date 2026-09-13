"""Editable confirmations consume a typed final submission and retain the target binding."""
from uuid import UUID

import pytest

from ax_workspace.platform.persistence import ConversationTurnRecord
from test_unified_commands import _stack


def _propose(client, application, kind, payload):
    headers = {'X-Demo-Persona': 'jiho'}
    conversation = client.post('/api/conversations', headers=headers, json={'title': '수정 확정'}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': kind}, json={'body': '이 변경을 준비해줘', 'context': []}).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
    proposal = application.propose_action(application.authenticated_principal('jiho'), execution_id, kind, '수정할 제안', payload)
    return client.get(f"/api/action-items/{proposal['action_id']}", headers=headers).json()


@pytest.mark.parametrize('kind', ['project.create', 'assistant.character.set'])
def test_extended_confirm_saves_and_executes_the_final_typed_content(tmp_path, kind):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    if kind == 'project.create':
        original, edit = {'name': '제안 원안'}, {'name': '최종 프로젝트', 'description': '검토해서 수정한 설명'}
    elif kind == 'assistant.character.set':
        original, edit = {'character_key': 'rabbit', 'expected_version': 0}, {'character_key': 'bear'}
    item = _propose(client, application, kind, original)
    assert item['edit_contract']['editor'] == 'command'
    assert 'confirm' in {command['id'] for command in item['allowed_commands']}
    body = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': {**item['edit_contract']['values'], **edit}}
    url = f"/api/action-items/{item['action_item_id']}/commands/confirm"
    confirmed = client.post(url, headers=headers, json=body)
    assert confirmed.status_code == 200, confirmed.text
    replay = client.post(url, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()['execution_result'] == confirmed.json()['execution_result']
    history = client.get(f"/api/action-items/{item['action_item_id']}", headers=headers).json()
    assert history['rounds'][-1]['snapshot'].items() >= edit.items()
    assert len(history['rounds']) == 2
    result = confirmed.json()['execution_result']
    if kind == 'project.create':
        assert result['name'] == edit['name']
    elif kind == 'assistant.character.set':
        assert result == {'character_key': 'bear', 'version': 1}


def test_extended_confirm_cannot_retarget_a_bound_command(tmp_path):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    first = client.post('/api/tasks', headers=headers, json={'title': '확인한 대상'}).json()
    second = client.post('/api/tasks', headers=headers, json={'title': '다른 대상'}).json()
    item = _propose(client, application, 'task.reassign', {'task_id': first['task_id'], 'expected_version': first['version'], 'assignee_id': 'mina'})
    values = {**item['edit_contract']['values'], 'task_id': second['task_id']}
    failed = client.post(f"/api/action-items/{item['action_item_id']}/commands/confirm", headers=headers, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': values})
    assert failed.status_code == 422, failed.text
    assert client.get(f"/api/tasks/{second['task_id']}", headers=headers).json()['version'] == second['version']


def test_defaulted_non_null_command_fields_do_not_offer_null_clears(tmp_path):
    client, application = _stack(tmp_path)
    item = _propose(client, application, 'conversation.create', {})
    assert item['edit_contract']['fields'][0]['empty_policy'] == 'forbid'
    from ax_workspace.modules.ax_execution.command_contracts import COMMAND_CONTRACTS
    contract = COMMAND_CONTRACTS['project.assign_member']
    assert contract.empty_policy('kind', contract.model.model_json_schema()['properties']['kind']) == 'forbid'


@pytest.mark.parametrize('capability', ['action.decide', 'meeting.manage'])
def test_extended_confirm_rechecks_revoked_authority_before_recording_a_decision(tmp_path, capability):
    from sqlalchemy import delete
    from ax_workspace.platform.persistence import RoleCapabilityRecord, ReviewDecisionRecord, SubmissionRecord

    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    detail = client.post('/api/meetings', headers=headers, json={'title': '권한 회수 전 회의', 'starts_at': '2026-09-11T01:00:00Z', 'ends_at': '2026-09-11T02:00:00Z'}).json()
    meeting = detail['meeting']
    item = _propose(client, application, 'meeting.info.update', {'meeting_id': meeting['meeting_id'], 'changes': {'title': '제안 원안'}})
    with application._session_factory() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == capability))
        session.commit()
    response = client.post(f"/api/action-items/{item['action_item_id']}/commands/approve", headers=headers, json={'expected_version': item['expected_version']})
    assert response.status_code in {403, 404, 422}, response.text
    unchanged = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=headers).json()['meeting']
    assert unchanged['title'] == meeting['title']
    with application._session_factory() as session:
        assert session.query(ReviewDecisionRecord).count() == 0
        assert session.query(SubmissionRecord).filter_by(decision_item_id=UUID(item['action_item_id'])).count() == 1
