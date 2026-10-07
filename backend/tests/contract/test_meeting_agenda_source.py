"""안건 출처는 안건마다 · 수정 참석자 합치기 (SPEC-010 §4.2 · §4.3 · §2.1 · §2.3 · WORK-012 Phase WP1-BE).

0.6.x 는 이어온 회의가 있으면 그 요청의 안건 **전부**를 `carried` 로 적었다 — 손으로 쓴 안건까지 「지난 회의에서 넘어옴」
이 됐다(SH-IMP-005). 이제 생성 전용 안건 입력이 안건마다 `source` 를 받고, 회의 중 안건 추가 입구는 제목만 받는다.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.domain import MeetingAgendaSourceInvalid
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack

MINA = {"X-Demo-Persona": "mina"}


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _create(client: TestClient, *, days: float, agendas: list[dict], carried_from: str | None = None, title="회의"):
    starts = datetime.now(UTC) + timedelta(days=days)
    body = {
        "title": title,
        "starts_at": _iso(starts),
        "ends_at": _iso(starts + timedelta(hours=1)),
        "attendee_ids": ["jiho"],
        "agendas": agendas,
    }
    if carried_from is not None:
        body["carried_from_meeting_id"] = carried_from
    return client.post("/api/meetings", headers=MINA, json=body)


def test_loading_a_previous_meeting_keeps_hand_written_agendas_manual(tmp_path) -> None:
    """[불러오기] — 불러온 미결 안건만 `carried`, 같은 요청의 손으로 쓴 안건은 `manual` 이다 (SPEC-010 §2.3 표)."""
    client = _client(tmp_path)
    first = _create(client, days=2, agendas=[{"title": "1회차 안건"}], title="주간 회의").json()
    second = _create(
        client,
        days=9,
        title="주간 회의",
        carried_from=first["meeting"]["meeting_id"],
        agendas=[{"title": "결론 안 난 안건", "source": "carried"}, {"title": "손으로 쓴 안건"}, {"title": "명시한 새 안건", "source": "manual"}],
    )
    assert second.status_code == 201, second.text
    assert [(row["title"], row["source"]) for row in second.json()["agendas"]] == [
        ("결론 안 난 안건", "carried"),
        ("손으로 쓴 안건", "manual"),
        ("명시한 새 안건", "manual"),
    ]


def test_next_meeting_booking_with_only_new_agendas_is_all_manual(tmp_path) -> None:
    """[다음 회의 예약] 입구 — 이어온 회의가 있어도 새로 쓴 안건은 `manual` 이다(옛 회의 단위 판정이 사라졌다)."""
    client = _client(tmp_path)
    first = _create(client, days=2, agendas=[{"title": "1회차 안건"}]).json()
    second = _create(client, days=9, carried_from=first["meeting"]["meeting_id"], agendas=[{"title": "새 안건"}])
    assert second.status_code == 201, second.text
    assert second.json()["meeting"]["carried_from_meeting_id"] == first["meeting"]["meeting_id"]
    assert [row["source"] for row in second.json()["agendas"]] == ["manual"]


def test_carried_without_a_previous_meeting_is_422_agenda_source_invalid(tmp_path) -> None:
    client = _client(tmp_path)
    refused = _create(client, days=2, agendas=[{"title": "넘어왔다는 안건", "source": "carried"}])
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "AGENDA_SOURCE_INVALID"


@pytest.mark.parametrize("source", ["set", "derived", "ai", ""])
def test_a_creation_agenda_source_is_only_manual_or_carried(tmp_path, source) -> None:
    client = _client(tmp_path)
    refused = _create(client, days=2, agendas=[{"title": "안건", "source": source}])
    assert refused.status_code == 422, refused.text


def test_the_agenda_add_entry_takes_no_source(tmp_path) -> None:
    """회의 중 안건 추가 입구는 옛 모델(제목만) 그대로다 — `carried` 가 들어올 길이 없다 (SPEC-010 §5 · W-r2-5)."""
    client, application = _stack(tmp_path)
    created = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "추가 입구 확인",
            "starts_at": "2026-09-30T15:00:00+09:00",
            "ends_at": "2026-09-30T16:00:00+09:00",
            "agendas": [{"title": "첫 안건"}],
        },
    ).json()
    meeting_id = created["meeting"]["meeting_id"]
    refused = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "더하기", "source": "carried"})
    assert refused.status_code == 422, refused.text

    facade = McpReportsFacade(application._settings, "mina")
    server = _create_bound_persona_server(facade)
    with pytest.raises(Exception):
        asyncio.run(server.call_tool("meeting_agenda_add", {"meeting_id": meeting_id, "request": {"title": "더하기", "source": "carried"}}))

    added = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "더하기"})
    assert added.status_code in (200, 201), added.text
    sources = {row["title"]: row["source"] for row in client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]}
    assert sources["더하기"] == "manual"


def test_mcp_meeting_create_takes_the_same_agenda_shape(tmp_path, monkeypatch) -> None:
    """AX `meeting_create` 도 안건마다 출처를 받는다 — 안 주면 `manual`, 이어온 회의 없는 `carried` 는 제안부터 세우지 않는다."""
    client, application = _stack(tmp_path)
    facade = McpReportsFacade(application._settings, "mina")
    server = _create_bound_persona_server(facade)
    first = asyncio.run(server.call_tool("meeting_create", {"request": {
        "title": "1회차",
        "starts_at": "2026-09-30T15:00:00+09:00",
        "ends_at": "2026-09-30T16:00:00+09:00",
    }}))
    assert not first.is_error, first
    first_id = first.structured_content["meeting"]["meeting_id"]
    second = asyncio.run(server.call_tool("meeting_create", {"request": {
        "title": "2회차",
        "starts_at": "2026-10-07T15:00:00+09:00",
        "ends_at": "2026-10-07T16:00:00+09:00",
        "carried_from_meeting_id": first_id,
        "agendas": [{"title": "넘어온 것", "source": "carried"}, {"title": "새 것"}],
    }}))
    assert not second.is_error, second
    assert [row["source"] for row in second.structured_content["agendas"]] == ["carried", "manual"]

    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    # 제안 저장(`_freeze_meeting_proposal`)이 도메인 규칙으로 거절한다 — 사람이 볼 카드가 서지 않는다.
    with pytest.raises(Exception) as refused:
        asyncio.run(server.call_tool("meeting_create", {"request": {
            "title": "이어온 회의 없는 넘어옴",
            "starts_at": "2026-10-14T15:00:00+09:00",
            "ends_at": "2026-10-14T16:00:00+09:00",
            "agendas": [{"title": "넘어온 것", "source": "carried"}],
        }}))
    assert isinstance(refused.value.__cause__, MeetingAgendaSourceInvalid)


def test_editing_a_meeting_merges_the_same_person_once(tmp_path) -> None:
    """수정도 생성처럼 같은 사람을 하나로 합친다 — 처음 나온 순서를 지킨다 (SPEC-010 §2.1 · §4.3 · OQ-909)."""
    client = _client(tmp_path)
    created = _create(client, days=3, agendas=[]).json()
    meeting_id = created["meeting"]["meeting_id"]
    patched = client.patch(
        f"/api/meetings/{meeting_id}",
        headers=MINA,
        json={"attendee_ids": ["jiho", "mina", "jiho", "", "mina"], "external_attendees": ["김외부", "박외부", "김외부"]},
    )
    assert patched.status_code == 200, patched.text
    meeting = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    attendees = [person["member_id"] for person in meeting["attendees"]]
    assert sorted(attendees) == ["jiho", "mina"] and len(attendees) == len(set(attendees))
    assert meeting["external_attendees"] == ["김외부", "박외부"]
