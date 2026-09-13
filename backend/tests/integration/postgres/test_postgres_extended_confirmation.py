"""The generic typed editor still commits one final submission and effect."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select, func

from ax_workspace.platform.persistence import (
    ActionItemRecord, ConversationTurnRecord, ProjectRecord,
    ReviewDecisionRecord, SubmissionRecord, make_session_factory,
)
from ax_workspace.entrypoints.reset_demo import reset_database
from test_postgres_integration import _postgres_test_url, _conversation_client


@pytest.mark.integration
def test_postgres_extended_confirm_concurrent_replay_keeps_one_edited_submission():
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    application = client.app.state.workflow_application
    headers = {'X-Demo-Persona': 'jiho'}
    conversation = client.post('/api/conversations', headers=headers, json={'title': '프로젝트 확정'}).json()
    turn = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': 'project-confirm'}, json={'body': '프로젝트를 준비해줘', 'context': []}).json()
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(turn['turn_id'])).execution_id
    proposal = application.propose_action(application.authenticated_principal('jiho'), execution_id, 'project.create', '프로젝트 확인', {'name': '원안'})
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=headers).json()
    body = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': {**item['edit_contract']['values'], 'name': '동시 확정 프로젝트'}}
    barrier = Barrier(2)

    def confirm():
        barrier.wait(timeout=10)
        return client.post(f"/api/action-items/{proposal['action_id']}/commands/confirm", headers=headers, json=body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: confirm(), range(2)))
    assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
    assert replies[0].json()['execution_result'] == replies[1].json()['execution_result']
    with make_session_factory(database_url)() as session:
        assert session.scalar(select(func.count()).select_from(ProjectRecord).where(ProjectRecord.name == '동시 확정 프로젝트')) == 1
        assert session.scalar(select(func.count()).select_from(SubmissionRecord).where(SubmissionRecord.decision_item_id == UUID(item['action_item_id']))) == 2
        assert session.scalar(select(func.count()).select_from(ReviewDecisionRecord).where(ReviewDecisionRecord.decision == 'confirm')) == 1
        assert session.get(ActionItemRecord, UUID(proposal['action_id'])).state == 'approved'
