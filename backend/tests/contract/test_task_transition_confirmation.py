"""State confirmations preserve the selected transition and existing lifecycle rules."""
import pytest
from test_extended_confirmation import _propose
from test_unified_commands import _stack


@pytest.mark.parametrize('target', ['in_progress', 'blocked', 'done', 'cancelled'])
def test_task_transition_confirms_only_its_bound_target_and_final_reason(tmp_path, target):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    task = client.post('/api/tasks', headers=headers, json={'title': '상태를 바꿀 업무'}).json()
    if target in {'blocked', 'done'}:
        task = client.post(f"/api/tasks/{task['task_id']}/start", headers=headers, json={'expected_version': task['version']}).json()
    payload = {'task_id': task['task_id'], 'expected_version': task['version'], 'target': target, 'reason': '제안 사유' if target == 'blocked' else None}
    item = _propose(client, application, 'task.transition', payload)
    assert item['edit_contract']['editor'] == 'command'
    assert 'target' not in {field['id'] for field in item['edit_contract']['fields']}
    if target == 'blocked':
        assert item['edit_contract']['fields'][0]['empty_policy'] == 'forbid'
    else:
        assert item['edit_contract']['fields'] == []
    draft = {**item['edit_contract']['values'], 'reason': '확인한 차단 사유' if target == 'blocked' else None}
    body = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': draft}
    url = f"/api/action-items/{item['action_item_id']}/commands/confirm"
    retargeted = client.post(url, headers=headers, json={**body, 'draft': {**draft, 'target': 'cancelled' if target != 'cancelled' else 'in_progress'}})
    assert retargeted.status_code == 422
    response = client.post(url, headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()['execution_result']
    assert result['state'] == target and result['version'] == task['version'] + 1
    assert result['block_reason'] == ('확인한 차단 사유' if target == 'blocked' else None)
    assert client.post(url, headers=headers, json=body).json()['execution_result'] == result
