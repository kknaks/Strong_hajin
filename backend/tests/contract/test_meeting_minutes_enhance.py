"""회의록 생성 고도화 — 새 세션 재시도 · AI 맥락 목록 · 용어 보정 표 (SPEC-010 §4.5 · §4.6 · §4.7 · WORK-012 Phase WP2-BE).

provider 는 대역이다 — 실물 Codex 의 900초 재합성·정정 품질은 코디 E2E 가 1회 확인한다(WORK-012 P-5).
"""
from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select

from ax_workspace.modules.meetings.batch import BATCH_CHARS, CAUSE_TRANSCRIPT, AiCallTimedOut
from ax_workspace.platform.persistence import MeetingBatchRunRecord, MemberRecord, ProjectRecord, TaskRecord

import test_meeting_finalize as finalize_support
from test_meeting_memo_batch import MINA, _agenda, _blocks, _line, _output, _running, _stack


def _runs(application, meeting_id: str) -> list[tuple[str, int, int]]:
    with application._session_factory() as session:
        rows = session.scalars(
            select(MeetingBatchRunRecord).where(MeetingBatchRunRecord.meeting_id == UUID(meeting_id)).order_by(MeetingBatchRunRecord.seq)
        )
        return [(row.status, row.from_seq, row.to_seq) for row in rows]


def _session_ref(application, meeting_id: str) -> str | None:
    with application._session_factory() as session:
        return application._meetings(session).ai_session_ref(UUID(meeting_id))


# ── 배치 · 웜스타트 — timeout 은 새 세션 1회 (SPEC-010 §4.6) ─────────────────────────────────────────────────────


def test_a_batch_timeout_retries_once_in_a_new_session_and_swaps_the_session_only_on_success(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    assert _session_ref(application, meeting_id) == "session-1"
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [AiCallTimedOut("timed out"), _output([_agenda("새 세션이 낸 화제", [_line("줄")])])]

    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()

    assert [run["session_ref"] for run in agent.runs] == ["session-1"]  # 같은 세션은 한 번만
    [fresh] = agent.fresh
    # 새 세션은 웜스타트 맥락 + AI 맥락 목록 + 그 배치의 미처리 구간을 한 turn 에 싣는다.
    assert "## 이 회의" in fresh["prompt"] and "## 조직 맥락 목록" in fresh["prompt"]
    assert "앞 배치까지 네 벌" in fresh["prompt"] and "확정 발화" in fresh["prompt"]
    assert _session_ref(application, meeting_id) == "session-fresh"  # 성공한 순간 갈아 끼운다
    assert [status for status, _, _ in _runs(application, meeting_id)] == ["succeeded"]


def test_a_failed_new_session_keeps_the_old_session_and_the_span_joins_the_next_batch(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [AiCallTimedOut("timed out"), AiCallTimedOut("timed out again")]

    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert _session_ref(application, meeting_id) == "session-1"  # 실패하면 옛 참조를 둔다
    [(status, from_seq, to_seq)] = _runs(application, meeting_id)
    assert status == "failed"

    # 다음 배치는 같은 구간부터 다시 싣는다(커서는 성공분만 전진) — 그리고 옛 세션을 다시 시도한다.
    agent.script = [_output([_agenda("다음 회차", [_line("줄")])])]
    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert _runs(application, meeting_id)[-1][:2] == ("succeeded", from_seq)
    assert agent.runs[-1]["session_ref"] == "session-1"


def test_speech_arriving_during_the_retry_waits_for_the_next_batch_and_batches_never_overlap(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    seen: dict = {}
    original = agent.run_batch_in_new_session

    def slow_new_session(**request):
        # 재시도가 도는 동안 발화가 더 오고 트리거가 울린다 — 배치는 회의마다 한 줄이라 새 배치가 서지 않는다.
        _blocks(application, meeting_id, count=2, chars=BATCH_CHARS, start_ms=900_000, text="나")
        seen["evaluated_during_retry"] = application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
        return original(**request)

    agent.run_batch_in_new_session = slow_new_session
    agent.script = [AiCallTimedOut("timed out"), _output([_agenda("화제", [_line("줄")])])]
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    first_to = _runs(application, meeting_id)[0][2]
    application.meeting_batch.drain()

    assert seen["evaluated_during_retry"] is False
    assert "나" not in agent.fresh[0]["prompt"].split("확정 발화:", 1)[1]  # 재시도 turn 은 그 배치의 구간만
    # 재시도가 끝난 자리에서 미뤄 둔 트리거를 갚는다 — 그 사이 온 발화가 다음 배치로 간다(새 세션을 이어 쓴다).
    assert len(_runs(application, meeting_id)) == 2
    assert _runs(application, meeting_id)[1][1] > first_to
    assert agent.runs[-1]["session_ref"] == "session-fresh"


def test_a_warm_start_timeout_opens_a_new_session_once(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    attempts: list[str] = []
    original = agent.open_session

    def flaky_open(**request):
        attempts.append(request["prompt"])
        if len(attempts) == 1:
            raise AiCallTimedOut("warm start timed out")
        return original(**request)

    agent.open_session = flaky_open
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    assert len(attempts) == 2 and all("## 조직 맥락 목록" in prompt for prompt in attempts)
    assert _session_ref(application, meeting_id) == "session-1"


def test_a_meeting_without_a_session_opens_one_with_its_next_batch(tmp_path) -> None:
    """웜스타트가 끝내 못 열었으면 「다음 배치에 합친다」 — 그 배치가 새 세션으로 연다(SPEC-010 §4.6)."""
    client, application, agent = _stack(tmp_path)
    agent.session_ref = None  # 웜스타트가 세션을 못 연다
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    assert _session_ref(application, meeting_id) is None
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [_output([_agenda("화제", [_line("줄")])])]

    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert agent.runs == [] and len(agent.fresh) == 1
    assert _session_ref(application, meeting_id) == "session-fresh"


# ── AI 맥락 목록 — 조직 전체 · 그 순간 DB (SPEC-010 §4.5) ────────────────────────────────────────────────────────


def _catalog_payload(application) -> dict:
    text = application.ai_context_catalog()
    assert text.startswith("## 조직 맥락 목록")
    return json.loads(text.strip().splitlines()[-1])


def test_the_catalog_is_every_project_every_open_task_and_every_active_member(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    payload = _catalog_payload(application)
    with application._session_factory() as session:
        projects = set(map(str, session.scalars(select(ProjectRecord.id))))
        open_tasks = set(map(str, session.scalars(select(TaskRecord.id).where(TaskRecord.state.not_in(("done", "cancelled"))))))
        closed_tasks = set(map(str, session.scalars(select(TaskRecord.id).where(TaskRecord.state.in_(("done", "cancelled"))))))
        members = set(session.scalars(select(MemberRecord.id).where(MemberRecord.record_status == "active")))
    assert {row["id"] for row in payload["projects"]} == projects
    assert {row["id"] for row in payload["tasks"]} == open_tasks
    assert not closed_tasks & {row["id"] for row in payload["tasks"]}
    assert {row["id"] for row in payload["members"]} == members
    assert set(payload["members"][0]) == {"id", "name", "unit", "position"}
    assert any(row["unit"] for row in payload["members"])


def test_a_new_project_shows_up_on_the_next_call(tmp_path) -> None:
    """별도 테이블이 없다 — 호출 때마다 DB 를 읽으므로 새 프로젝트가 다음 호출부터 들어간다."""
    from datetime import UTC, datetime

    _, application, _ = _stack(tmp_path)
    before = {row["name"] for row in _catalog_payload(application)["projects"]}
    with application._session_factory() as session:
        now = datetime.now(UTC)
        session.add(ProjectRecord(name="새로 선 프로젝트", state="active", created_by_actor_id="mina", created_at=now, updated_at=now))
        session.commit()
    after = {row["name"] for row in _catalog_payload(application)["projects"]}
    assert after - before == {"새로 선 프로젝트"}


# ── 용어 보정 표 — 최상위 `term_corrections` · null / [] / 행 (SPEC-010 §4.7-4 · 코디 판정 (A)) ───────────────────


def _finalized_with(tmp_path, *, corrections):
    client, application, agent = finalize_support._stack(tmp_path)
    made = finalize_support._summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    memo = made["agendas"][0]["agenda_id"]
    finalize_support._blocks(application, meeting_id)
    body = json.loads(finalize_support._output([
        finalize_support._agenda(
            "첫 안건", merged_from=[memo],
            lines=[finalize_support._line("합성이 낸 줄", evidence=[{"from_ms": 0, "to_ms": 900}])],
        )
    ]))
    if corrections is not None:
        body["term_corrections"] = corrections
    agent.script = [json.dumps(body, ensure_ascii=False)]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True
    return client, meeting_id


def test_a_meeting_that_was_never_corrected_answers_null(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert "term_corrections" in detail and detail["term_corrections"] is None


def test_a_correction_pass_with_nothing_to_fix_answers_an_empty_list(tmp_path) -> None:
    client, meeting_id = _finalized_with(tmp_path, corrections=[])
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] == []


def test_the_term_table_is_top_level_in_order_and_drops_only_bad_rows(tmp_path) -> None:
    client, meeting_id = _finalized_with(tmp_path, corrections=[
        {"heard": "캐스티", "corrected": "CASTI", "grade": "auto"},
        {"heard": "등급", "corrected": "틀림", "grade": "maybe"},
        {"heard": "차티", "corrected": "팀원 B", "grade": "presumed"},
    ])
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["term_corrections"] == [
        {"heard": "캐스티", "corrected": "CASTI", "grade": "auto"},
        {"heard": "차티", "corrected": "팀원 B", "grade": "presumed"},
    ]
    assert list(detail) == ["meeting", "agendas", "term_corrections"]
    # 원문(스크립트)은 바꾸지 않는다 — 들린 말이 그대로 남는다.
    transcript = client.get(f"/api/meetings/{meeting_id}/transcript", headers=MINA)
    assert transcript.status_code == 200


def test_responses_that_return_the_detail_shape_carry_the_same_term_table(tmp_path) -> None:
    """화면은 저장 뒤 받은 응답으로 다시 그린다 — PATCH 같은 상세 모양 응답에도 같은 모양으로 (코디 지시)."""
    client, meeting_id = _finalized_with(tmp_path, corrections=[{"heard": "캐스티", "corrected": "CASTI", "grade": "auto"}])
    expected = [{"heard": "캐스티", "corrected": "CASTI", "grade": "auto"}]
    patched = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"title": "고친 제목"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["term_corrections"] == expected


def test_deleting_the_note_content_clears_the_term_table_back_to_null(tmp_path) -> None:
    """[회의록만 삭제]가 지우는 저장소 자리는 보정 표와 그 시각도 걷는다. (그 API 는 지금 「예정」 회의에서만 열려 보정 표가
    있을 수 없지만, 같은 저장소 함수를 부르는 길이 늘어도 「정정이 돌았다」 가 줄 없는 회의록에 남지 않게 한다.)"""
    client, meeting_id = _finalized_with(tmp_path, corrections=[{"heard": "캐스티", "corrected": "CASTI", "grade": "auto"}])
    application = client.app.state.workflow_application
    with application._session_factory() as session:
        meetings = application._meetings(session)
        record = meetings._repository.meeting(UUID(meeting_id), lock=True)
        meetings._repository.delete_note_content(record)
        session.commit()
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] is None


# ── WP2 수정 1 F-2 — 웜스타트와 세션 없는 배치의 경주 ─────────────────────────────────────────────────────────────


def test_a_batch_arriving_during_a_slow_warm_start_waits_and_uses_that_session(tmp_path) -> None:
    """웜스타트가 도는 동안 온 배치는 자기 세션을 따로 열지 않는다 — 미뤄졌다가 웜스타트의 세션을 이어 쓴다.

    세션 덮어쓰기 0 · provider 이중 호출 0 (검수 F-2). 웜스타트는 회의당 락 안에서 돌고, 기록은 CAS 다.
    """
    import threading

    client, application, agent = _stack(tmp_path)
    released = threading.Event()
    entered = threading.Event()
    original = agent.open_session

    def slow_open(**request):
        entered.set()
        assert released.wait(10), "시험이 웜스타트를 풀지 않았다"
        return original(**request)

    agent.open_session = slow_open
    meeting_id = _running(client)["meeting"]["meeting_id"]
    assert entered.wait(10)
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [_output([_agenda("화제", [_line("줄")])])]

    # 웜스타트가 락을 쥐고 있다 — 배치는 서지 않고 미뤄진다(새 세션을 열지 않는다).
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is False
    assert agent.fresh == [] and agent.runs == []

    released.set()
    application.meeting_batch.drain()
    assert agent.fresh == []  # 이중 provider 호출 없음 — 새 세션 배치가 돌지 않았다
    assert [run["session_ref"] for run in agent.runs] == ["session-1"]  # 미룬 배치가 웜스타트 세션을 이어 쓴다
    assert _session_ref(application, meeting_id) == "session-1"
    assert [status for status, _, _ in _runs(application, meeting_id)] == ["succeeded"]


def test_recording_a_session_is_compare_and_set(tmp_path) -> None:
    """기대한 참조일 때만 갈아 끼운다 — 그 사이 다른 쪽이 기록했으면 덮어쓰지 않고 지금 참조를 돌려준다."""
    client, application, agent = _stack(tmp_path)
    agent.session_ref = None
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    with application._session_factory() as session:
        meetings = application._meetings(session)
        assert meetings.record_ai_session(UUID(meeting_id), session_ref="S1", persona_id="mina", expected=None) == "S1"
        session.commit()
    with application._session_factory() as session:
        meetings = application._meetings(session)
        # 늦게 끝난 쪽(웜스타트)이 「없겠지」 하고 기록하려 해도 S1 이 남는다.
        assert meetings.record_ai_session(UUID(meeting_id), session_ref="S2", persona_id="mina", expected=None) == "S1"
        # 이어 쓰던 쪽(timeout 뒤 새 세션)은 자기가 본 S1 을 기대하고 갈아 끼운다.
        assert meetings.record_ai_session(UUID(meeting_id), session_ref="S3", persona_id="mina", expected="S1") == "S3"
        session.commit()
    assert _session_ref(application, meeting_id) == "S3"


# ── WP2 수정 1 W-2 — 보정 표 칸이 없으면 「정정이 돌았다」 를 찍지 않는다 ──────────────────────────────────────────


def test_a_final_answer_without_the_term_table_is_null_not_an_empty_table(tmp_path) -> None:
    client, meeting_id = _finalized_with(tmp_path, corrections=None)
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] is None


def test_a_final_answer_whose_term_table_is_not_a_list_is_null(tmp_path) -> None:
    client, meeting_id = _finalized_with(tmp_path, corrections="표가 아님")
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] is None
