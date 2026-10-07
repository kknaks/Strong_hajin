"""할 일 기한 — 「다음 회의」 = 이 회의에서 이월된 회의만 · 모든 날짜 KST · 회의일보다 이른 기한은 비운다
(SPEC-010 §4.8 · DEC-009 D-19 · WORK-012 Phase WP1-BE · BE 조사 §5.5 경로 (a)(b)(c)(d)).

10-07 운영 사례: 오전 회의의 다음 할 일 기한이 회의 전날(10-06)로 섰다 — 같은 owner 의 **같은 날 오후 다른 예약**이
「다음 회의」 로 잡혔다(경로 (b)). 경로 (d)(말의 날짜가 회의일보다 이름)는 `test_meeting_finalize.py` 가 본다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from test_meeting_finalize import MINA, _agenda, _blocks, _line, _output, _stack, _summarizing, _the_final, _todo

KST = ZoneInfo("Asia/Seoul")


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _book(client: TestClient, *, starts: datetime, carried_from: str | None = None, title="다음 회의") -> str:
    body = {
        "title": title,
        "starts_at": _iso(starts),
        "ends_at": _iso(starts + timedelta(hours=1)),
        "attendee_ids": ["jiho"],
    }
    if carried_from is not None:
        body["carried_from_meeting_id"] = carried_from
    response = client.post("/api/meetings", headers=MINA, json=body)
    assert response.status_code == 201, response.text
    return response.json()["meeting"]["meeting_id"]


def _meeting_day(client: TestClient, meeting_id: str) -> tuple[date, datetime]:
    starts = datetime.fromisoformat(client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["starts_at"])
    return starts.astimezone(KST).date(), starts


def _finalize_without_spoken_date(client: TestClient, application, agent, meeting_id: str, memo: str) -> dict:
    _blocks(application, meeting_id)
    agent.script = [
        _output([
            _agenda("첫 안건", merged_from=[memo],
                    lines=[_line("합성이 낸 줄", evidence=[{"from_ms": 0, "to_ms": 900}])],
                    todos=[_todo("말에 날짜가 없는 일")]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True
    [todo] = _the_final(client, meeting_id)["todos"]
    return todo


def _open(tmp_path):
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    return client, application, agent, made["meeting"]["meeting_id"], made["agendas"][0]["agenda_id"]


def test_a_carried_meeting_a_week_later_gives_the_day_before_it(tmp_path) -> None:
    client, application, agent, meeting_id, memo = _open(tmp_path)
    day, _ = _meeting_day(client, meeting_id)
    _book(client, starts=datetime.combine(day + timedelta(days=7), time(10, 0), tzinfo=KST), carried_from=meeting_id)
    todo = _finalize_without_spoken_date(client, application, agent, meeting_id, memo)
    assert todo["due_candidate"] == (day + timedelta(days=6)).isoformat()


def test_another_booking_of_the_same_owner_is_not_the_next_meeting(tmp_path) -> None:
    """경로 (b) — 같은 owner 의 다음 예약(이월 아님)은 「다음 회의」 가 아니다 → ② 없음 → `null`."""
    client, application, agent, meeting_id, memo = _open(tmp_path)
    _, starts = _meeting_day(client, meeting_id)
    _book(client, starts=starts + timedelta(minutes=61), title="같은 날 오후 다른 회의")
    todo = _finalize_without_spoken_date(client, application, agent, meeting_id, memo)
    assert todo["due_candidate"] is None


def test_a_carried_meeting_that_starts_before_this_one_is_not_the_next_meeting(tmp_path) -> None:
    """경로 (a) — 이월 회의가 원본보다 앞이면 「뒤에 시작하는 것」 이 아니므로 제외."""
    client, application, agent, meeting_id, memo = _open(tmp_path)
    _, starts = _meeting_day(client, meeting_id)
    _book(client, starts=starts - timedelta(days=2), carried_from=meeting_id, title="앞 날짜의 이월 회의")
    todo = _finalize_without_spoken_date(client, application, agent, meeting_id, memo)
    assert todo["due_candidate"] is None


def test_a_cancelled_carried_meeting_is_not_the_next_meeting(tmp_path) -> None:
    client, application, agent, meeting_id, memo = _open(tmp_path)
    day, _ = _meeting_day(client, meeting_id)
    cancelled = _book(client, starts=datetime.combine(day + timedelta(days=3), time(10, 0), tzinfo=KST), carried_from=meeting_id)
    assert client.delete(f"/api/meetings/{cancelled}", headers=MINA, params={"scope": "meeting"}).status_code in (200, 204)
    later = datetime.combine(day + timedelta(days=10), time(10, 0), tzinfo=KST)
    _book(client, starts=later, carried_from=meeting_id, title="살아 있는 이월 회의")
    todo = _finalize_without_spoken_date(client, application, agent, meeting_id, memo)
    assert todo["due_candidate"] == (day + timedelta(days=9)).isoformat()


def test_the_next_meeting_day_is_counted_in_kst(tmp_path) -> None:
    """경로 (c) — 다음 날 KST 08:00 회의는 UTC 로는 전날 23:00 이다. KST 로 세면 기한 = 회의일(같은 날은 허용)."""
    client, application, agent, meeting_id, memo = _open(tmp_path)
    day, _ = _meeting_day(client, meeting_id)
    _book(client, starts=datetime.combine(day + timedelta(days=1), time(8, 0), tzinfo=KST), carried_from=meeting_id)
    todo = _finalize_without_spoken_date(client, application, agent, meeting_id, memo)
    assert todo["due_candidate"] == day.isoformat()
