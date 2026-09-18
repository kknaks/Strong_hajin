"""업무 구조·생명주기 v2 — **실제 PostgreSQL 에서의 경합** (SPEC-003 §3 S-16 3~5 · §6 C2 · WORK-002 Phase 8).

**여기서 무엇을 증명하는가.** 부딪히는 두 명령을 **같은 순간의 두 transaction** 으로 실제로 겹치게
만들어, 잠금과 유일 제약이 남기는 상태를 본다. 계약 테스트(`tests/contract/test_task_lifecycle_v2.py`
의 `..._c2_two_commands_that_race_resolve_without_a_contradiction`)는 **순서대로 들어온 두 명령**을
못 박고 그 자리에서 「진짜 경합은 PostgreSQL 의 몫」이라고 적었다 — 그 몫이 이 파일이다.

**우연히 순차로 돈 것을 동시성 증거로 쓰지 않는다.** 각 시험은 `Barrier`/`Event` 로 **두 번째
transaction 이 열린 뒤에야** 첫 번째가 잠금을 잡도록 만든다. 겹치지 못하면 barrier 가 시간 초과로
깨져 시험이 **실패한다** — 조용히 순차 실행으로 흘러가 통과하지 않는다.

**미정은 미정으로 둔다 (EU-17).** 수락과 철회가 부딪힐 때 **어느 쪽이 이기는지는 SPEC 이 열어 둔
자리**다 (§3 S-16 3 · DEC-002 M-14). 이 파일은 승자를 고정하지 않고 **둘 다 적용되는 일이 없다**는
것과 **이긴 쪽의 상태가 스스로 모순되지 않는다**는 것만 단언한다.

**이미 정확한 시험이 있는 자리는 다시 짓지 않는다.**
- 판단함(`/api/action-items/.../commands/{accept,decline}`) 경유의 **같은 답 동시 재전송**은
  `test_postgres_assignment_decision_replay.py::test_assignment_simultaneous_same_decision_replays_after_the_owner_lock`
  이 이미 못 박았다. 여기서는 **v2 담당 교체 제안**(`/api/task-assignments/{id}/accept`)의 같은
  자리만 더한다.
- 같은 멱등 키의 **동시 발송**이 요청 한 건·업무 한 건이라는 것은
  `test_postgres_integration.py::test_postgres_promotes_one_meeting_candidate_into_one_task_under_concurrency`
  가 이미 갖고 있다. 여기서는 **본인 업무 생성**(`POST /api/tasks`)의 같은 자리와, 영수증을 주기 전의
  **지금 권한 재검사**(K-2)를 더한다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select, text

from ax_workspace.platform.persistence import (
    ReviewDecisionRecord,
    TaskAssignmentRecord,
    TaskRecord,
    WorkRequestRecord,
    make_session_factory,
)
from ax_workspace.platform.work_tasks import (
    SqlAlchemyTaskAssignmentRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRequestRepository,
)

from test_postgres_integration import _postgres_test_url
from v2_pg_support import (
    JIHO,
    MINA,
    MINSEOK,
    assignment_ledger,
    complete,
    detail,
    own_task,
    pg_stack,
    reassign,
    request_state,
    request_version,
    send_request,
    start,
    version,
    accept_request,
)

#: barrier 가 이 안에 풀리지 않으면 두 명령이 **겹치지 못한 것**이고, 그때는 시험이 깨진다.
RACE_TIMEOUT = 30.0


def _gate_once_per_thread(barrier: Barrier) -> Any:
    """한 thread 가 **처음 지나갈 때만** 기다리게 한다 — 같은 seam 을 두 번 밟아도 교착되지 않는다."""
    seen: set[int] = set()
    guard = Lock()

    def wait() -> None:
        ident = threading.get_ident()
        with guard:
            first = ident not in seen
            seen.add(ident)
        if first:
            barrier.wait(timeout=RACE_TIMEOUT)

    return wait


def _one_won(first: Any, second: Any) -> tuple[Any, Any]:
    """정확히 하나가 서고 하나가 **정의된 거절**이다 — 500 도, 둘 다 200 도 아니다."""
    codes = [first.status_code, second.status_code]
    assert codes.count(200) == 1, [first.text, second.text]
    loser = second if first.status_code == 200 else first
    assert loser.status_code in {400, 404, 409, 422}, loser.text
    return (first, second) if first.status_code == 200 else (second, first)


# ---------------------------------------------------------------------------
# C2 ① — 같은 요청의 수락 ↔ 철회 (S-16 3 · K-4 · 미정 EU-17)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_accepting_and_withdrawing_one_request_never_both_land(monkeypatch) -> None:
    """수락과 철회가 **같은 원 회차로 같은 순간에** 와도 둘 다 반영되는 일은 없다 (S-16 3 · K-4).

    두 명령은 같은 요청 행을 `FOR UPDATE` 로 집으므로 PostgreSQL 이 줄을 세운다. 진 쪽은 회차가
    이미 오른 것을 보고 정의된 거절로 끝난다.

    **어느 쪽이 이기는지는 단언하지 않는다 (EU-17).** 그것은 SPEC 이 열어 둔 자리이고, 여기서 한쪽을
    고정하면 미정을 테스트로 확정해 버린다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    sent = send_request(client, "같은 순간에 수락과 철회", "jiho", MINA)
    request_id, task_id = sent["request_id"], sent["task_id"]
    opened = request_version(client, request_id, MINA)

    gate = _gate_once_per_thread(Barrier(2))
    original = SqlAlchemyWorkRequestRepository.request

    def racing_request(self, identifier, **kwargs):
        # 잠그러 들어가기 **직전**에 만난다 — 두 transaction 이 모두 열린 뒤에 첫 잠금이 일어난다.
        if kwargs.get("lock"):
            gate()
        return original(self, identifier, **kwargs)

    monkeypatch.setattr(SqlAlchemyWorkRequestRepository, "request", racing_request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepting = pool.submit(
            lambda: client.post(
                f"/api/work-requests/{request_id}/accept", headers=JIHO, json={"expected_version": opened}
            )
        )
        withdrawing = pool.submit(
            lambda: client.post(
                f"/api/work-requests/{request_id}/withdraw", headers=MINA, json={"expected_version": opened}
            )
        )
        accepted, withdrawn = accepting.result(timeout=60), withdrawing.result(timeout=60)

    _one_won(accepted, withdrawn)
    state = request_state(client, request_id, MINA)
    with make_session_factory(database_url)() as session:
        task = session.get(TaskRecord, UUID(task_id))
        rows = list(
            session.scalars(select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id)))
        )
        request = session.get(WorkRequestRecord, UUID(request_id))
    statuses = sorted(row.status for row in rows)

    # 요청은 **정확히 한 답**을 갖는다. 회차도 한 칸만 올랐다 — 두 명령이 겹쳐 두 번 오르지 않는다.
    assert state in {"accepted", "withdrawn"}
    assert int(request.version) == opened + 1, (state, request.version)

    if accepted.status_code == 200:
        # 수락이 이겼다 — 업무는 그대로 열려 있고 **담당은 정확히 하나**다 (K-3).
        assert state == "accepted"
        assert task.state == "open" and task.cancel_reason is None
        assert statuses.count("active") == 1, statuses
        assert next(row.assignee_id for row in rows if row.status == "active") == "jiho"
    else:
        # 철회가 이겼다 — 업무는 취소된 채로 남고 **아무도 들지 않는다.**
        assert state == "withdrawn"
        assert task.state == "cancelled" and task.cancel_reason == "request_withdrawn"
        assert "active" not in statuses, statuses
    # 어느 쪽이 이겼든 **진 명령의 흔적이 상태에 섞이지 않는다.**
    assert (task.cancel_reason is None) == (state == "accepted")


# ---------------------------------------------------------------------------
# C2 ② — 담당 교체의 수락 ↔ 거절 (S-16 4 · K-3 · 정책 V-18)
# ---------------------------------------------------------------------------


def _active_watcher(database_url: str, task_id: str, stop: Event) -> Any:
    """경합이 도는 **내내** 활성 담당 수를 밖에서 센다 — 책임이 비는 순간이 있으면 여기 잡힌다.

    읽기마다 자기 transaction 이므로(READ COMMITTED) 다른 쪽이 커밋한 것만 본다. 교체가 한 덩어리면
    본 값은 언제나 1 이고, 종료와 활성화가 갈라져 있으면 0 또는 2 가 섞인다.
    """
    factory = make_session_factory(database_url)
    seen: list[int] = []

    def watch() -> list[int]:
        while not stop.is_set():
            with factory() as session:
                seen.append(
                    int(
                        session.scalar(
                            select(func.count())
                            .select_from(TaskAssignmentRecord)
                            .where(
                                TaskAssignmentRecord.task_id == UUID(task_id),
                                TaskAssignmentRecord.status == "active",
                            )
                        )
                    )
                )
        return seen

    return watch


@pytest.mark.integration
def test_postgres_handover_acceptance_and_decline_never_leave_the_task_unheld(monkeypatch) -> None:
    """담당 교체의 수락과 거절이 부딪혀도 **활성 담당이 0명이나 2명이 되지 않는다** (S-16 4 · K-3).

    두 명령은 `_pending_target` 에서 **같은 Task 행**을 `FOR UPDATE` 로 집는다 — 그 잠금이 줄을
    세우고, 진 쪽은 제안이 이미 닫힌 것을 본다. 교체가 서는 경우에도 **이전 종료와 새 활성화가 한
    transaction** 이라 밖에서 보는 활성 담당은 내내 하나다 (정책 V-18).
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    sent = send_request(client, "담당이 바뀔 요청", "jiho", MINA)
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]
    held = assignment_ledger(client, task_id, JIHO)
    assert held["current"]["assignee_id"] == "jiho" and held["pending"] is None

    proposal_id = reassign(client, task_id, "mina", JIHO)["assignment_id"]
    ledger = assignment_ledger(client, task_id, JIHO)
    assert ledger["current"]["assignee_id"] == "jiho" and ledger["pending"]["assignee_id"] == "mina"

    stop = Event()
    watcher = _active_watcher(database_url, task_id, stop)
    gate = _gate_once_per_thread(Barrier(2))
    original = SqlAlchemyTaskAssignmentRepository.task_by_id

    def racing_task(self, identifier, **kwargs):
        if kwargs.get("lock"):
            gate()
        return original(self, identifier, **kwargs)

    monkeypatch.setattr(SqlAlchemyTaskAssignmentRepository, "task_by_id", racing_task)

    with ThreadPoolExecutor(max_workers=3) as pool:
        watching = pool.submit(watcher)
        taking = pool.submit(lambda: client.post(f"/api/task-assignments/{proposal_id}/accept", headers=MINA))
        refusing = pool.submit(
            lambda: client.post(
                f"/api/task-assignments/{proposal_id}/decline", headers=MINA, json={"reason": "다른 일이 많습니다"}
            )
        )
        took, refused = taking.result(timeout=60), refusing.result(timeout=60)
        stop.set()
        observed = watching.result(timeout=60)

    _one_won(took, refused)
    # **책임 공백이 없다** — 경합이 도는 동안 밖에서 본 활성 담당은 언제나 하나였다.
    assert observed, "감시 thread 가 한 번도 읽지 못했다면 이 단언은 아무것도 말하지 않는다"
    assert set(observed) == {1}, sorted(set(observed))

    after = assignment_ledger(client, task_id, JIHO)
    with make_session_factory(database_url)() as session:
        rows = list(
            session.scalars(select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id)))
        )
        task = session.get(TaskRecord, UUID(task_id))
    statuses = sorted(row.status for row in rows)
    assert statuses.count("active") == 1, statuses
    assert after["pending"] is None
    # **교체 제안의 거절은 제안만 닫는다** — 어느 쪽이 이겼든 업무는 취소되지 않는다 (O-13 · 정책 V-18).
    assert task.state != "cancelled" and task.cancel_reason is None

    if took.status_code == 200:
        assert after["current"]["assignee_id"] == "mina"
        assert statuses == sorted(["active", "superseded"]), statuses
    else:
        assert after["current"]["assignee_id"] == "jiho"
        assert statuses == sorted(["active", "declined"]), statuses


@pytest.mark.integration
def test_postgres_resending_one_handover_answer_at_once_is_still_one_answer(monkeypatch) -> None:
    """**같은 답을 같은 순간에 두 번** 보내면 **둘 다 영수증**이다 (S-16 1 · K-1).

    판단함 경유의 같은 자리는 `test_postgres_assignment_decision_replay.py::
    test_assignment_simultaneous_same_decision_replays_after_the_owner_lock` 가 이미 **200·200** 으로
    못 박았다 — 여기서는 **v2 담당 교체 제안**의 입구(`/api/task-assignments/{id}/accept`)에서 같은
    보장이 서는지 본다. 두 입구가 같은 사건에 다른 답을 내면 그것이 곧 계약의 구멍이다.

    **정당한 수신자가 같은 행에 같은 답을 다시 보낸 것은 오류가 아니다** (SPEC-003 §3 S-16 1 ·
    §4 Case Matrix 「재전송이면 영수증이 먼저」). 통신이 끊겨 다시 누른 자리이고, 그 사이에 자기
    답이 이미 접수됐다는 사실이 그 재시도를 실패로 만들지 않는다. **두 번째 effect 가 없을 뿐**이다.

    그러므로 여기서 거절 코드를 허용 목록에 넣지 않는다 — 넣으면 제품이 내는 아무 오류나
    「영수증」으로 통과하고, 이 줄은 아무것도 증명하지 못한다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    sent = send_request(client, "같은 답이 두 번 오는 교체", "jiho", MINA)
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]
    proposal_id = reassign(client, task_id, "mina", JIHO)["assignment_id"]

    gate = _gate_once_per_thread(Barrier(2))
    original = SqlAlchemyTaskAssignmentRepository.task_by_id

    def racing_task(self, identifier, **kwargs):
        if kwargs.get("lock"):
            gate()
        return original(self, identifier, **kwargs)

    monkeypatch.setattr(SqlAlchemyTaskAssignmentRepository, "task_by_id", racing_task)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = [
            future.result(timeout=60)
            for future in [
                pool.submit(lambda: client.post(f"/api/task-assignments/{proposal_id}/accept", headers=MINA))
                for _ in range(2)
            ]
        ]

    codes = [reply.status_code for reply in replies]
    assert codes == [200, 200], [reply.text for reply in replies]
    # 두 영수증이 **같은 사건**을 가리킨다 — 같은 담당 행, 같은 업무, 같은 결과 상태다.
    first, second = (reply.json() for reply in replies)
    assert first["assignment_id"] == second["assignment_id"] == proposal_id, (first, second)
    assert first["assignee_id"] == second["assignee_id"] == "mina", (first, second)
    assert first["status"] == second["status"] == "active", (first, second)
    assert first["task"]["task_id"] == second["task"]["task_id"] == task_id, (first, second)
    # 재전송이 회차를 한 번 더 올리지 않는다.
    assert first["task"]["version"] == second["task"]["version"], (first, second)

    # **응답 권한 검사는 그대로다** — 영수증이 열려 있다고 남의 답까지 받아 주지 않는다.
    for headers in (JIHO, MINSEOK):
        refused = client.post(f"/api/task-assignments/{proposal_id}/accept", headers=headers)
        assert refused.status_code == 404, (headers, refused.text)

    with make_session_factory(database_url)() as session:
        rows = list(
            session.scalars(select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id)))
        )
        # **이 교체에 답한 사람의 판단만** 센다 — 요청을 수락할 때 지호가 남긴 판단은 다른 사건이다.
        answers = list(
            session.scalars(select(ReviewDecisionRecord).where(ReviewDecisionRecord.actor_member_id == "mina"))
        )
    statuses = sorted(row.status for row in rows)
    # 담당 행은 **둘 그대로**(이전·새것)이고 활성은 하나다 — 재전송이 세 번째 행을 만들지 않는다.
    assert len(rows) == 2, statuses
    assert statuses.count("active") == 1, statuses
    # 판단도 **한 번**이다.
    assert [row.decision for row in answers] == ["accept"], [
        (row.actor_member_id, row.decision, str(row.id)) for row in answers
    ]


# ---------------------------------------------------------------------------
# C2 ③ — 상위 완료 ↔ 하위 재개 (S-16 5 · 정책 L-13·L-14)
# ---------------------------------------------------------------------------


def _parent_and_finished_child(client) -> tuple[str, str]:
    """상위 하나와 그 아래 **끝난 하위** 하나 — 재개가 가능한 자리까지 실제 명령으로 세운다."""
    parent = own_task(client, "상위 업무", MINA)
    start(client, parent, MINA)
    child = own_task(client, "하위 업무", MINA, parent_task_id=parent)
    start(client, child, MINA)
    assert complete(client, child, MINA).status_code == 200
    return parent, child


def _blocked_backends(database_url: str) -> int:
    """지금 **잠금을 기다리는** backend 수. 0 이 아니면 누군가 실제로 줄을 서 있다는 뜻이다."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            # **이 데이터베이스 안에서만** 센다 — 같은 서버의 다른 DB 에서 도는 일이 답을 바꾸면
            # 「실제로 기다렸다」는 증거가 아니다. 행 잠금을 기다리는 쪽은 `transactionid` 잠금에
            # 매달리는데 그 행의 `pg_locks.database` 는 비어 있으므로, 세는 자리는 `pg_stat_activity`
            # 쪽이다 — 누가 누구를 막고 있는지까지 함께 답한다.
            return int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        " WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "   AND cardinality(pg_blocking_pids(pid)) > 0"
                    )
                )
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgres_a_parent_completion_waits_for_the_child_reopen_that_holds_it(monkeypatch) -> None:
    """재개가 상위 행을 먼저 잡으면 **상위의 완료가 실제로 줄을 선다** (S-16 5 · 정책 L-13).

    재개는 하위 → 상위 순으로 잠그고, 완료는 **자기 한 행**만 잡는다. 그래서 재개가 상위를 쥔 동안
    완료는 PostgreSQL 안에서 **기다린다** — 이 시험은 그 기다림을 `pg_locks` 로 **실제로 확인한 뒤에야**
    재개를 놓아준다. 우연히 순차로 돈 것을 동시성 증거로 쓰지 않으려면 그 확인이 있어야 한다.

    풀려난 뒤 완료는 다시 읽은 하위가 열린 것을 보고 **막힌다** — 거짓 완료가 남지 않는다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    parent, child = _parent_and_finished_child(client)

    holds_parent, may_finish = Event(), Event()
    waited: list[int] = []
    original = SqlAlchemyTaskRepository.task_by_id

    def gated_task_by_id(self, identifier, **kwargs):
        row = original(self, identifier, **kwargs)
        # **상위 행을 잡은 직후** — 이 시점부터 완료 쪽은 기다릴 수밖에 없다.
        if kwargs.get("lock") and str(identifier) == parent and not holds_parent.is_set():
            holds_parent.set()
            assert may_finish.wait(timeout=RACE_TIMEOUT), "완료 쪽이 잠금을 기다리는 것을 확인하지 못했다"
        return row

    monkeypatch.setattr(SqlAlchemyTaskRepository, "task_by_id", gated_task_by_id)

    def reopen_child():
        return client.post(
            f"/api/tasks/{child}/reopen", headers=MINA, json={"expected_version": version(client, child, MINA)}
        )

    def complete_parent():
        assert holds_parent.wait(timeout=RACE_TIMEOUT), "재개가 상위 행을 잡지 못했다"
        return complete(client, parent, MINA)

    def confirm_the_wait():
        try:
            assert holds_parent.wait(timeout=RACE_TIMEOUT), "재개가 상위 행을 잡지 못했다"
            deadline = time.monotonic() + RACE_TIMEOUT
            while time.monotonic() < deadline:
                blocked = _blocked_backends(database_url)
                if blocked:
                    waited.append(blocked)
                    return
        finally:
            may_finish.set()

    with ThreadPoolExecutor(max_workers=3) as pool:
        reopening = pool.submit(reopen_child)
        completing = pool.submit(complete_parent)
        watching = pool.submit(confirm_the_wait)
        watching.result(timeout=90)
        reopened, completed = reopening.result(timeout=90), completing.result(timeout=90)

    # **실제 경합이었다** — 완료가 PostgreSQL 안에서 잠금을 기다린 것을 밖에서 봤다.
    assert waited, "완료가 한 번도 잠금을 기다리지 않았다 — 두 명령이 겹치지 않았다는 뜻이다"
    assert reopened.status_code == 200, reopened.text
    assert completed.status_code == 409, completed.text
    assert "끝나지 않은 하위 업무" in completed.text and "하위 업무" in completed.text

    parent_view, child_view = detail(client, parent, MINA), detail(client, child, MINA)
    assert child_view["state"] == "in_progress"
    assert parent_view["state"] == "in_progress"
    _no_open_child_under_a_settled_parent(parent_view, child_view, reopened, completed)


@pytest.mark.integration
def test_postgres_a_child_reopen_is_refused_by_the_parent_that_completed_first(monkeypatch) -> None:
    """반대 순서 — 상위가 먼저 끝나면 **그 뒤의 재개가 막힌다** (`WORK_REOPEN_PARENT_DONE`).

    재개는 하위를 잡은 채 상위를 읽으러 가는 자리에서 멈춘다. 그동안 완료는 자기 한 행만 잡으므로
    막히지 않고 통째로 돈다 — **인위 교착이 아니다.** 풀려난 재개는 그제야 상위를 잡고 **커밋된**
    `done` 을 본다. 잠금 없이 읽던 예전 판에서는 이 자리가 옛 상태를 보고 지나갔다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    parent, child = _parent_and_finished_child(client)

    holds_child, parent_settled = Event(), Event()
    original = SqlAlchemyTaskRepository.task_by_id

    def gated_task_by_id(self, identifier, **kwargs):
        # **상위를 잡으러 들어가기 직전** — 하위는 이미 잡혀 있고 상위는 아직 비어 있다.
        if kwargs.get("lock") and str(identifier) == parent and not holds_child.is_set():
            holds_child.set()
            assert parent_settled.wait(timeout=RACE_TIMEOUT), "상위 완료가 이 안에 끝나지 않았다"
        return original(self, identifier, **kwargs)

    monkeypatch.setattr(SqlAlchemyTaskRepository, "task_by_id", gated_task_by_id)

    def reopen_child():
        return client.post(
            f"/api/tasks/{child}/reopen", headers=MINA, json={"expected_version": version(client, child, MINA)}
        )

    def complete_parent():
        try:
            assert holds_child.wait(timeout=RACE_TIMEOUT), "재개가 상위를 읽는 자리에 닿지 않았다"
            return complete(client, parent, MINA)
        finally:
            parent_settled.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reopening = pool.submit(reopen_child)
        completing = pool.submit(complete_parent)
        reopened, completed = reopening.result(timeout=90), completing.result(timeout=90)

    assert completed.status_code == 200, completed.text
    assert reopened.status_code == 409, reopened.text
    assert "상위 업무가 완료" in reopened.text, reopened.text

    parent_view, child_view = detail(client, parent, MINA), detail(client, child, MINA)
    assert parent_view["state"] == "done"
    # 재개가 막혔으므로 하위는 끝난 채로 남는다 — 실패한 명령이 회차를 올리지도 않는다.
    assert child_view["state"] == "done"
    _no_open_child_under_a_settled_parent(parent_view, child_view, reopened, completed)


def _no_open_child_under_a_settled_parent(parent_view, child_view, reopened, completed) -> None:
    """두 시험이 함께 지키는 한 줄 — **끝난 상위 아래 열린 하위가 없다** (정책 L-13·L-14)."""
    settled_parent = parent_view["state"] in {"done", "cancelled"}
    open_child = child_view["state"] in {"open", "in_progress", "blocked"}
    assert not (settled_parent and open_child), (
        f"상위={parent_view['state']}(완료 {completed.status_code}) · "
        f"하위={child_view['state']}(재개 {reopened.status_code})"
    )


# ---------------------------------------------------------------------------
# C1 — 같은 키의 동시 생성과, 영수증을 주기 전의 권한 재검사 (S-16 1·2 · K-1·K-2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_the_same_creation_key_sent_at_once_stands_one_task() -> None:
    """같은 멱등 키의 **동시 생성**은 업무 한 건이다 (S-16 1 · K-1).

    발송 쪽의 같은 자리는 `test_postgres_integration.py::
    test_postgres_promotes_one_meeting_candidate_into_one_task_under_concurrency` 가 갖고 있다 —
    여기는 `POST /api/tasks`(본인 업무) 쪽이다. 진 transaction 은 멱등 원장의 unique 에 막혀 통째로
    사라지고, 그 뒤 영수증으로 이긴 쪽을 읽는다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    headers = {**MINA, "Idempotency-Key": "one-intent-two-clicks"}
    gate = Barrier(2)

    def create():
        gate.wait(timeout=RACE_TIMEOUT)
        return client.post("/api/tasks", headers=headers, json={"title": "한 번만 서야 하는 업무"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = (future.result(timeout=60) for future in [pool.submit(create), pool.submit(create)])

    assert [first.status_code, second.status_code] == [201, 201], [first.text, second.text]
    assert first.json()["task_id"] == second.json()["task_id"]
    with make_session_factory(database_url)() as session:
        tasks = list(session.scalars(select(TaskRecord)))
        rows = list(session.scalars(select(TaskAssignmentRecord)))
    assert [task.title for task in tasks] == ["한 번만 서야 하는 업무"]
    # 본인 업무이므로 담당은 **선 그대로 활성**이고, 그 행도 하나다.
    assert [(row.assignee_id, row.status) for row in rows] == [("mina", "active")]


@pytest.mark.integration
def test_postgres_a_resent_creation_key_asks_for_the_right_to_read_again() -> None:
    """영수증을 주기 **전에** 지금의 열람 권한을 다시 묻는다 — 잃었으면 존재를 숨긴다 (S-16 2 · K-2).

    같은 키의 재전송은 이미 선 요청을 다시 읽어 돌려주는 길이다. 그 사이에 읽을 권한이 회수됐다면
    **재전송이라는 이유로 내주지 않는다** — 재전송의 신원이 열람 권한을 대신하지 않는다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    application = client.app.state.workflow_application
    headers = {**MINA, "Idempotency-Key": "receipt-after-revocation"}

    created = client.post(
        "/api/work-requests", headers=headers, json={"title": "권한이 회수될 요청", "assignee_id": "jiho"}
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["request_id"]

    # 같은 키의 재전송은 아직 영수증이다.
    receipt = client.post(
        "/api/work-requests", headers=headers, json={"title": "권한이 회수될 요청", "assignee_id": "jiho"}
    )
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["request_id"] == request_id

    yuna = application.authenticated_principal("yuna")
    application.set_role_capabilities(
        yuna,
        "role:member",
        [
            capability
            for capability in sorted(application.authenticated_principal("mina").capabilities)
            if capability != "work_request.read"
        ],
        expected_version=1,
        reason="요청 열람 권한 회수",
    )

    hidden = client.post(
        "/api/work-requests", headers=headers, json={"title": "권한이 회수될 요청", "assignee_id": "jiho"}
    )
    assert hidden.status_code in {403, 404}, hidden.text
    with make_session_factory(database_url)() as session:
        # 거절이 새 요청을 만들지도, 있던 것을 지우지도 않는다.
        assert [str(row.id) for row in session.scalars(select(WorkRequestRecord))] == [request_id]
