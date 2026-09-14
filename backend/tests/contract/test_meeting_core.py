"""회의 계약: 상태 여섯 · 안건과 줄 · 열람 경계 · 목록 두 구획 · 생성과 편집과 삭제 두 갈래.

SCAX-SPEC-004 §3·§4·§5.1·§10과 SCAX-WP-001 Phase 1~3의 검증 항목을 그대로 옮긴 것이다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.domain import (
    MeetingError,
    MeetingStateConflict,
    MeetingStatus,
    ensure_agenda_capacity,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def _schedule(client: TestClient, *, title="주간 제품 회의", days=3.0, minutes=60, attendees=("jiho",), agendas=("권한 모델",), headers=MINA):
    starts = datetime.now(UTC) + timedelta(days=days)
    response = client.post(
        "/api/meetings",
        headers=headers,
        json={
            "title": title,
            "purpose": "이번 주에 정할 것",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
            "location": "3층 회의실",
            "attendee_ids": list(attendees),
            "external_attendees": ["김외부"],
            "agendas": [{"title": name} for name in agendas],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------- Phase 1 · 도메인과 스키마


def test_the_twenty_first_agenda_is_refused(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _schedule(client, agendas=[f"안건 {index}" for index in range(1, 21)])
    meeting_id = meeting["meeting"]["meeting_id"]
    assert len(meeting["agendas"]) == 20

    refused = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "스물한 번째"})
    assert refused.status_code == 409, refused.text
    assert len(client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]) == 20

    with pytest.raises(MeetingStateConflict):
        ensure_agenda_capacity(20)


def test_the_twenty_agenda_limit_is_counted_per_track_not_across_the_three(tmp_path) -> None:
    """**안건 한도는 벌마다 20이다 — 합쳐서 20이 아니다** (SPEC-004 §4.0-3 · `X-100` 확장).

    세 벌을 합산으로 세면 AI 벌이 회차마다 안건을 세우는 것만으로 사람이 자기 벌에 안건을 못 세우게
    된다. 벌마다 자기 목록이므로 한도도 벌마다다.
    """
    client = _client(tmp_path)
    meeting = _schedule(client, agendas=tuple(f"안건 {index}" for index in range(1, 21)))
    meeting_id = meeting["meeting"]["meeting_id"]
    assert len(meeting["agendas"]) == 20

    # 사람 벌이 꽉 찼다 — 스물한 번째는 거절된다.
    assert client.post(
        f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "스물한 번째"}
    ).status_code == 409

    # **AI 벌은 그 셈에 걸리지 않는다** — 사람 벌이 20이어도 배치는 자기 벌을 채운다.
    application = client.app.state.workflow_application
    with application._session_factory() as session:
        meetings = application._meetings(session)
        record = meetings._repository.meeting(_uuid(meeting_id))
        for order in range(1, 21):
            meetings._repository.create_agenda(
                record, track="ai", title=f"AI 안건 {order}", source=None, order_index=order
            )
        session.commit()
    agendas = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert len([row for row in agendas if row["track"] == "memo"]) == 20
    assert len([row for row in agendas if row["track"] == "ai"]) == 20

    # 사람 벌 한도는 여전히 사람 벌만 본다 — 합산 40이 되었다고 달라지지 않는다.
    assert client.post(
        f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "그래도 스물한 번째"}
    ).status_code == 409

    # 하나를 지우면 그 벌에 한 자리가 난다.
    memo_ids = [row["agenda_id"] for row in agendas if row["track"] == "memo"]
    assert client.delete(f"/api/meetings/{meeting_id}/agendas/{memo_ids[-1]}", headers=MINA).status_code == 204
    assert client.post(
        f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "빈 자리에 선다"}
    ).status_code == 201


def test_deleting_an_agenda_takes_its_lines_with_it(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _schedule(client, agendas=("남을 안건", "지울 안건"))
    meeting_id = meeting["meeting"]["meeting_id"]
    staying, going = (agenda["agenda_id"] for agenda in meeting["agendas"])
    application = client.app.state.workflow_application
    principal = client.app.state.workflow_application.authenticated_principal("mina")
    application.append_meeting_line(principal, _uuid(meeting_id), _uuid(staying), track="memo", text="남는 줄")
    application.append_meeting_line(principal, _uuid(meeting_id), _uuid(going), track="memo", text="사라질 줄")

    removed = client.delete(f"/api/meetings/{meeting_id}/agendas/{going}", headers=MINA)
    assert removed.status_code == 204, removed.text

    agendas = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [agenda["title"] for agenda in agendas] == ["남을 안건"]
    assert [line["text"] for line in agendas[0]["lines"]] == ["남는 줄"]


def test_an_agenda_carries_order_source_conclusion_and_its_lines(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _schedule(client, agendas=("첫 안건",))
    meeting_id = meeting["meeting"]["meeting_id"]
    added = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "둘째 안건"})
    assert added.status_code == 201, added.text
    assert added.json()["order"] == 2
    assert added.json()["source"] == "manual"
    assert added.json()["concluded"] is False
    assert added.json()["lines"] == [] and added.json()["todos"] == []

    # **결론 표시가 서는 것은 최종 벌뿐이다** (§4.0-5) — 사람 벌의 안건은 결론 여부를 갖지 않는다:
    # 회의가 도는 동안에는 결론이 화면에 서지 않기 때문이다.
    concluded = client.patch(
        f"/api/meetings/{meeting_id}/agendas/{added.json()['agenda_id']}", headers=MINA, json={"concluded": True}
    )
    assert concluded.status_code == 409, concluded.text
    # 원장에도 남지 않았다 — 사람 벌의 안건은 결론 없이 그대로다.
    after = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert all(row["concluded"] is False for row in after)

    too_long = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "가" * 101})
    assert too_long.status_code == 422


def test_a_line_hangs_from_an_agenda_with_a_track_and_an_empty_evidence_slot(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _schedule(client)
    meeting_id = meeting["meeting"]["meeting_id"]
    agenda_id = meeting["agendas"][0]["agenda_id"]
    application = client.app.state.workflow_application
    principal = client.app.state.workflow_application.authenticated_principal("mina")
    application.append_meeting_line(principal, _uuid(meeting_id), _uuid(agenda_id), track="memo", text="메모 한 줄")

    [agenda] = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert agenda["lines"] == [
        {
            "line_id": agenda["lines"][0]["line_id"],
            "track": "memo",
            "order": 1,
            "text": "메모 한 줄",
            "author": "mina",
            # 「예정」 회의라 회의 시작 시각이 없다 — 낮은 표면은 시각을 매기지 않는다.
            "at_ms": None,
            "evidence": [],
            # 계보는 최종 벌 줄만 들지만 **키는 언제나 낸다** (§4.2-10).
            "from_lines": [],
        }
    ]


# --------------------------------------------------------------------- Phase 2 · 열람 판정과 목록·상세


def test_someone_without_a_relationship_is_not_told_the_meeting_exists(tmp_path) -> None:
    client = _client(tmp_path)
    meeting_id = _schedule(client)["meeting"]["meeting_id"]

    assert client.get(f"/api/meetings/{meeting_id}", headers=SORA).status_code == 404
    board = client.get("/api/meetings", headers=SORA).json()
    assert board["upcoming"] == [] and board["past"]["items"] == []


def test_a_shared_meeting_reaches_the_past_column_as_a_viewer_and_never_becomes_editable(tmp_path) -> None:
    client = _client(tmp_path)
    meeting_id = _schedule(client)["meeting"]["meeting_id"]

    shared = client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora"]})
    assert shared.status_code == 200, shared.text

    detail = client.get(f"/api/meetings/{meeting_id}", headers=SORA)
    assert detail.status_code == 200
    assert detail.json()["meeting"]["viewer_relation"] == "shared"
    assert detail.json()["meeting"]["can_edit_info"] is False
    assert detail.json()["meeting"]["can_edit_note"] is False

    board = client.get("/api/meetings", headers=SORA).json()
    assert board["upcoming"] == []
    assert [row["viewer_relation"] for row in board["past"]["items"]] == ["shared"]

    revoked = client.delete(f"/api/meetings/{meeting_id}/shares/sora", headers=MINA)
    assert revoked.status_code == 200, revoked.text
    assert client.get(f"/api/meetings/{meeting_id}", headers=SORA).status_code == 404


def test_the_board_splits_upcoming_from_past_and_pages_the_past_twenty_at_a_time(tmp_path) -> None:
    client = _client(tmp_path)
    for index in range(22):
        _schedule(client, title=f"지난 회의 {index:02d}", days=-(index + 2), attendees=())
    _schedule(client, title="다가오는 회의", days=5)

    first = client.get("/api/meetings", headers=MINA).json()
    assert [row["title"] for row in first["upcoming"]] == ["다가오는 회의"]
    assert len(first["past"]["items"]) == 20
    assert first["past"]["next_cursor"]
    # 지난 날짜로 세운 회의는 자동 취소에 걸리지 않는다 — 「예정」으로 남아 「지난」 구획에 선다 (SPEC §3.1-8 · `X-149`).
    assert {row["status"] for row in first["past"]["items"]} == {"scheduled"}

    second = client.get("/api/meetings", headers=MINA, params={"cursor": first["past"]["next_cursor"]}).json()
    assert len(second["past"]["items"]) == 2
    assert second["past"]["next_cursor"] is None
    seen = [row["meeting_id"] for row in first["past"]["items"] + second["past"]["items"]]
    assert len(set(seen)) == 22


def test_the_detail_answers_the_whole_head_and_every_agenda(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _schedule(client)
    head = meeting["meeting"]
    assert head["title"] == "주간 제품 회의"
    assert head["purpose"] == "이번 주에 정할 것"
    assert head["location"] == "3층 회의실"
    assert head["status"] == "scheduled"
    assert head["created_by"] == "mina"
    assert head["external_attendees"] == ["김외부"]
    assert [person["member_id"] for person in head["attendees"]] == ["jiho", "mina"]
    assert head["viewer_relation"] == "attendee"
    assert head["can_edit_info"] is True
    assert head["last_saved_at"] is None
    assert head["carried_from_meeting_id"] is None
    # 저장 위치·provider 참조 같은 내부 값은 응답에 없다 (WP Pre-deploy Check).
    assert not {"storage_key", "provider_call_ref", "provider_reference", "version"} & set(head)


# --------------------------------------------------------------------- Phase 3 · 생성·편집·취소


def test_quick_start_stands_with_nothing_but_the_person_who_opened_it(tmp_path) -> None:
    client = _client(tmp_path)
    started = client.post("/api/meetings/quick-start", headers=MINA, json={})
    assert started.status_code == 201, started.text
    head = started.json()["meeting"]
    assert head["status"] == "in_progress"
    assert head["title"] is None
    assert head["location"] is None
    assert [person["member_id"] for person in head["attendees"]] == ["mina"]
    # 값을 묻지 않아도 **메모가 붙을 자리 하나**는 서고 선다 — 안건이 0개면 메모를 던질 곳이 없다 (D32).
    agendas = started.json()["agendas"]
    assert len(agendas) == 1
    # **제목은 빈 값이다** (§4.1-7 · W-7) — AI 가 사람 벌에 손대지 않으므로 채워 줄 사람이 없고,
    # 자리표시 문자열을 넣으면 화면 라벨과 겹쳐 「안건 1. 안건 1」이 된다 (D48 폐기).
    assert agendas[0]["title"] == "" and agendas[0]["title_placeholder"] is True
    # 사람 벌의 안건이고 출처를 갖는다 (§4.0 표 · §4.1-2). 순서는 벌 안에서 1 부터다.
    assert agendas[0]["track"] == "memo"
    assert agendas[0]["source"] == "manual" and agendas[0]["order"] == 1
    assert agendas[0]["lines"] == [] and agendas[0]["todos"] == []

    assert client.get(f"/api/meetings/{head['meeting_id']}", headers=JIHO).status_code == 404


def test_an_attendee_who_did_not_call_the_meeting_may_still_edit_its_information(tmp_path) -> None:
    client = _client(tmp_path)
    meeting_id = _schedule(client)["meeting"]["meeting_id"]

    edited = client.patch(
        f"/api/meetings/{meeting_id}",
        headers=JIHO,
        json={"title": "지호가 고친 제목", "location": "5층 회의실", "attendee_ids": ["jiho", "sora"]},
    )
    assert edited.status_code == 200, edited.text
    head = edited.json()["meeting"]
    assert head["title"] == "지호가 고친 제목"
    assert head["location"] == "5층 회의실"
    assert [person["member_id"] for person in head["attendees"]] == ["jiho", "mina", "sora"]

    # 참석이 아닌 사람은 고치기는커녕 회의를 찾지도 못한다.
    outsider = client.patch(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "yuna"}, json={"title": "몰래"})
    assert outsider.status_code == 404


def test_information_editing_is_refused_while_the_meeting_runs_and_opens_again_when_it_is_done(tmp_path) -> None:
    client = _client(tmp_path)
    meeting_id = _schedule(client)["meeting"]["meeting_id"]

    assert client.post(f"/api/meetings/{meeting_id}/start", headers=MINA).json()["meeting"]["status"] == "in_progress"
    running = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"title": "진행 중 수정"})
    assert running.status_code == 409

    assert client.post(f"/api/meetings/{meeting_id}/end", headers=MINA).json()["meeting"]["status"] == "summarizing"
    summarizing = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"title": "정리 중 수정"})
    assert summarizing.status_code == 409

    # 「완료」로 옮기는 것은 합성(SCAX-WP-004)의 일이므로 여기서는 도메인 전이로 세운다.
    _force_status(client, meeting_id, MeetingStatus.DONE)
    done = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"title": "완료 후 수정"})
    assert done.status_code == 200, done.text
    assert done.json()["meeting"]["title"] == "완료 후 수정"


def test_cancelling_the_meeting_and_deleting_only_the_note_delete_different_things(tmp_path) -> None:
    client = _client(tmp_path)
    kept = _schedule(client, title="회의록만 지울 회의")
    kept_id = kept["meeting"]["meeting_id"]
    application = client.app.state.workflow_application
    principal = client.app.state.workflow_application.authenticated_principal("mina")
    application.append_meeting_line(
        principal, _uuid(kept_id), _uuid(kept["agendas"][0]["agenda_id"]), track="memo", text="지워질 줄"
    )

    note_deleted = client.delete(f"/api/meetings/{kept_id}", headers=MINA, params={"scope": "note"})
    assert note_deleted.status_code == 204, note_deleted.text
    detail = client.get(f"/api/meetings/{kept_id}", headers=MINA).json()
    # 회의 예약은 그대로 남고 안건도 남는다 — 사라진 것은 회의록의 줄이다.
    assert detail["meeting"]["status"] == "scheduled"
    assert detail["meeting"]["last_saved_at"] is None
    assert [agenda["title"] for agenda in detail["agendas"]] == ["권한 모델"]
    assert detail["agendas"][0]["lines"] == []

    cancelled_id = _schedule(client, title="취소할 회의")["meeting"]["meeting_id"]
    cancelled = client.delete(f"/api/meetings/{cancelled_id}", headers=MINA, params={"scope": "meeting"})
    assert cancelled.status_code == 204, cancelled.text
    after = client.get(f"/api/meetings/{cancelled_id}", headers=MINA).json()
    assert after["meeting"]["status"] == "cancelled"
    assert after["agendas"] == []


def test_deleting_is_refused_once_the_meeting_has_started(tmp_path) -> None:
    client = _client(tmp_path)
    meeting_id = _schedule(client)["meeting"]["meeting_id"]
    client.post(f"/api/meetings/{meeting_id}/start", headers=MINA)
    assert client.delete(f"/api/meetings/{meeting_id}", headers=MINA, params={"scope": "meeting"}).status_code == 409
    assert client.delete(f"/api/meetings/{meeting_id}", headers=MINA, params={"scope": "note"}).status_code == 409


def test_an_empty_meeting_whose_time_passed_cancels_itself_and_a_single_line_releases_it(tmp_path) -> None:
    """미리 잡아 두고 아무도 오지 않은 회의만 스스로 취소된다."""
    client = _client(tmp_path)
    meeting = _schedule(client, title="아무도 오지 않은 회의", days=1)
    meeting_id = meeting["meeting"]["meeting_id"]
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "scheduled"

    _wind_past(client, meeting_id)
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "cancelled"

    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    application.append_meeting_line(
        principal, _uuid(meeting_id), _uuid(meeting["agendas"][0]["agenda_id"]), track="memo", text="늦게 적은 줄"
    )
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "scheduled"


def test_a_meeting_may_continue_the_one_before_it(tmp_path) -> None:
    client = _client(tmp_path)
    first_id = _schedule(client, title="1회차")["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=7)
    second = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "2회차",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": ["jiho"],
            "agendas": [{"title": "결론 안 난 안건"}],
            "carried_from_meeting_id": first_id,
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["meeting"]["carried_from_meeting_id"] == first_id
    assert [agenda["source"] for agenda in second.json()["agendas"]] == ["carried"]


def test_the_meeting_tools_still_answer_for_the_persona_they_are_bound_to(tmp_path) -> None:
    """도구 목록에 있다는 것과 실제로 답한다는 것은 다르다."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client = _client(tmp_path)
    database_url = client.app.state.workflow_application._settings.database_url
    settings = Settings(RuntimeProfile.TEST, database_url)
    mine = _schedule(client, title="도구가 답해야 할 회의", attendees=())["meeting"]

    facade = McpReportsFacade(settings, "mina")
    listed = facade.my_meetings()
    assert any(row.get("meeting_id") == mine["meeting_id"] for row in listed if row.get("kind") == "meeting")
    assert facade.get_meeting(mine["meeting_id"])["meeting"]["title"] == "도구가 답해야 할 회의"

    other = McpReportsFacade(settings, "jiho").list_meetings()
    assert all(row.get("title") != "도구가 답해야 할 회의" for row in other)


def _uuid(value: str):
    from uuid import UUID

    return UUID(str(value))


def _wind_past(client: TestClient, meeting_id: str) -> None:
    """어제 미리 잡아 둔 회의가 오늘 아침을 맞은 자리로 옮긴다 — 세운 시각이 종료 시각보다 앞이다."""
    from ax_workspace.platform.persistence import MeetingRecord

    application = client.app.state.workflow_application
    now = datetime.now(UTC)
    with application._session_factory() as session:
        meeting = session.get(MeetingRecord, _uuid(meeting_id))
        meeting.created_at = now - timedelta(days=2)
        meeting.starts_at = now - timedelta(hours=2)
        meeting.ends_at = now - timedelta(hours=1)
        session.commit()


def _force_status(client: TestClient, meeting_id: str, status: MeetingStatus) -> None:
    """합성(SCAX-WP-004)이 아직 없으므로 「완료」·「실패」는 저장소에서 직접 세운다."""
    from ax_workspace.platform.persistence import MeetingRecord

    application = client.app.state.workflow_application
    with application._session_factory() as session:
        meeting = session.get(MeetingRecord, _uuid(meeting_id))
        meeting.status = status.value
        session.commit()


def test_the_note_is_a_line_list_that_saving_overwrites_rather_than_stacking(tmp_path) -> None:
    """[수정] 하나로 열리고 [저장] 하나로 닫힌다. 판을 쌓지 않고 마지막 저장분이 그 회의록이다."""
    client = _client(tmp_path)
    meeting = _schedule(client)
    meeting_id = meeting["meeting"]["meeting_id"]
    agenda_id = meeting["agendas"][0]["agenda_id"]

    # 「예정」에서는 회의록 줄 편집이 열리지 않는다 (SPEC §5.1). **사람 벌의 안건에는 아예 열리지
    # 않는다** — 고치는 자리는 최종 벌 하나다 (§8-9 · D53).
    too_early = client.patch(
        f"/api/meetings/{meeting_id}/agendas/{agenda_id}", headers=MINA, json={"lines": [{"text": "이르다"}]}
    )
    assert too_early.status_code == 409

    _force_status(client, meeting_id, MeetingStatus.DONE)
    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["can_edit_note"] is True
    # 「완료」에서 사람이 세우는 안건은 **최종 벌**에 선다 (§4.1-6 「안건 추가」) — 합성이 실패했거나
    # 아직 돌지 않은 회의록을 사람이 직접 채우는 자리다 (§4.2-6 실패 행).
    assert head["can_add_agenda"] == {"memo": False, "ai": False, "final": True}
    made_final = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "최종 안건"})
    assert made_final.status_code == 201, made_final.text
    final_id = made_final.json()["agenda_id"]
    # 사람이 [수정] 에서 세운 최종 안건의 계보는 비어 있다 (§8-9).
    assert made_final.json()["track"] == "final" and made_final.json()["merged_from"] == []

    saved = client.patch(
        f"/api/meetings/{meeting_id}/agendas/{final_id}",
        headers=MINA,
        json={"lines": [{"text": "권한 모델은 참석으로 가른다."}, {"text": "   "}, {"text": "공유는 열람만 연다."}]},
    )
    assert saved.status_code == 200, saved.text
    # 빈 줄은 저장할 때 버린다. 순서는 배열 순서이고 쓴 사람이 저자다.
    assert [(line["order"], line["text"], line["track"], line["author"]) for line in saved.json()["lines"]] == [
        (1, "권한 모델은 참석으로 가른다.", "final", "mina"),
        (2, "공유는 열람만 연다.", "final", "mina"),
    ]
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["last_saved_at"]

    # 판을 쌓지 않는다 — 목록에서 빠진 id 는 지워진 줄이다 (§8-9).
    keep = saved.json()["lines"][0]["line_id"]
    rewritten = client.patch(
        f"/api/meetings/{meeting_id}/agendas/{final_id}",
        headers=MINA,
        json={"lines": [{"line_id": keep, "text": "한 줄만 남긴다."}]},
    )
    assert [line["text"] for line in rewritten.json()["lines"]] == ["한 줄만 남긴다."]
    finals = [row for row in client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
              if row["track"] == "final"]
    assert [line["text"] for agenda in finals for line in agenda["lines"]] == ["한 줄만 남긴다."]
    # 사람 벌의 안건은 이 편집에 걸리지 않는다 (D53).
    assert client.patch(
        f"/api/meetings/{meeting_id}/agendas/{agenda_id}", headers=MINA, json={"lines": [{"text": "원본을 고쳐 본다"}]}
    ).status_code == 409


def test_the_memo_track_is_edited_whenever_it_is_the_working_copy_and_locked_once_it_is_evidence(tmp_path) -> None:
    """사람 벌은 **임시 재료인 동안 열리고, 근거가 된 뒤 닫힌다** (사용자 결정 2026-09-14 · D53).

    열리는 상태는 예정 · **진행 중** · 취소다. 회의가 도는 동안 사람 벌은 최종 회의록을 지을 재료일
    뿐이므로 사람이 자기가 적은 것을 고치고 지운다 — 오타로 세운 안건이 회의가 끝날 때까지 박제되던
    자리가 여기였다(조사 근거 6). 「종료」·「실패」에서 닫히는 이유는 그때 최종 벌이 섰고 원본이
    **그것을 대조하는 근거**가 되기 때문이다 (§8-11).

    SPEC §4.1-6 게이트 표 · 시안 `SCR-106-E77`·`E35`·`E71`. 회의록 **줄** 편집과는 열리는 상태가 다르다.
    """
    client = _client(tmp_path)
    meeting = _schedule(client)
    meeting_id = meeting["meeting"]["meeting_id"]
    agenda_id = meeting["agendas"][0]["agenda_id"]

    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    # 「예정」은 **사람 벌의** 안건을 열고 줄은 닫는다 — 두 칸이 갈라져 있어야 화면이 [수정]을 세울 수 있다.
    # 판정은 **벌별 셋**이다 (§3.3 · §4.1-6): 불리언 하나로는 벌마다 다른 게이트를 낼 수 없다.
    assert head["can_edit_agendas"] == {"memo": True, "ai": False, "final": False}
    assert head["can_edit_note"] is False
    assert client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "예정에서 더한 안건"}).status_code == 201

    client.post(f"/api/meetings/{meeting_id}/start", headers=MINA)
    running = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    # **「진행 중」에도 사람 벌은 더하고 고치고 지운다** — 임시 재료이므로 임시로 다룬다.
    assert running["can_edit_agendas"] == {"memo": True, "ai": False, "final": False}
    assert running["can_add_agenda"] == {"memo": True, "ai": False, "final": False}
    # 회의록 **줄** 편집은 여전히 닫혀 있다 — 최종 벌이 아직 서지 않았다.
    assert running["can_edit_note"] is False
    added_running = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "진행 중 안건"})
    assert added_running.status_code == 201
    running_id = added_running.json()["agenda_id"]
    # 제목을 고친다 — 오타로 세운 안건이 박제되지 않는다.
    assert client.patch(
        f"/api/meetings/{meeting_id}/agendas/{running_id}", headers=MINA, json={"title": "고쳐 쓴 안건"}
    ).status_code == 200
    # 지운다 — 그 안건에 매달린 그 벌의 줄이 함께 사라진다 (§4.1-10).
    assert client.delete(f"/api/meetings/{meeting_id}/agendas/{running_id}", headers=MINA).status_code == 204
    assert "진행 중 안건" not in [
        row["title"] for row in client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    ]
    # **결론 표시는 여전히 최종 벌에만 있다** — 임시가 열렸다고 최종의 값이 사람 벌로 오지 않는다.
    assert client.patch(
        f"/api/meetings/{meeting_id}/agendas/{agenda_id}", headers=MINA, json={"concluded": True}
    ).status_code == 409

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    # **정리가 도는 동안에는 어느 벌도 열리지 않는다** — 합성이 그 재료를 읽는 중이다.
    assert client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "정리 중 안건"}).status_code == 409
    assert client.delete(f"/api/meetings/{meeting_id}/agendas/{agenda_id}", headers=MINA).status_code == 409

    _force_status(client, meeting_id, MeetingStatus.DONE)
    done = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    # **「완료」는 최종 벌만 연다** — 원본 두 벌은 읽기 전용이다 (D53 · §8-11). 0.4.x 의 「종료 뒤 안건
    # 편집」은 뼈대가 한 벌이던 때의 표이고, 그 자리는 이제 최종 벌이다.
    assert done["can_edit_agendas"] == {"memo": False, "ai": False, "final": True}
    assert done["can_edit_note"] is True
    # **사람 벌은 「완료」에 닫힌다** — 이제 그것은 최종 벌을 대조하는 근거이고, 고칠 수 있으면
    # 근거가 되지 못한다 (D53). 열렸다 닫히는 이 축이 「임시 재료 → 근거」의 전부다.
    assert client.patch(
        f"/api/meetings/{meeting_id}/agendas/{agenda_id}", headers=MINA, json={"title": "원본을 고쳐 본다"}
    ).status_code == 409
    assert client.delete(f"/api/meetings/{meeting_id}/agendas/{agenda_id}", headers=MINA).status_code == 409
    # 새로 세우는 안건은 **최종 벌**에 선다.
    added_after = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "완료 뒤 안건"})
    assert added_after.status_code == 201
    assert added_after.json()["track"] == "final"


def test_a_todo_carries_the_eight_values_the_promotion_modal_needs(tmp_path) -> None:
    """SCAX-SPEC-004 §8.1. 값을 채우는 것은 합성(SCAX-WP-004)이고 이 WP는 자리와 모양만 낸다."""
    from ax_workspace.platform.persistence import MeetingTodoRecord

    client = _client(tmp_path)
    meeting = _schedule(client)
    meeting_id = meeting["meeting"]["meeting_id"]
    agenda_id = meeting["agendas"][0]["agenda_id"]
    application = client.app.state.workflow_application
    with application._session_factory() as session:
        session.add(
            MeetingTodoRecord(
                meeting_id=_uuid(meeting_id),
                agenda_id=_uuid(agenda_id),
                order_index=1,
                title="계약서를 검토한다",
                description="무엇을 왜 해야 하는지.\n회의 주간 제품 회의 · 안건 1 에서",
                due_candidate=None,
                checklist_candidate=["초안 읽기", "쟁점 정리"],
                reference={"meeting_id": meeting_id, "agenda_id": agenda_id, "line_ids": []},
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

    [agenda] = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    [todo] = agenda["todos"]
    assert set(todo) == {
        "todo_id", "agenda_id", "title", "description", "due_candidate", "checklist_candidate", "reference", "linked",
        # 회의 중 배치가 낸 후보인가 (D46) — 참이면 읽기 전용이라 승격 단추가 서지 않는다.
        "provisional",
    }
    assert todo["agenda_id"] == agenda_id
    assert todo["title"] == "계약서를 검토한다"
    assert todo["checklist_candidate"] == ["초안 읽기", "쟁점 정리"]
    assert todo["reference"] == {"meeting_id": meeting_id, "agenda_id": agenda_id, "line_ids": []}
    # 승격 전에는 비어 있다. 담당자 칸은 아예 없다 — AI가 고르지 않는다.
    assert todo["linked"] is None
    assert "assignee_candidate" not in todo


def test_an_unknown_agenda_source_is_refused_as_an_unprocessable_request(tmp_path) -> None:
    """모르는 출처는 **422** 로 돌아온다 — 화면이 아는 넷 밖의 글자를 원장에 남기지 못한다.

    **출처는 사람 벌 안의 출처다** (§4.1-2) — 0.4.x 의 「AI 정리」(`ai`)는 은퇴했고, 다른 두 벌에는
    출처가 붙지 않는다.
    """
    from ax_workspace.entrypoints.http import _runtime_error
    from ax_workspace.modules.meetings.domain import TRACK_MEMO, ensure_agenda_source

    try:
        ensure_agenda_source("imported", track=TRACK_MEMO)
    except MeetingError as error:
        refusal = _runtime_error(error)
    assert refusal.status_code == 422
    assert "manual" in str(refusal.detail) and "derived" in str(refusal.detail)


def test_the_places_a_meeting_actually_builds_agendas_from_are_still_three(tmp_path) -> None:
    """지금 만들어지는 출처는 셋뿐이다 — 값을 연 것이 경로를 연 것은 아니다 (D38)."""
    client = _client(tmp_path)
    scheduled = _schedule(client)
    assert [row["source"] for row in scheduled["agendas"]] == ["manual"]

    carried = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "이어지는 회의",
            "starts_at": (datetime.now(UTC) + timedelta(days=4)).isoformat().replace("+00:00", "Z"),
            "ends_at": (datetime.now(UTC) + timedelta(days=4, hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": ["jiho"],
            "agendas": [{"title": "지난 회의에서"}],
            "carried_from_meeting_id": scheduled["meeting"]["meeting_id"],
        },
    )
    assert carried.status_code == 201, carried.text
    assert [row["source"] for row in carried.json()["agendas"]] == ["carried"]


def test_the_host_may_stand_up_and_fix_and_drop_an_agenda_while_the_meeting_is_running(tmp_path) -> None:
    """말이 새 주제로 넘어가는 순간이 곧 안건이 필요한 순간이다 (사용자 결정 D45, 2026-09-11).

    회의가 끝나기를 기다리게 하면 그동안의 메모가 엉뚱한 안건에 붙는다. **세우는 것만이 아니라 고치고
    지우는 것도 열린다** (사용자 결정 「최종 회의록만 회의록이다」 2026-09-14) — 사람 벌은 최종
    회의록을 지을 **임시 재료**이고, 임시를 최종처럼 잠가 두면 오타로 세운 안건이 박제된다.

    안건을 지우면 **그 벌의 줄이 함께 사라진다** (§4.1-10) — 사람이 자기가 적은 것을 지우는 것이고,
    최종 회의록은 재전사문 위에서 새로 지어지므로 그 삭제가 회의록을 깨지 않는다.
    """
    client = _client(tmp_path)
    made = _schedule(client)
    meeting_id = made["meeting"]["meeting_id"]
    standing = made["agendas"][0]["agenda_id"]
    assert client.post(f"/api/meetings/{meeting_id}/start", headers=MINA).status_code == 200

    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["status"] == "in_progress"
    # 「+ 새 안건」과 편집이 **사람 벌에서 같이 열린다** — 임시 재료라 세우는 것도 지우는 것도 그 자리다.
    assert head["can_add_agenda"] == {"memo": True, "ai": False, "final": False}
    assert head["can_edit_agendas"] == {"memo": True, "ai": False, "final": False}

    added = client.post(f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "회의 중 안건"})
    assert added.status_code == 201, added.text
    assert added.json()["title"] == "회의 중 안건" and added.json()["source"] == "manual"
    assert [row["title"] for row in client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]] == [
        "권한 모델",
        "회의 중 안건",
    ]

    # **고치기·지우기도 함께 열린다** (사용자 결정 2026-09-14) — 사람 벌은 임시 재료이므로 회의가
    # 도는 동안 사람이 자기가 적은 것을 손본다. 0.4.x 는 여기를 막아, 이야기가 옮겨 가며 세운 안건이
    # 잘못 서면 회의가 끝날 때까지 그대로 남았다.
    assert client.patch(
        f"/api/meetings/{meeting_id}/agendas/{standing}", headers=MINA, json={"title": "고쳐 쓴 안건"}
    ).status_code == 200

    # 지우는 것도 열린다 — 매달린 메모가 함께 사라진다.
    memo = client.post(
        f"/api/meetings/{meeting_id}/agendas/{standing}/lines", headers=MINA, json={"text": "여기 적은 메모"}
    )
    assert memo.status_code == 201, memo.text
    assert client.delete(f"/api/meetings/{meeting_id}/agendas/{standing}", headers=MINA).status_code == 204
    left = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [row["title"] for row in left] == ["회의 중 안건"]
    assert all(not row["lines"] for row in left)

    # **AI 벌은 여전히 어느 쪽도 열리지 않는다** — 그것은 AI 의 기록이고 배치가 매 회차 전량 교체한다.
    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    with application._session_factory() as session:
        meetings = application._meetings(session)
        ai_agenda = meetings._repository.create_agenda(
            meetings._repository.meeting(_uuid(meeting_id)),
            track="ai", title="AI 가 세운 안건", source=None, order_index=1,
        )
        ai_id = str(ai_agenda.id)
        session.commit()
    assert client.patch(
        f"/api/meetings/{meeting_id}/agendas/{ai_id}", headers=MINA, json={"title": "AI 것을 고쳐 본다"}
    ).status_code == 409
    assert client.delete(f"/api/meetings/{meeting_id}/agendas/{ai_id}", headers=MINA).status_code == 409
    del principal
