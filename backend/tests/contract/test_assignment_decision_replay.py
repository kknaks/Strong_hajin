"""A later Task edit cannot erase the receipt of its earlier assignment acceptance."""
from uuid import UUID
import pytest
from ax_workspace.platform.persistence import ReviewDecisionRecord, TaskAssignmentRecord
from test_unified_commands import _stack


@pytest.mark.parametrize('historical_record', [False, True])
def test_assignment_acceptance_replays_its_consumed_version_after_a_task_edit(tmp_path, historical_record):
    client, application = _stack(tmp_path)
    jiho, mina = {'X-Demo-Persona': 'jiho'}, {'X-Demo-Persona': 'mina'}
    created = client.post('/api/tasks/assign', headers=jiho, json={'title': '원래 배정', 'assignee_id': 'mina'}).json()
    path = f"/api/action-items/{created['assignment_id']}"
    item = client.get(path, headers=mina).json()
    payload = {'expected_version': item['expected_version']}
    accepted = client.post(path + '/commands/accept', headers=mina, json=payload)
    assert accepted.status_code == 200, accepted.text
    if historical_record:
        # Previously saved decisions did not record consumed-version facts.
        with application._session_factory() as session:
            assignment = session.get(TaskAssignmentRecord, UUID(created['assignment_id']))
            session.get(ReviewDecisionRecord, assignment.source_review_decision_id).conditions = None
            session.commit()
    edited = client.patch(f"/api/tasks/{created['task']['task_id']}", headers=mina, json={'expected_version': item['expected_version'], 'title': '수락 뒤 변경'})
    assert edited.status_code == 200, edited.text
    replay = client.post(path + '/commands/accept', headers=mina, json=payload)
    assert replay.status_code == 200, replay.text
    assert client.post(path + '/commands/accept', headers=mina, json={'expected_version': edited.json()['version']}).status_code == 422


def test_closed_assignment_keeps_its_frozen_content_after_the_task_moves_to_another_holder(tmp_path):
    client, _ = _stack(tmp_path)
    jiho, mina = {'X-Demo-Persona': 'jiho'}, {'X-Demo-Persona': 'mina'}
    assigned = client.post('/api/tasks/assign', headers=jiho, json={'title': '당시 배정한 업무', 'assignee_id': 'mina'}).json()
    task_id = assigned['task']['task_id']
    path = f"/api/action-items/{assigned['assignment_id']}"
    item = client.get(path, headers=mina).json()
    payload = {'expected_version': item['expected_version']}
    assert client.post(path + '/commands/accept', headers=mina, json=payload).status_code == 200
    reassigned = client.post(f'/api/tasks/{task_id}/reassign', headers=jiho, json={'expected_version': item['expected_version'], 'assignee_id': 'jiho'})
    assert reassigned.status_code == 200, reassigned.text
    new_assignment = reassigned.json()
    assert client.post(f"/api/task-assignments/{new_assignment['assignment_id']}/accept", headers=jiho).status_code == 200
    updated = client.patch(f'/api/tasks/{task_id}', headers=jiho, json={'expected_version': new_assignment['task']['version'], 'title': '새 담당자만 볼 내용', 'description': '새 담당자의 비공개 설명'})
    assert updated.status_code == 200, updated.text
    assert client.get(f'/api/tasks/{task_id}', headers=mina).status_code == 404
    receipt = client.post(path + '/commands/accept', headers=mina, json=payload)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()['subject'] == '당시 배정한 업무'
    assert '새 담당자' not in receipt.text
    detail = client.get(path, headers=mina)
    assert detail.status_code == 200 and '새 담당자' not in detail.text
