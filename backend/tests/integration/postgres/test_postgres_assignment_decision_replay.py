"""Both tabs offered the same assignment decision must receive one outcome.

**수락 대기 배정은 W1 이후 과거 행에만 있다** — 신규 배정은 즉시 활성 담당으로 선다 (WORK-001 Phase 4).
그 회차에 답하는 코드는 그대로이므로 예전 배포가 남긴 모양에서 계속 검증한다.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID
import pytest
from sqlalchemy import select, func
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.action_center import TaskAssignmentActionHandler
from ax_workspace.platform.persistence import ReviewDecisionRecord, TaskAssignmentRecord, make_session_factory
from legacy_acceptance import pending_assignment
from test_postgres_integration import _postgres_test_url, _conversation_client


@pytest.mark.integration
@pytest.mark.parametrize('command', ['accept', 'decline'])
def test_assignment_simultaneous_same_decision_replays_after_the_owner_lock(monkeypatch, command):
    url = _postgres_test_url()
    reset_database(url)
    client = _conversation_client(url)
    jiho, mina = {'X-Demo-Persona': 'jiho'}, {'X-Demo-Persona': 'mina'}
    assignment = pending_assignment(client, url, jiho, title='동시 판단', assignee_id='mina')
    path = f"/api/action-items/{assignment['assignment_id']}"
    item = client.get(path, headers=mina).json()
    payload = {'expected_version': item['expected_version'], **({'reason': '다른 담당자가 필요합니다'} if command == 'decline' else {})}
    execute = TaskAssignmentActionHandler.execute
    barrier = Barrier(2)

    def after_offer(self, principal, item, command, payload):
        barrier.wait(timeout=10)
        return execute(self, principal, item, command, payload)

    monkeypatch.setattr(TaskAssignmentActionHandler, 'execute', after_offer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post(path + '/commands/' + command, headers=mina, json=payload), range(2)))
    assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
    with make_session_factory(url)() as session:
        saved = session.get(TaskAssignmentRecord, UUID(assignment['assignment_id']))
        assert saved.status == ('active' if command == 'accept' else 'declined')
        assert session.scalar(select(func.count()).select_from(ReviewDecisionRecord).where(ReviewDecisionRecord.id == saved.source_review_decision_id)) == 1
