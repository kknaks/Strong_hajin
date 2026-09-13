"""Old pending reassignments remain answerable without rewriting past submissions."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from ax_workspace.platform.persistence import (
    TaskAssignmentRecord, TaskRecord, DecisionItemRecord, SubmissionRecord,
    SubjectVersionRecord, ReviewDecisionRecord, make_session_factory,
)
from test_task_assignments import _client, JIHO, MINA


def _legacy_pending(client, database_url):
    task = client.post('/api/tasks', headers=JIHO, json={'title': '과거에 재배정된 업무'}).json()
    with make_session_factory(database_url)() as session:
        held = session.scalar(select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task['task_id'])))
        held.status = 'superseded'
        held.superseded_at = datetime.now(UTC) - timedelta(days=1)
        target = session.get(TaskRecord, UUID(task['task_id']))
        target.version += 1
        legacy = TaskAssignmentRecord(task_id=target.id, assignee_id='mina', assigned_by='jiho', assignment_kind='direct', status='pending', created_at=held.superseded_at)
        session.add(legacy)
        session.commit()
        return str(legacy.id), str(target.id), target.version, legacy.created_at


@pytest.mark.parametrize('command', ['accept', 'decline', 'cancel'])
def test_legacy_assignment_only_materializes_missing_acceptance_on_an_authorized_command(tmp_path, command):
    client, database_url = _client(tmp_path)
    assignment_id, task_id, version, created_at = _legacy_pending(client, database_url)
    url = f'/api/action-items/{assignment_id}'
    item = client.get(url, headers=MINA)
    assert item.status_code == 200
    assert client.get('/api/action-items', headers=MINA).status_code == 200
    denied = client.post(url + '/commands/accept', headers=JIHO, json={'expected_version': version})
    assert denied.status_code == 422
    with make_session_factory(database_url)() as session:
        assert session.query(SubmissionRecord).count() == 0
        assert session.get(TaskAssignmentRecord, UUID(assignment_id)).source_decision_item_id is None
    if command == 'cancel':
        application = client.app.state.workflow_application
        with application._session_factory() as session:
            result = application._assignments(session).cancel(application.authenticated_principal('jiho'), UUID(assignment_id))
            session.commit()
            assert result['status'] == 'cancelled'
    else:
        response = client.post(url + '/commands/' + command, headers=MINA, json={'expected_version': version, 'reason': '진행 불가' if command == 'decline' else None})
        assert response.status_code == 200, response.text
    with make_session_factory(database_url)() as session:
        assignment = session.get(TaskAssignmentRecord, UUID(assignment_id))
        assert assignment.created_at.replace(tzinfo=None) == created_at.replace(tzinfo=None)
        decision_item = session.get(DecisionItemRecord, assignment.source_decision_item_id)
        assert decision_item.status == 'resolved'
        submission = session.scalar(select(SubmissionRecord).where(SubmissionRecord.decision_item_id == decision_item.id))
        assert submission.decision_policy_snapshot['legacy_assignment_id'] == assignment_id
        assert submission.submitted_at.replace(tzinfo=None) > created_at.replace(tzinfo=None)
        snapshot = session.get(SubjectVersionRecord, submission.subject_version_id).snapshot
        assert snapshot['task_version'] == version and snapshot['title'] == '과거에 재배정된 업무'
        assert session.query(ReviewDecisionRecord).count() == (0 if command == 'cancel' else 1)


def test_legacy_repair_and_decision_roll_back_together_and_recheck_cached_task_version(tmp_path, monkeypatch):
    from ax_workspace.platform.work_tasks import ActivityLedger
    from ax_workspace.modules.work.application import InvalidTaskTransition
    client, database_url = _client(tmp_path)
    assignment_id, task_id, version, _ = _legacy_pending(client, database_url)
    application = client.app.state.workflow_application
    principal = application.authenticated_principal('mina')
    record = ActivityLedger.record

    def fail_after_record(self, **kwargs):
        record(self, **kwargs)
        raise RuntimeError('injected assignment effect failure')

    with monkeypatch.context() as failure:
        failure.setattr(ActivityLedger, 'record', fail_after_record)
        with pytest.raises(RuntimeError, match='assignment effect failure'):
            client.post(f'/api/action-items/{assignment_id}/commands/accept', headers=MINA, json={'expected_version': version})
    with application._session_factory() as cached:
        target = cached.get(TaskRecord, UUID(task_id))
        assert cached.get(TaskAssignmentRecord, UUID(assignment_id)).source_decision_item_id is None
        assert cached.query(SubmissionRecord).count() == cached.query(ReviewDecisionRecord).count() == 0
        with application._session_factory() as other:
            other.get(TaskRecord, UUID(task_id)).version += 1
            other.commit()
        assert target.version == version
        with pytest.raises(InvalidTaskTransition, match='stale'):
            application._assignments(cached).accept(principal, UUID(assignment_id), expected_task_version=version)
        assert cached.query(SubmissionRecord).count() == 0


def test_legacy_assignment_history_keeps_the_answered_content_after_later_task_edits(tmp_path):
    client, database_url = _client(tmp_path)
    assignment_id, task_id, version, _ = _legacy_pending(client, database_url)
    url = f'/api/action-items/{assignment_id}'
    assert client.post(url + '/commands/accept', headers=MINA, json={'expected_version': version}).status_code == 200
    recorded = client.get(url, headers=MINA).json()['rounds']
    assert recorded[0]['capture_kind'] == 'legacy_assignment_on_command'
    edited = client.patch(f'/api/tasks/{task_id}', headers=MINA, json={'expected_version': version, 'title': '수락 이후 바뀐 내용'})
    assert edited.status_code == 200, edited.text
    assert client.get(url, headers=MINA).json()['rounds'] == recorded
