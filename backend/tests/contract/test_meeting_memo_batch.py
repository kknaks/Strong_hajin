"""메모 트랙과 AI 중간 요약 배치 (SCAX-SPEC-004 §6 · §7 · SCAX-WP-003 Phase 1~3).

사람이 던지는 메모와 AI 가 채우는 트랙은 서로의 자리에 쓰지 않는다. 실제 provider 를 부르지 않는다 —
provider 경계(`BatchAgent`)에서 대역을 끼운다.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.batch import (
    BATCH_CHARS,
    BATCH_SWITCH_MIN_CHARS,
    CAUSE_AGENDA_SWITCH,
    CAUSE_TRANSCRIPT,
    DEFAULT_TOOL_REGISTRY,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


# --------------------------------------------------------------------- 대역


class FakeBatchAgent:
    """세션을 열고 대본대로 답한다. 실패는 예외 하나로 낸다 — 설계한 실패가 그것뿐이다."""

    def __init__(self) -> None:
        self.session_ref: str | None = "session-1"
        self.opened: list[dict] = []
        self.runs: list[dict] = []
        self.script: list[str | Exception] = []

    def open_session(self, *, persona_id: str, prompt: str, tools: tuple[str, ...]) -> str | None:
        self.opened.append({"persona_id": persona_id, "prompt": prompt, "tools": tools})
        return self.session_ref

    def run_batch(self, *, persona_id: str, session_ref: str, prompt: str, tools: tuple[str, ...]) -> str:
        self.runs.append(
            {"persona_id": persona_id, "session_ref": session_ref, "prompt": prompt, "tools": tools}
        )
        if not self.script:
            return _output([])
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _output(agendas: list[dict]) -> str:
    return json.dumps({"agendas": agendas}, ensure_ascii=False)


def _agenda(title: str, lines: list[dict], *, todos: list[dict] | None = None) -> dict:
    """배치 출력의 안건 하나 — **AI 벌의 안건이다.**

    `agenda_id` 도 `source` 도 없다 (SPEC v0.5.1 §7.1 출력 · W-6): AI 벌은 회차마다 전량 교체되고 안건
    id 가 매 회차 새로 나므로 이어 쓸 id 가 없고, 출처는 사람 벌 안의 값이라 여기 붙지 않는다.
    `todos` 는 D46 부터 **required** 라 비어도 키가 있어야 한다.
    """
    return {"title": title, "lines": lines, "todos": todos or []}


def _todo(title: str, *, description: str = "회의에서 나온 일.", due: str | None = None,
          checklist: list[str] | None = None, line_ids: list[str] | None = None) -> dict:
    return {
        "title": title, "description": description, "due_candidate": due,
        "checklist_candidate": checklist or [], "line_ids": line_ids or [],
    }


def _line(text: str, *, evidence: list[dict] | None = None, task_id: str | None = None) -> dict:
    return {"text": text, "evidence": evidence or [], "task_id": task_id}


# --------------------------------------------------------------------- 발판


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url, recordings_dir=str(tmp_path / "audio")))
    application = app.state.workflow_application
    agent = FakeBatchAgent()
    application.meeting_batch._agent = agent
    return TestClient(app), application, agent


def _meeting(client: TestClient, *, attendees=("jiho",), agendas=("첫 안건",)) -> dict:
    starts = datetime.now(UTC) + timedelta(minutes=5)
    return client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "메모할 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": list(attendees),
            "agendas": [{"title": name} for name in agendas],
        },
    ).json()


def _running(client: TestClient, **kwargs) -> dict:
    made = _meeting(client, **kwargs)
    meeting_id = made["meeting"]["meeting_id"]
    assert client.post(f"/api/meetings/{meeting_id}/start", headers=MINA).status_code == 200
    return made


def _blocks(application, meeting_id: str, *, count: int, chars: int, start_ms: int = 0, text: str = "가") -> None:
    """확정 발화를 직접 쌓는다 — 스트림(WP-002)이 하는 일을 시험이 대신한다."""
    from sqlalchemy import func, select

    from ax_workspace.platform.persistence import MeetingTranscriptRecord

    with application._session_factory() as session:
        highest = session.scalar(
            select(func.max(MeetingTranscriptRecord.seq)).where(
                MeetingTranscriptRecord.meeting_id == UUID(meeting_id)
            )
        ) or 0
        for index in range(count):
            session.add(
                MeetingTranscriptRecord(
                    meeting_id=UUID(meeting_id),
                    seq=highest + index + 1,
                    speaker_label="1",
                    at_ms=start_ms + index * 1_000,
                    end_ms=start_ms + index * 1_000 + 900,
                    text=text * chars,
                    created_at=datetime.now(UTC),
                )
            )
        session.commit()


# --------------------------------------------------------------------- Phase 1 · 메모 트랙


def test_a_memo_becomes_a_line_the_server_timestamps(tmp_path) -> None:
    """메모 하나가 줄 하나다. 시각은 **서버가 매긴다** — 클라이언트 시계를 믿지 않는다 (SPEC §6-5)."""
    client, _, _ = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    agenda_id = made["agendas"][0]["agenda_id"]

    written = client.post(
        f"/api/meetings/{meeting_id}/agendas/{agenda_id}/lines", headers=MINA, json={"text": "권한부터 정한다"}
    )
    assert written.status_code == 201, written.text
    line = written.json()
    # `from_lines` 가 늘었다 — 계보는 최종 벌 줄만 들지만 **키는 언제나 낸다** (§4.2-10).
    assert set(line) == {"line_id", "track", "order", "text", "author", "at_ms", "evidence", "from_lines"}
    assert line["from_lines"] == []
    # 사람 벌의 줄은 `memo` 이다 — 0.4.x 의 `memo` 표기를 안건과 같은 이름으로 정렬했다 (§4.0-2 · §11.4).
    assert line["track"] == "memo" and line["author"] == "mina" and line["evidence"] == []
    assert isinstance(line["at_ms"], int) and line["at_ms"] >= 0

    [agenda] = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert agenda["track"] == "memo"
    assert [row["text"] for row in agenda["lines"]] == ["권한부터 정한다"]


def test_writing_the_same_memo_twice_makes_two_lines_and_a_failed_write_makes_none(tmp_path) -> None:
    """자동 저장은 재시도한다 — 실패한 쓰기는 줄을 남기지 않고, 성공한 쓰기만 쌓인다 (SPEC §6-7)."""
    client, _, _ = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    agenda_id = made["agendas"][0]["agenda_id"]
    path = f"/api/meetings/{meeting_id}/agendas/{agenda_id}/lines"

    assert client.post(path, headers=MINA, json={"text": ""}).status_code == 422
    assert client.post(path, headers=MINA, json={"text": "다시 건 저장"}).status_code == 201
    [agenda] = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [row["text"] for row in agenda["lines"]] == ["다시 건 저장"]


def test_the_transcript_carries_speech_and_memo_and_opens_to_attendees_and_viewers(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    agenda_id = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id, count=2, chars=10)
    client.post(f"/api/meetings/{meeting_id}/agendas/{agenda_id}/lines", headers=MINA, json={"text": "사람이 적은 것"})

    mine = client.get(f"/api/meetings/{meeting_id}/transcript", headers=MINA)
    assert mine.status_code == 200
    body = mine.json()
    assert [set(row) for row in body["items"]] == [{"id", "speakerLabel", "atMs", "endMs", "content"}] * 2
    assert [set(row) for row in body["memos"]] == [{"line_id", "agenda_id", "text", "author", "atMs"}]
    assert body["memos"][0]["agenda_id"] == agenda_id and body["memos"][0]["author"] == "mina"

    # 참석자는 읽는다. 참석도 공유도 아닌 사람은 존재를 모른다.
    assert client.get(f"/api/meetings/{meeting_id}/transcript", headers=JIHO).status_code == 200
    assert client.get(f"/api/meetings/{meeting_id}/transcript", headers=SORA).status_code == 404
    # 공유가 유일한 예외다 (SPEC §3.2-2).
    assert client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora"]}).status_code == 200
    assert client.get(f"/api/meetings/{meeting_id}/transcript", headers=SORA).status_code == 200


def test_a_meeting_that_has_not_started_answers_an_empty_transcript_rather_than_a_missing_one(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    answer = client.get(f"/api/meetings/{meeting_id}/transcript", headers=MINA)
    assert answer.status_code == 200
    assert answer.json() == {"items": [], "memos": []}


# --------------------------------------------------------------------- Phase 2 · 세션과 트리거


def test_starting_a_meeting_opens_one_provider_session_in_the_background(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()

    assert len(agent.opened) == 1
    opened = agent.opened[0]
    # 도구는 회의를 만든 사람으로 선다 (SPEC §13 `OQ-315` 잠정값).
    assert opened["persona_id"] == "mina"
    assert opened["tools"] == DEFAULT_TOOL_REGISTRY
    # 첫 turn 이 맥락을 싣는다 — 회의 정보 · 안건. 참석자는 수만 싣고 실명을 흘리지 않는다.
    assert "메모할 회의" in opened["prompt"] and "첫 안건" in opened["prompt"]
    assert "지호" not in opened["prompt"]

    with application._session_factory() as session:
        assert application._meetings(session).ai_session_ref(UUID(meeting_id)) == "session-1"


def test_a_meeting_opened_by_the_quick_start_button_gets_the_same_warm_start(tmp_path) -> None:
    """바로 시작도 「진행 중」이다 — 웜스타트가 걸리지 않으면 전사도 메모도 배치를 한 번도 돌리지 못한다."""
    client, application, agent = _stack(tmp_path)
    started = client.post("/api/meetings/quick-start", headers=MINA, json={})
    assert started.status_code == 201, started.text
    meeting_id = started.json()["meeting"]["meeting_id"]
    application.meeting_batch.drain()

    assert len(agent.opened) == 1
    assert agent.opened[0]["persona_id"] == "mina"
    # 제목 없이 선 회의라도 기본 안건은 첫 turn 의 맥락에 실린다 (D32) — 다만 그 **제목은 빈 값이다**
    # (§4.1-7 · W-7): AI 가 사람 벌에 손대지 않으므로 자리표시 문자열을 넣을 이유가 사라졌다 (D48 폐기).
    assert '"title": ""' in agent.opened[0]["prompt"]
    # 사람 벌의 안건은 **맥락으로만** 실린다 — id 를 싣지 않는다: 이어 쓸 사람 안건이 없다 (W-6).
    assert '"agenda_id"' not in agent.opened[0]["prompt"]
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    [placeholder] = detail["agendas"]
    assert placeholder["track"] == "memo" and placeholder["title"] == ""
    assert placeholder["title_placeholder"] is True and placeholder["source"] == "manual"
    with application._session_factory() as session:
        assert application._meetings(session).ai_session_ref(UUID(meeting_id)) == "session-1"

    # 세션이 섰으니 분량 트리거가 실제로 배치를 낸다.
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert len(agent.runs) == 1


def test_a_session_that_cannot_be_opened_leaves_the_meeting_running_and_the_batch_unsubmitted(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    agent.session_ref = None
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    application.meeting_batch.drain()

    # 회의는 정상이다 — 화면에 아무것도 표시하지 않는다.
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "in_progress"
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    assert agent.runs == []  # 세션이 없으면 제출하지 않는다


def test_a_second_batch_does_not_start_while_one_is_running(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id, count=2, chars=BATCH_CHARS)
    batch = application.meeting_batch

    # 첫 배치가 도는 **중에** 온 트리거를 재현한다 — provider 호출이 그 자리를 붙들고 있는 동안이다.
    fired: list[bool] = []
    original = agent.run_batch

    def reentrant(**kwargs) -> str:
        fired.append(batch.evaluate(meeting_id, CAUSE_TRANSCRIPT))
        return original(**kwargs)

    agent.run_batch = reentrant
    fired.append(batch.evaluate(meeting_id, CAUSE_TRANSCRIPT))
    # 하나만 낸다 — 도는 중에 온 트리거는 아무것도 하지 않는다 (SPEC §7.1 동시성).
    # 도는 중에 온 트리거는 아무것도 하지 않는다 (SPEC §7.1 동시성).
    assert fired == [False, True]
    assert len(agent.runs) == 1


def test_the_second_batch_carries_only_what_came_after_the_last_success(tmp_path) -> None:
    """세션이 앞 구간을 기억한다 — 두 번째 배치가 같은 발화를 다시 싣지 않는다 (SPEC §7.1 이후 배치)."""
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    batch = application.meeting_batch

    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    assert "가" * 20 in agent.runs[0]["prompt"]

    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS, start_ms=5_000, text="나")
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    assert len(agent.runs) == 2
    second = agent.runs[1]["prompt"]
    assert "나" * 20 in second and "가" * 20 not in second
    # 이어 쓴다 — 같은 세션 참조다.
    assert agent.runs[1]["session_ref"] == "session-1"


def test_the_batch_session_opens_only_the_tools_the_registry_names(tmp_path) -> None:
    """도구 목록은 레지스트리다 — 설정으로 늘린다. 최소 넷은 바닥이지 천장이 아니다 (SPEC §7.2-3)."""
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, meeting_ai_tools=("task_list", "graph_search"))
    assert settings.meeting_ai_tool_registry == (*DEFAULT_TOOL_REGISTRY, "graph_search")

    from ax_workspace.bootstrap.application import create_codex_cli_provider

    provider = create_codex_cli_provider(settings, enabled_tools=settings.meeting_ai_tool_registry)
    overrides = provider._mcp_overrides(None)
    [enabled] = [row for row in overrides if row.startswith("mcp_servers.scax.enabled_tools=")]
    for name in settings.meeting_ai_tool_registry:
        assert f'"{name}"' in enabled
    # 쓰기 도구는 열리지 않는다 — 배치가 도구로 무엇을 바꾸지 않는다 (SPEC §7.2-4).
    assert "task_create_self" not in enabled and "task_update" not in enabled


def test_the_registry_tools_all_exist_and_only_read(tmp_path) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade, create_mcp_server

    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AX_MCP_PERSONA", "mina")
    try:
        server = create_mcp_server(settings)
    finally:
        monkeypatch.undo()
    registered = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in registered}
    for name in DEFAULT_TOOL_REGISTRY:
        assert name in by_name, sorted(by_name)
        # 도구는 전부 조회다 — 배치가 도구로 무엇을 바꾸지 않는다 (SPEC §7.2-4).
        assert by_name[name].annotations is not None and by_name[name].annotations.read_only_hint is True

    facade = McpReportsFacade(settings, "mina")
    assert isinstance(facade.list_projects(), list)
    assert {row.get("member_id") or row.get("id") for row in facade.list_members()} >= {"mina", "jiho"}


# --------------------------------------------------------------------- Phase 3 · 검증과 적재


def test_an_output_that_breaks_the_schema_discards_the_whole_batch(tmp_path) -> None:
    """스키마 위반은 한 줄도 들어가지 않고 직전 성공분이 그대로 남는다 (SPEC §7.1 검증)."""
    client, application, agent = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    batch = application.meeting_batch

    agent.script = [_output([_agenda("첫 정리", [_line("살아남을 줄")])]), '{"agendas": [{"title": 1}]}']
    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    before = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [line["text"] for agenda in before for line in agenda["lines"]] == ["살아남을 줄"]

    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS, start_ms=9_000, text="다")
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    after = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [line["text"] for agenda in after for line in agenda["lines"]] == ["살아남을 줄"]


def test_the_ai_track_is_replaced_whole_and_never_touches_a_memo_agenda(tmp_path) -> None:
    """**AI 는 자기 벌에만 쓴다. 예외가 없다** (SPEC v0.5 §4.1-8 · §7.3 · D51 · D14 폐기).

    0.4.x 는 출력의 `agenda_id` 를 보고 사람 안건을 이어 썼다 — 사람 안건 아래에 AI 줄이 매달렸고, 그래서
    「사람이 적은 것이 남았나」를 물을 수가 없었다. 이제 **벌이 갈렸다**: 사람 안건에는 `memo` 줄만,
    AI 안건에는 `ai` 줄만 매달리고, 전량 교체가 **진짜 전량**이 된다(예외 분기가 사라졌다).
    """
    client, application, agent = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    [memo_agenda] = [row["agenda_id"] for row in made["agendas"] if row["track"] == "memo"]
    application.meeting_batch.drain()
    batch = application.meeting_batch

    client.post(
        f"/api/meetings/{meeting_id}/agendas/{memo_agenda}/lines", headers=MINA, json={"text": "사람이 적은 줄"}
    )
    agent.script = [
        _output([
            _agenda("AI 가 가른 첫 화제", [_line("첫 배치 줄")]),
            _agenda("AI 가 세운 안건", [_line("새 주제")]),
        ]),
        _output([_agenda("두 번째 정리", [_line("갈아끼운 줄")])]),
    ]
    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)

    agendas = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    by_id = {agenda["agenda_id"]: agenda for agenda in agendas}
    memo = by_id[memo_agenda]
    # 사람 벌의 안건은 제목도 출처도 그대로이고 **그 아래에는 사람 줄만 있다.**
    assert memo["track"] == "memo" and memo["title"] == "첫 안건" and memo["source"] == "manual"
    assert [(row["track"], row["text"]) for row in memo["lines"]] == [("memo", "사람이 적은 줄")]

    ai_agendas = [agenda for agenda in agendas if agenda["track"] == "ai"]
    assert [agenda["title"] for agenda in ai_agendas] == ["AI 가 가른 첫 화제", "AI 가 세운 안건"]
    # AI 벌의 안건은 출처를 갖지 않는다 — 전부 AI 가 세운 것이라 물을 것이 없다 (§4.1-2).
    assert all(agenda["source"] is None for agenda in ai_agendas)
    assert all(row["track"] == "ai" for agenda in ai_agendas for row in agenda["lines"])
    first_round_ai_ids = {agenda["agenda_id"] for agenda in ai_agendas}

    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS, start_ms=9_000, text="라")
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)

    after = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    # **전량 교체다** — 앞 회차의 AI 안건과 줄이 하나도 남지 않고 안건 id 도 새로 났다.
    surviving_ai = [agenda for agenda in after if agenda["track"] == "ai"]
    assert [agenda["title"] for agenda in surviving_ai] == ["두 번째 정리"]
    assert not first_round_ai_ids & {agenda["agenda_id"] for agenda in surviving_ai}
    assert [row["text"] for agenda in surviving_ai for row in agenda["lines"]] == ["갈아끼운 줄"]
    # 사람 벌은 그 교체에 닿지 않는다.
    [still_memo] = [agenda for agenda in after if agenda["track"] == "memo"]
    assert still_memo["agenda_id"] == memo_agenda and still_memo["title"] == "첫 안건"
    assert [row["text"] for row in still_memo["lines"]] == ["사람이 적은 줄"]


def test_a_person_cannot_write_a_memo_into_the_ai_track(tmp_path) -> None:
    """**사람은 AI 벌에 적지 않는다** (SPEC §6-4 · §6-8 · §4.0-1).

    메모 칸의 드롭다운에는 사람 벌의 안건만 서고, 경로의 안건이 사람 벌의 것이 아니면 서버가 거절한다 —
    벌을 본문으로 고를 수 없다.
    """
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    agent.script = [_output([_agenda("AI 가 세운 안건", [_line("AI 줄")])])]
    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)

    agendas = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    [ai_agenda] = [row["agenda_id"] for row in agendas if row["track"] == "ai"]

    refused = client.post(
        f"/api/meetings/{meeting_id}/agendas/{ai_agenda}/lines", headers=MINA, json={"text": "여기 적으면 안 된다"}
    )
    assert refused.status_code == 422
    # 한 줄도 들어가지 않았다 — AI 벌은 배치가 쓴 것 그대로다.
    after = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [
        (row["track"], row["text"]) for agenda in after if agenda["track"] == "ai" for row in agenda["lines"]
    ] == [("ai", "AI 줄")]


def test_a_committed_batch_is_pushed_to_the_subscribers_at_once(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    pushed: list[dict] = []
    application.meeting_stream.push_ai_batch_threadsafe = lambda meeting, *, seq, agendas: pushed.append(
        {"meeting": meeting, "seq": seq, "agendas": agendas}
    )
    agent.script = [_output([_agenda("정리", [_line("밀려 나갈 줄")])])]
    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)

    assert len(pushed) == 1 and pushed[0]["meeting"] == meeting_id and pushed[0]["seq"] == 1
    # AI 트랙만 실린다 — 화면이 그 탭을 통째로 갈아끼운다.
    lines = [line for agenda in pushed[0]["agendas"] for line in agenda["lines"]]
    assert lines and all(line["track"] == "ai" for line in lines)


def test_a_provider_failure_is_quiet_and_its_span_joins_the_next_batch(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    batch = application.meeting_batch
    agent.script = [RuntimeError("대역: provider 실패"), _output([_agenda("나중에", [_line("합쳐서 낸 줄")])])]

    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    # 화면에 아무것도 나가지 않는다.
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"][0]["lines"] == []

    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    # 커서가 전진하지 않았으므로 같은 구간이 다시 실렸다.
    assert "가" * 20 in agent.runs[1]["prompt"]
    agendas = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    assert [row["text"] for agenda in agendas for row in agenda["lines"]] == ["합쳐서 낸 줄"]


def test_no_batch_is_submitted_once_the_meeting_has_ended(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id, count=2, chars=BATCH_CHARS)
    assert client.post(f"/api/meetings/{meeting_id}/end", headers=MINA).status_code == 200

    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    # 트리거는 평가되지만 「진행 중」이 아니므로 제출할 것이 없다 — 합성은 SCAX-WP-004 의 다른 진입점이다.
    assert agent.runs == []


def test_the_batch_turn_binds_the_output_schema_to_the_provider(tmp_path) -> None:
    """대화로 돌아도 출력이 스키마를 벗어나지 못한다 (SCAX-SPEC-004 §7.2-6).

    강제와 검증은 다른 층이다 — provider 에 파일로 걸고, 받은 JSON 을 서버가 같은 스키마로 다시 본다.
    """
    from ax_workspace.modules.meetings.batch import OUTPUT_SCHEMA

    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url))
    application = app.state.workflow_application
    seen: list[Any] = []

    class RecordingProvider:
        def generate(self, request):  # pragma: no cover - 배치는 대화로 돈다
            raise AssertionError("배치는 generate 를 쓰지 않는다")

        def converse(self, request, *, sink=None, cancel=None):
            seen.append(request)
            from ax_workspace.modules.ax_execution.ai import AiConversationResult

            return AiConversationResult("run", "session-1", _output([]), [])

    application._report_provider = RecordingProvider()
    agent = application.meeting_batch._agent
    agent.open_session(persona_id="mina", prompt="warm", tools=("task_list",))
    agent.run_batch(persona_id="mina", session_ref="session-1", prompt="batch", tools=("task_list",))

    warm, batch = seen
    # 웜스타트는 「준비됨」 한 마디라 스키마를 걸지 않는다. 배치는 건다.
    assert warm.output_schema is None
    assert batch.output_schema is OUTPUT_SCHEMA


def test_the_cli_turn_passes_the_schema_file_to_the_provider(tmp_path) -> None:
    from ax_workspace.modules.ax_execution.ai import AiConversationRequest, AiDelegatedToolContext
    from ax_workspace.platform.codex_cli import CodexCliMcpServer, CodexCliProviderAdapter

    adapter = CodexCliProviderAdapter(scax_mcp_server=CodexCliMcpServer("py", ("-m", "x"), {}))
    request = AiConversationRequest(
        prompt="p",
        provider_session_ref="session-1",
        context_references=[],
        delegated_tool_context=AiDelegatedToolContext(principal_id="mina", causation_id="c"),
        output_schema={"type": "object"},
    )
    schema_path = tmp_path / "output-schema.json"
    arguments = adapter._conversation_arguments(request, schema_path, tmp_path / "out.txt", "p")
    assert "--output-schema" in arguments
    assert arguments[arguments.index("--output-schema") + 1] == str(schema_path)
    # **스키마는 언제나 걸린다** — 부르는 쪽이 자기 것을 주면 그것이고, 주지 않으면 대화 계약이다.
    # (머지 전에는 스키마 없는 대화가 있었다. main 이 대화에도 계약을 걸면서 「없는 경우」가 사라졌다.)
    from ax_workspace.platform.codex_cli import _CONVERSATION_OUTPUT_SCHEMA

    plain = AiConversationRequest(
        prompt="p",
        provider_session_ref="session-1",
        context_references=[],
        delegated_tool_context=AiDelegatedToolContext(principal_id="mina", causation_id="c"),
    )
    assert plain.output_schema is None
    assert _CONVERSATION_OUTPUT_SCHEMA


def test_a_trigger_that_arrives_mid_batch_is_repaid_when_that_batch_ends(tmp_path) -> None:
    """도는 중에 온 트리거를 잊지 않는다 — 90초 타이머를 기다리게 하지 않는다."""
    client, application, agent = _stack(tmp_path)
    meeting_id = _running(client)["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    batch = application.meeting_batch
    _blocks(application, meeting_id, count=1, chars=BATCH_CHARS)

    original = agent.run_batch
    arrived: list[bool] = []

    def reentrant(**kwargs) -> str:
        # 첫 배치가 도는 동안에만 말이 더 쌓이고 안건이 바뀌었다 — 새 배치를 내지 않고 사유를 기억해 둔다.
        if not arrived:
            arrived.append(True)
            _blocks(application, meeting_id, count=1, chars=BATCH_SWITCH_MIN_CHARS, start_ms=30_000, text="나")
            assert batch.evaluate(meeting_id, CAUSE_AGENDA_SWITCH) is False
        return original(**kwargs)

    agent.run_batch = reentrant
    batch.evaluate(meeting_id, CAUSE_TRANSCRIPT)
    batch.drain()

    # 끝난 자리에서 그 트리거를 갚았다 — 두 번째 제출이 있다.
    assert len(agent.runs) == 2
    assert batch.is_timer_armed(meeting_id) is False


# --------------------------------------------------------------------- D46 · 회의 중 다음 할 일 후보


def _agendas_of(client: TestClient, meeting_id: str, *, track: str = "ai") -> list[dict]:
    """상세 응답은 **세 벌을 전부** 낸다 (§8-11) — 무엇을 보는지는 부르는 쪽이 벌로 고른다."""
    agendas = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    return [agenda for agenda in agendas if agenda["track"] == track]


def test_the_batch_brings_follow_up_candidates_and_they_land_as_provisional(tmp_path) -> None:
    """회의 중 배치가 안건별 후속 업무 후보를 함께 낸다 (사용자 결정 D46, 2026-09-11).

    아직 **후보일 뿐**이라 `provisional` 로 선다 — 다음 회차가 통째로 갈아 끼운다.
    """
    client, application, agent = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [
        _output([
            _agenda("AI 가 가른 화제", [_line("AI 가 낸 줄")],
                    todos=[_todo("계약서를 검토한다", due="2026-09-20"), _todo("일정을 잡는다")]),
        ])
    ]
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()

    [agenda] = _agendas_of(client, meeting_id)
    assert [row["title"] for row in agenda["todos"]] == ["계약서를 검토한다", "일정을 잡는다"]
    assert all(row["provisional"] is True for row in agenda["todos"])
    assert agenda["todos"][0]["due_candidate"] == "2026-09-20"
    assert agenda["todos"][1]["due_candidate"] is None
    # 담당자 칸은 없다 — AI 가 고르지 않는다.
    assert all("assignee" not in row for row in agenda["todos"])


def test_the_next_batch_replaces_the_candidates_whole(tmp_path) -> None:
    """AI 트랙 줄과 같은 결이다 — 그 회차 출력이 곧 전체다."""
    client, application, agent = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    application.meeting_batch.drain()

    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [_output([_agenda("AI 가 가른 화제", [], todos=[_todo("먼저 낸 후보")])])]
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert [row["title"] for row in _agendas_of(client, meeting_id)[0]["todos"]] == ["먼저 낸 후보"]

    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS, start_ms=100_000)
    agent.script = [_output([_agenda("AI 가 가른 화제", [], todos=[_todo("다시 낸 후보")])])]
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert [row["title"] for row in _agendas_of(client, meeting_id)[0]["todos"]] == ["다시 낸 후보"]


def test_a_batch_that_brings_no_candidate_is_still_a_good_batch(tmp_path) -> None:
    """낼 것이 없으면 빈 배열이다 — 키가 없는 것이 아니라 비어 있는 것이다."""
    client, application, agent = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [_output([_agenda("AI 가 가른 화제", [_line("줄만 있다")])])]

    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()
    assert _agendas_of(client, meeting_id)[0]["todos"] == []


def test_the_pushed_batch_frame_carries_the_candidates(tmp_path) -> None:
    """화면이 배치를 받은 뒤에는 프레임만 읽고도 후보를 그린다."""
    client, application, agent = _stack(tmp_path)
    made = _running(client)
    meeting_id = made["meeting"]["meeting_id"]
    application.meeting_batch.drain()

    pushed: list[dict] = []
    application.schedule_push_ai_batch = lambda meeting, *, seq, agendas: pushed.append(
        {"seq": seq, "agendas": agendas}
    )
    _blocks(application, meeting_id, count=3, chars=BATCH_CHARS)
    agent.script = [
        _output([_agenda("AI 가 가른 화제", [_line("AI 줄")], todos=[_todo("프레임에 실릴 후보")])])
    ]
    assert application.meeting_batch.evaluate(meeting_id, CAUSE_TRANSCRIPT) is True
    application.meeting_batch.drain()

    [frame] = pushed
    [agenda] = frame["agendas"]
    assert [row["title"] for row in agenda["todos"]] == ["프레임에 실릴 후보"]
    assert agenda["todos"][0]["provisional"] is True
    # 줄은 AI 트랙만 실린다 — 그 규칙은 그대로다.
    assert all(line["track"] == "ai" for line in agenda["lines"])
