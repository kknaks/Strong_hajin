"""D8: saved item failures replay; a fresh confirmation checks current authority/version."""
import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.modules.work.application import TaskApplication
from ax_workspace.platform.persistence import RoleCapabilityRecord, ReviewDecisionRecord, SubmissionRecord, make_session_factory
from test_mcp_checklist import _stack, _task_with_steps, _delegated_turn, MINA


def _confirm(client, proposal):
    path = f"/api/action-items/{proposal['action_id']}"
    item = client.get(path, headers=MINA).json()
    body = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version']}
    return path + '/commands/confirm', body


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('revoke_before_new_confirmation', [False, True])
def test_saved_batch_failures_are_not_reexecuted_and_new_confirmation_rechecks_authority(tmp_path, monkeypatch, partial, revoke_before_new_confirmation):
    database_url, settings, client = _stack(tmp_path)
    failed_task, _ = _task_with_steps(client, title='다시 확인할 업무')
    failed_before = client.get(f'/api/tasks/{failed_task}', headers=MINA).json()['version']
    operations = [{'kind': 'progress.note', 'task_id': failed_task, 'expected_version': failed_before, 'summary': '확인된 진행 사실'}]
    if partial:
        good_task, _ = _task_with_steps(client, title='먼저 반영할 업무')
        operations.insert(0, {'kind': 'progress.note', 'task_id': good_task, 'expected_version': 1, 'summary': '먼저 반영'})
    _delegated_turn(client, client.app.state.workflow_application, MINA, 'mina', monkeypatch)
    proposal = McpReportsFacade(settings, 'mina').update_task_progress_batch(operations)
    assert client.post(f'/api/tasks/{failed_task}/checklist', headers=MINA, json={'text': '그 사이 생긴 단계'}).status_code == 201
    url, body = _confirm(client, proposal)
    first = client.post(url, headers=MINA, json=body)
    assert first.status_code == 200, first.text
    result = first.json()['execution_result']
    assert result['batch_state'] == ('partial' if partial else 'failed')
    assert result['items'][-1]['status'] == 'stale'
    assert result['applied_count'] == int(partial)

    def must_not_execute(*args, **kwargs):
        raise AssertionError('a saved success or failure was executed again')

    with monkeypatch.context() as replay:
        replay.setattr(TaskApplication, 'add_progress_note', must_not_execute)
        repeated = client.post(url, headers=MINA, json=body)
    assert repeated.status_code == 200 and repeated.json()['execution_result'] == result
    current_version = client.get(f'/api/tasks/{failed_task}', headers=MINA).json()['version']
    _delegated_turn(client, client.app.state.workflow_application, MINA, 'mina', monkeypatch)
    new_proposal = McpReportsFacade(settings, 'mina').update_task_progress_batch([{
        'kind': 'progress.note', 'task_id': failed_task, 'expected_version': current_version, 'summary': '확인된 진행 사실',
    }])
    assert new_proposal['action_id'] != proposal['action_id'] and new_proposal['state'] == 'pending'
    new_url, new_body = _confirm(client, new_proposal)
    assert client.get(f'/api/tasks/{failed_task}', headers=MINA).json()['version'] == current_version
    if revoke_before_new_confirmation:
        with make_session_factory(database_url)() as session:
            session.execute(RoleCapabilityRecord.__table__.delete().where(RoleCapabilityRecord.capability_id == 'task.self_manage'))
            session.commit()
    new_result = client.post(new_url, headers=MINA, json=new_body)
    assert new_result.status_code == 200, new_result.text
    assert new_result.json()['execution_result']['items'][0]['status'] == ('denied' if revoke_before_new_confirmation else 'applied')
    assert client.get(f'/api/tasks/{failed_task}', headers=MINA).json()['version'] == current_version + int(not revoke_before_new_confirmation)
    assert client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()['execution_result'] == result


def test_unexpected_batch_failure_rolls_back_successful_savepoints_and_confirmation(tmp_path, monkeypatch):
    database_url, settings, client = _stack(tmp_path)
    first, _ = _task_with_steps(client, title='앞선 업무')
    second, _ = _task_with_steps(client, title='예외가 발생할 업무')
    _delegated_turn(client, client.app.state.workflow_application, MINA, 'mina', monkeypatch)
    proposal = McpReportsFacade(settings, 'mina').update_task_progress_batch([
        {'kind': 'progress.note', 'task_id': target, 'expected_version': 1, 'summary': '기록할 진행'} for target in [first, second]
    ])
    url, body = _confirm(client, proposal)
    original = TaskApplication.add_progress_note

    def fail_second(self, principal, task_id, *args, **kwargs):
        if str(task_id) == second:
            raise RuntimeError('unexpected batch failure')
        return original(self, principal, task_id, *args, **kwargs)

    with monkeypatch.context() as failure:
        failure.setattr(TaskApplication, 'add_progress_note', fail_second)
        with pytest.raises(RuntimeError, match='unexpected batch failure'):
            client.post(url, headers=MINA, json=body)
    assert all(client.get(f'/api/tasks/{target}', headers=MINA).json()['version'] == 1 for target in [first, second])
    with make_session_factory(database_url)() as session:
        assert session.query(ReviewDecisionRecord).count() == 0
        assert session.query(SubmissionRecord).count() == 1
