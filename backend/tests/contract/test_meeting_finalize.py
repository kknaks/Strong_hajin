"""종료 합성 · 후속업무 승격 · 내보내기 (SCAX-SPEC-004 §8 · §9 · SCAX-WP-004 Phase 1~3).

회의가 끝나면 사람이 아무것도 하지 않아도 회의록이 서 있어야 한다 — 이 제품의 약속이 여기서 지켜진다.
실제 provider 를 부르지 않는다: `FinalizeAgent` 경계에서 대역을 끼운다.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.domain import MeetingStatus

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


# --------------------------------------------------------------------- 대역


class FakeFinalizeAgent:
    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.script: list[str | Exception] = []

    def run_final(self, *, persona_id: str, session_ref: str | None, prompt: str) -> str:
        self.runs.append({"persona_id": persona_id, "session_ref": session_ref, "prompt": prompt})
        if not self.script:
            return _output([])
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class NoopBatchAgent:
    """Finalization tests never exercise the live meeting batch provider."""

    def open_session(self, *, persona_id: str, prompt: str, tools: tuple[str, ...]) -> str:
        del persona_id, prompt, tools
        return "finalization-test-session"

    def run_batch(self, *, persona_id: str, session_ref: str, prompt: str, tools: tuple[str, ...]) -> str:
        raise AssertionError("finalization tests must not run a live meeting batch")


def _output(agendas: list[dict], *, title_candidate: str | None = None) -> str:
    return json.dumps({"title_candidate": title_candidate, "agendas": agendas}, ensure_ascii=False)


def _agenda(title: str, *, merged_from: list[str] | None = None, concluded: bool = False,
            lines: list[dict] | None = None, todos: list[dict] | None = None) -> dict:
    """합성 출력의 안건 하나 — **최종 벌의 안건이다.**

    `agenda_id` 도 `source` 도 없다 (SPEC v0.5 §8-5 · §4.1-2): 최종 벌은 이어 쓸 대상이 없고, 출처는
    사람 벌 안의 값이다. 그 자리에 **계보**(`merged_from`)가 온다 — 이 최종 안건이 묶은 원본 안건 id 다.
    """
    return {
        "title": title, "merged_from": list(merged_from or []), "concluded": concluded,
        "lines": lines or [], "todos": todos or [],
    }


def _line(text: str, *, evidence: list[dict] | None = None, from_lines: list[str] | None = None) -> dict:
    """합성 출력의 줄 하나. `from_lines` 가 그 줄이 딛는 **원본 줄** 계보다 (§4.2-10)."""
    return {"text": text, "evidence": evidence or [], "from_lines": list(from_lines or [])}


def _track(detail: dict, track: str) -> list[dict]:
    """상세 응답은 **세 벌을 전부** 낸다 (SPEC §8-11 · D53) — 무엇을 보는지 벌로 고른다."""
    return [agenda for agenda in detail["agendas"] if agenda["track"] == track]


def _the_final(client: TestClient, meeting_id: str, headers: dict | None = None) -> dict:
    """최종 벌의 안건 하나 — 대부분의 시험이 한 안건만 내게 하고 그 하나를 본다."""
    detail = client.get(f"/api/meetings/{meeting_id}", headers=headers or MINA).json()
    [agenda] = _track(detail, "final")
    return agenda


def _todo(title: str, *, description: str = "무엇을 왜 해야 하는지 두 문장.", due: str | None = None,
          checklist: list[str] | None = None, line_ids: list[str] | None = None) -> dict:
    return {
        "title": title, "description": description, "due_candidate": due,
        "checklist_candidate": checklist or ["초안 잡기", "검토 받기"], "line_ids": line_ids or [],
    }


# --------------------------------------------------------------------- 발판


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url, recordings_dir=str(tmp_path / "audio")))
    application = app.state.workflow_application
    application.meeting_batch._agent = NoopBatchAgent()
    agent = FakeFinalizeAgent()
    application.meeting_finalize._agent = agent
    return TestClient(app), application, agent


def _summarizing(
    client: TestClient, application, *, agendas=("첫 안건",), attendees=("jiho",), recording: bool = True
) -> dict:
    starts = datetime.now(UTC) + timedelta(minutes=5)
    made = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "합성할 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": list(attendees),
            "agendas": [{"title": name} for name in agendas],
        },
    ).json()
    meeting_id = made["meeting"]["meeting_id"]
    client.post(f"/api/meetings/{meeting_id}/start", headers=MINA)
    application.meeting_batch.drain()
    if recording:
        # 종료는 언제나 ① 재전사를 지난다 (D44 정정) — 실물과 같은 모양으로 음원과 대역을 세운다.
        _with_recording(application, meeting_id)
    return made


def _blocks(application, meeting_id: str, *, count: int = 2) -> None:
    """확정 발화를 직접 쌓는다 — 스트림(WP-002)이 하는 일을 시험이 대신한다.

    블록을 3초씩 띄운다: 경계 규칙이 2초 침묵에서 블록을 닫으므로, 붙여 두면 재전사가 같은 줄을
    다시 묶을 때 하나로 합쳐진다. 실제 회의도 블록 사이에 그만큼은 벌어져 있다.
    """
    from ax_workspace.platform.persistence import MeetingTranscriptRecord

    with application._session_factory() as session:
        for index in range(count):
            session.add(
                MeetingTranscriptRecord(
                    meeting_id=UUID(meeting_id), seq=index + 1, speaker_label="1",
                    at_ms=index * 3_000, end_ms=index * 3_000 + 900,
                    text=f"확정 발화 {index}", created_at=datetime.now(UTC),
                )
            )
        session.commit()


class _EchoTranscriber:
    """시험의 기본 재전사 — **같은 말을 그대로 다시 들은** 것으로 친다.

    폴백이 없어진 뒤로(D44 정정) 종료는 언제나 ① 재전사를 지난다. 합성만 보려는 시험까지 음원 대역을
    일일이 세우게 하면 시험이 읽히지 않으므로, 기본은 「두 번째로 들어도 같더라」로 둔다 —
    원문 내용이 그대로라 기존 단정이 그대로 산다. 다른 결과를 보려는 시험은 이 자리를 갈아 끼운다.
    """

    def __init__(self, application, meeting_id: str) -> None:
        self._application = application
        self._meeting_id = meeting_id
        self.calls: list = []

    def transcribe(self, recording):
        from sqlalchemy import select

        from ax_workspace.modules.meetings.stream import SttToken
        from ax_workspace.platform.persistence import MeetingTranscriptRecord

        self.calls.append(recording)
        with self._application._session_factory() as session:
            rows = list(session.scalars(
                select(MeetingTranscriptRecord)
                .where(MeetingTranscriptRecord.meeting_id == UUID(self._meeting_id))
                .order_by(MeetingTranscriptRecord.seq)
            ))
        if not rows:
            # 한 줄도 없던 회의 — 재전사가 빈 결과를 내면 실패이므로 한 마디는 들린 것으로 둔다.
            return [SttToken(text="다시 들은 말", is_final=True, speaker="1", start_ms=0, end_ms=900)]
        return [
            SttToken(
                text=row.text, is_final=True, speaker=row.speaker_label,
                start_ms=row.at_ms - recording.base_ms, end_ms=row.end_ms - recording.base_ms,
            )
            for row in rows
        ]


def _worker(application, settings_url: str):
    from ax_workspace.bootstrap.meeting_worker import MeetingFinalizeWorker

    settings = Settings(RuntimeProfile.TEST, settings_url)
    return MeetingFinalizeWorker(settings, application=application, queue_factory=lambda session: application.job_queue(session))


# --------------------------------------------------------------------- Phase 1 · 종료 파이프라인


def test_ending_a_meeting_answers_at_once_and_leaves_the_merge_to_a_job(tmp_path) -> None:
    """사람이 종료를 누르고 provider 를 기다리지 않는다 (SPEC §8-1)."""
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    _blocks(application, meeting_id)

    ended = client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert ended.status_code == 200
    assert ended.json()["meeting"]["status"] == "summarizing"
    # 응답이 나갈 때까지 provider 는 불리지 않았다.
    assert agent.runs == []

    from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE

    with application._session_factory() as session:
        claimed = application.job_queue(session).claim(
            JOB_KIND_MEETING_FINALIZE, limit=5, lease_seconds=60, worker_id="test"
        )
        session.commit()
    assert [job.payload["meeting_id"] for job in claimed] == [meeting_id]


def test_a_merge_that_succeeds_closes_the_meeting_with_its_notes_already_written(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    agent.script = [
        _output([
            _agenda("첫 안건", merged_from=[human], concluded=True,
                    lines=[_line("합쳐진 줄", evidence=[{"from_ms": 0, "to_ms": 900}])],
                    todos=[_todo("계약서를 검토한다")]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "done"
    # **최종 벌은 새로 섰고 원본 두 벌은 그대로 남는다** (§8-5 · §8-11 · D53).
    [human_agenda] = _track(detail, "memo")
    [agenda] = _track(detail, "final")
    assert agenda["agenda_id"] != human_agenda["agenda_id"]
    # 결론 표시가 서는 것은 최종 벌뿐이다 (§4.0-5).
    assert agenda["concluded"] is True and human_agenda["concluded"] is False
    # 계보가 「이 최종 안건은 그 사람 안건에서 나왔다」를 말한다 (§4.1-3).
    assert agenda["merged_from"] == [human_agenda["agenda_id"]]
    assert [line["text"] for line in agenda["lines"]] == ["합쳐진 줄"]
    assert all(line["track"] == "final" for line in agenda["lines"])
    [todo] = agenda["todos"]
    assert todo["title"] == "계약서를 검토한다"
    assert todo["linked"] is None
    # 담당자는 후보에 없다 (SPEC §8.2).
    assert "assignee" not in todo and "assignee_candidate" not in todo
    # 같은 세션을 이어 쓴다 — 회의를 처음부터 다시 읽히지 않는다 (§8-3).
    assert agent.runs[0]["session_ref"] is not None
    assert "확정 발화 0" not in agent.runs[0]["prompt"]


def test_a_merge_that_fails_leaves_the_speech_and_memos_and_marks_the_meeting_failed(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    agenda_id = made["agendas"][0]["agenda_id"]
    client.post(f"/api/meetings/{meeting_id}/agendas/{agenda_id}/lines", headers=MINA, json={"text": "사람이 적은 것"})
    _blocks(application, meeting_id)
    agent.script = [RuntimeError("대역: provider 실패")] * 3

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is False

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "failed"
    assert detail["meeting"]["failure_reason"]
    # 받은 발화와 메모는 그대로 남는다 (§8-8).
    script = client.get(f"/api/meetings/{meeting_id}/transcript", headers=MINA).json()
    assert len(script["items"]) == 2 and [row["text"] for row in script["memos"]] == ["사람이 적은 것"]


def test_retry_runs_the_merge_again_without_touching_the_original(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    agent.script = [RuntimeError("실패")] * 3
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    application.finalize_meeting(UUID(meeting_id))

    # 「실패」에서만 열린다.
    agent.script = [_output([_agenda("첫 안건", merged_from=[human], lines=[_line("두 번째에 성공")])])]
    retried = client.post(f"/api/meetings/{meeting_id}/finalize", headers=MINA)
    assert retried.status_code == 200
    assert retried.json()["meeting"]["status"] == "summarizing"
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "done"
    assert len(client.get(f"/api/meetings/{meeting_id}/transcript", headers=MINA).json()["items"]) == 2
    # 「완료」에서 다시 걸 수는 없다 — 재생성을 두지 않는다 (§5.1).
    assert client.post(f"/api/meetings/{meeting_id}/finalize", headers=MINA).status_code == 409


def test_the_worker_claims_the_job_and_finishes_the_delivery(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    agent.script = [_output([_agenda("첫 안건", merged_from=[human], lines=[_line("워커가 낸 줄")])])]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)

    worker = _worker(application, application._settings.database_url)
    assert asyncio.run(worker.run_once()) is True
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "done"
    # 배달은 끝났다 — 같은 잡이 다시 오지 않는다.
    assert asyncio.run(worker.run_once()) is False


def _orphan_line_count(application, meeting_id: str) -> int:
    """이미 없는 안건을 가리키는 줄의 수. postgres 라면 이런 줄은 애초에 생기지 못한다 — 외래키가 막는다."""
    from sqlalchemy import select

    from ax_workspace.platform.persistence import MeetingAgendaRecord, MeetingLineRecord

    with application._session_factory() as session:
        alive = set(
            session.scalars(
                select(MeetingAgendaRecord.id).where(MeetingAgendaRecord.meeting_id == UUID(meeting_id))
            )
        )
        held = list(
            session.scalars(
                select(MeetingLineRecord.agenda_id).where(MeetingLineRecord.meeting_id == UUID(meeting_id))
            )
        )
    return sum(1 for agenda_id in held if agenda_id not in alive)


def _ai_agenda_with_lines(application, meeting_id: str, title: str, texts: list[str]) -> None:
    """회의 중 배치가 안건을 새로 세우고 거기에 자기 줄을 매다는 상태를 만든다 — 배치와 **같은 통로**로."""
    import json as _json

    from ax_workspace.modules.meetings.batch import parse_output

    payload = _json.dumps(
        {
            "agendas": [
                {
                    "title": title,
                    "lines": [{"text": text, "evidence": [], "task_id": None} for text in texts],
                    # 배치 출력은 `todos` 를 **언제나** 싣는다 — 낼 것이 없으면 빈 배열이다 (D46).
                    "todos": [],
                }
            ]
        },
        ensure_ascii=False,
    )
    with application._session_factory() as session:
        application._meetings(session).replace_ai_track(UUID(meeting_id), parse_output(payload))
        session.commit()


def test_the_merge_writes_a_new_final_track_and_leaves_both_origin_tracks_untouched(tmp_path) -> None:
    """**합성은 원본 두 벌을 건드리지 않는다** (SPEC v0.5 §8-5 · §4.0-4 · D53).

    0.4.x 는 `agenda_id` 가 오면 사람 안건이든 AI 안건이든 **그 안건을 이어 써서** 「사람 것이
    보존됐는가」를 물을 수가 없었다. 이제 최종 벌이 자기 안건과 줄을 갖고, 원본 두 벌은 최종본을
    대조하는 근거로 **이 저장 전후가 한 글자도 같아야** 한다.

    **줄이 안건보다 오래 살지 않는다**도 함께 본다. SQLite 는 외래키를 강제하지 않으므로(postgres 는
    한다) 최종 벌을 비울 때 매달린 줄을 먼저 떼지 않으면 여기서 고아 줄이 남는다.
    """
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    [human_agenda] = [row["agenda_id"] for row in made["agendas"] if row["track"] == "memo"]
    memo = client.post(
        f"/api/meetings/{meeting_id}/agendas/{human_agenda}/lines", headers=MINA, json={"text": "사람이 적은 줄"}
    ).json()["line_id"]
    _blocks(application, meeting_id)
    _ai_agenda_with_lines(application, meeting_id, "온보딩 자료", ["배치가 낸 줄 하나", "배치가 낸 줄 둘"])

    before = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    origin_before = [row for row in before if row["track"] != "final"]
    assert [row["track"] for row in origin_before] == ["memo", "ai"]
    ai_agenda = next(row for row in origin_before if row["track"] == "ai")["agenda_id"]

    agent.script = [
        _output(
            [
                _agenda("권한과 온보딩", merged_from=[human_agenda, ai_agenda],
                        lines=[_line("두 벌을 합쳐 새로 쓴 줄", from_lines=[memo])]),
            ]
        )
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "done" and detail["meeting"]["failure_reason"] is None
    # **원본 두 벌이 저장 전과 한 글자도 다르지 않다** — 그것이 D-4 가 지키는 것이다.
    origin_after = [row for row in detail["agendas"] if row["track"] != "final"]
    assert origin_after == origin_before

    # 최종 벌은 **새로 선 안건 하나**이고 두 원본 안건을 계보로 든다.
    [final] = _track(detail, "final")
    assert final["agenda_id"] not in {human_agenda, ai_agenda}
    assert final["merged_from"] == [human_agenda, ai_agenda]
    assert final["source"] is None
    assert [row["text"] for row in final["lines"]] == ["두 벌을 합쳐 새로 쓴 줄"]
    assert final["lines"][0]["from_lines"] == [memo]
    assert _orphan_line_count(application, meeting_id) == 0


def test_the_two_origin_tracks_stay_readable_and_read_only_once_the_meeting_is_closed(tmp_path) -> None:
    """**원본 두 벌은 종료 뒤에도 남고, 읽기 전용이다** (SPEC §8-11 · §6-9 · D53).

    0.4.x 는 데이터는 남기고 **상세 응답이 `final` 줄만 골라 내어** 원본을 가렸다. 최종 벌이 AI 의
    저작이므로 사람이 자기가 적은 것과 대조할 길이 없으면 최종본을 검증할 근거가 사라진다.
    """
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    [human_agenda] = [row["agenda_id"] for row in made["agendas"] if row["track"] == "memo"]
    client.post(
        f"/api/meetings/{meeting_id}/agendas/{human_agenda}/lines", headers=MINA, json={"text": "사람이 적은 줄"}
    )
    _blocks(application, meeting_id)
    _ai_agenda_with_lines(application, meeting_id, "온보딩 자료", ["배치가 낸 줄"])
    ai_agenda = next(
        row["agenda_id"]
        for row in client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
        if row["track"] == "ai"
    )

    agent.script = [_output([_agenda("최종", merged_from=[human_agenda], lines=[_line("최종 줄")])])]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    # **세 벌이 전부 응답에 있다.**
    assert {row["track"] for row in detail["agendas"]} == {"memo", "ai", "final"}
    assert [row["text"] for row in _track(detail, "memo")[0]["lines"]] == ["사람이 적은 줄"]
    assert [row["text"] for row in _track(detail, "ai")[0]["lines"]] == ["배치가 낸 줄"]
    # 게이트도 그렇게 말한다 — 「종료」에는 최종 벌만 열린다.
    assert detail["meeting"]["can_edit_agendas"] == {"memo": False, "ai": False, "final": True}
    assert detail["meeting"]["can_add_agenda"] == {"memo": False, "ai": False, "final": True}

    # **고치는 자리가 없다** — 제목도, 줄도, 삭제도 원본 두 벌에는 열리지 않는다.
    for origin in (human_agenda, ai_agenda):
        path = f"/api/meetings/{meeting_id}/agendas/{origin}"
        assert client.patch(path, headers=MINA, json={"title": "고쳐 본다"}).status_code == 409
        assert client.patch(path, headers=MINA, json={"lines": [{"text": "덮어 본다"}]}).status_code == 409
        assert client.delete(path, headers=MINA).status_code == 409

    after = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert after["agendas"] == detail["agendas"]


def _job_rows(application, meeting_id: str) -> list[dict]:
    """이 회의의 합성 잡. 시험 프로필은 in-process transport 를 쓴다 — 상태·시도수는 거기 산다."""
    return [
        row
        for row in application.memory_job_queue._jobs
        if row["kind"] == "meeting.finalize" and str(row["payload"]["meeting_id"]) == meeting_id
    ]


def _make_job_available(application, meeting_id: str) -> None:
    """되돌린 배달의 backoff 를 건너뛴다 — 워커가 기다렸다가 다시 집는 것을 시험이 대신한다."""
    from datetime import UTC as _UTC, datetime as _datetime

    for row in _job_rows(application, meeting_id):
        row["available_at"] = _datetime.now(_UTC)


def test_a_delivery_that_raises_is_not_closed_as_completed_while_the_meeting_waits(tmp_path) -> None:
    """합성이 **예외로** 끝나면 회의는 아직 「정리 중」이다 — 그 배달을 끝내면 회의가 갇힌다.

    상한까지 되돌리고, 그래도 안 되면 잡을 `failed` 로 닫으면서 회의도 「실패」로 보낸다.
    """
    from ax_workspace.bootstrap.meeting_worker import MAX_DELIVERIES

    client, application, _ = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    _blocks(application, meeting_id)
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)

    calls: list[UUID] = []

    def _explode(target: UUID) -> bool:
        calls.append(target)
        raise RuntimeError("대역: 합성이 제 실패를 다루지 못했다")

    application.finalize_meeting = _explode
    worker = _worker(application, application._settings.database_url)

    for delivery in range(1, MAX_DELIVERIES):
        assert asyncio.run(worker.run_once()) is True
        # 아직 끝나지 않았다 — 되돌린 배달이므로 회의는 「정리 중」 그대로다.
        assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "summarizing"
        # **`completed` 로 닫히지 않았다** — 이 한 줄이 실물에서 무너진 계약이다.
        assert [row["state"] for row in _job_rows(application, meeting_id)] == ["queued"], delivery
        _make_job_available(application, meeting_id)

    assert asyncio.run(worker.run_once()) is True
    assert len(calls) == MAX_DELIVERIES
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "failed"
    assert "RuntimeError" in detail["meeting"]["failure_reason"]
    rows = _job_rows(application, meeting_id)
    assert [row["state"] for row in rows] == ["failed"] and rows[0]["attempt_count"] == MAX_DELIVERIES


def test_two_human_agendas_may_be_folded_into_one_when_the_merge_rewrites_the_note(tmp_path) -> None:
    """**안건 목록도 AI 가 다시 잡는다** (사용자 결정 2026-09-11).

    사람이 예약 때 적은 안건 제목은 재료의 하나일 뿐이다: 합쳐도 되고 나눠도 된다. 예전에는 이것이
    「사람 안건을 빠뜨렸다」로 실패였는데, 그 검사가 통합 회의록의 뜻과 어긋나 사라졌다.
    """
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application, agendas=("첫 안건", "둘째 안건"))
    meeting_id = made["meeting"]["meeting_id"]
    first, second = (agenda["agenda_id"] for agenda in made["agendas"])
    _blocks(application, meeting_id)
    # 둘을 하나로 합쳐 냈다 — 최종 안건 하나를 세우고 계보에 원본 둘을 적는다 (§4.1-3).
    agent.script = [
        _output([_agenda("둘을 합친 안건", merged_from=[first, second], lines=[_line("합쳐 쓴 줄")])])
    ]

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "done"
    assert detail["meeting"]["failure_reason"] is None
    # 최종 안건 하나가 섰고 **계보가 묶은 둘을 든다** — 「내가 적은 안건이 어디로 갔나」에 그것이 답한다.
    [folded] = _track(detail, "final")
    assert folded["title"] == "둘을 합친 안건" and folded["source"] is None
    assert folded["merged_from"] == [first, second]
    assert [row["text"] for row in folded["lines"]] == ["합쳐 쓴 줄"]
    # 원본 두 안건은 **그대로 남는다** — 묶였다는 것이 사라졌다는 뜻이 아니다 (D53).
    assert [agenda["agenda_id"] for agenda in _track(detail, "memo")] == [first, second]
    # provider 를 한 번만 불렀다 — 시도 상한까지 헛돌지 않았다.
    assert len(agent.runs) == 1


def test_two_tracks_that_said_the_same_thing_fold_into_one_line(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    memo = client.post(
        f"/api/meetings/{meeting_id}/agendas/{human}/lines", headers=MINA, json={"text": "권한부터 정한다"}
    ).json()["line_id"]
    _blocks(application, meeting_id)
    agent.script = [
        _output([
            _agenda("첫 안건", merged_from=[human],
                    lines=[_line("권한을 먼저 정한다", evidence=[{"from_ms": 0, "to_ms": 900}], from_lines=[memo, "ai-1"])]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    application.finalize_meeting(UUID(meeting_id))

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    [final] = _track(detail, "final")
    assert [line["text"] for line in final["lines"]] == ["권한을 먼저 정한다"]
    # **계보는 존재하는 원본 줄만 남는다** — 없는 id(`ai-1`)는 그 id 만 버리고 줄 자체는 산다 (§8-6).
    assert final["lines"][0]["from_lines"] == [memo]
    # 사람 메모는 지워지지 않는다 — 합성은 최종 벌에만 쓴다 (D53).
    assert [line["text"] for line in _track(detail, "memo")[0]["lines"]] == ["권한부터 정한다"]


def test_a_titleless_meeting_gets_a_candidate_the_person_still_has_to_save(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    quick = client.post("/api/meetings/quick-start", headers=MINA, json={}).json()
    meeting_id = quick["meeting"]["meeting_id"]
    application.meeting_batch.drain()
    _blocks(application, meeting_id)
    _with_recording(application, meeting_id)
    # 바로 시작한 회의도 기본 안건 하나를 이고 선다 (D32) — 사람 안건이므로 합성이 반드시 덮는다 (§8-6).
    default_agenda = quick["agendas"][0]["agenda_id"]
    agent.script = [
        _output(
            [
                _agenda("안건 1", merged_from=[default_agenda]),
                _agenda("AI 가 세운 안건", lines=[_line("무슨 이야기를 했다")]),
            ],
            title_candidate="권한 모델 회의",
        )
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    application.finalize_meeting(UUID(meeting_id))

    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    # 사람이 저장해야 제목이 된다 — 그전까지는 제목이 없다 (§8-5 · D14).
    assert head["title"] is None
    assert head["title_candidate"] == "권한 모델 회의"


def test_a_candidate_that_is_already_work_is_not_offered(tmp_path) -> None:
    """이미 있는 업무면 후보를 내지 않는다 (SPEC §8.1-1)."""
    client, application, agent = _stack(tmp_path)
    client.post("/api/tasks", headers=MINA, json={"title": "계약서 검토"})
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    agent.script = [
        _output([
            _agenda("첫 안건", merged_from=[human],
                    todos=[_todo("계약서 검토"), _todo("새로 생긴 일")]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    application.finalize_meeting(UUID(meeting_id))

    todos = _the_final(client, meeting_id)["todos"]
    assert [todo["title"] for todo in todos] == ["새로 생긴 일"]


# --------------------------------------------------------------------- Phase 3 · 편집 · 승격 · 내보내기


def _finalized(client: TestClient, application, agent, *, todos: list[dict] | None = None) -> tuple[str, str]:
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    agent.script = [
        _output([
            _agenda("첫 안건", merged_from=[human],
                    lines=[_line("합성이 낸 줄", evidence=[{"from_ms": 0, "to_ms": 900}])],
                    todos=todos if todos is not None else [_todo("계약서를 검토한다")]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True
    # **최종 벌의** 안건을 돌려준다 — 편집도 승격도 그 벌에서만 열린다 (§8-9 · §9-1).
    return meeting_id, _the_final(client, meeting_id)["agenda_id"]


def test_saving_an_agenda_that_someone_else_already_saved_is_refused_with_what_is_there_now(tmp_path) -> None:
    """판정은 안건 단위다 — 그 사이에 그 안건이 저장됐으면 덮어쓰지 않는다 (SPEC §8-9)."""
    client, application, agent = _stack(tmp_path)
    meeting_id, agenda_id = _finalized(client, application, agent)
    path = f"/api/meetings/{meeting_id}/agendas/{agenda_id}"

    stale = _the_final(client, meeting_id)["last_saved_at"]
    first = client.patch(path, headers=MINA, json={"lines": [{"text": "다른 탭이 먼저 저장한 줄"}], "expected_last_saved_at": stale})
    assert first.status_code == 200

    refused = client.patch(path, headers=MINA, json={"lines": [{"text": "늦게 온 저장"}], "expected_last_saved_at": stale})
    assert refused.status_code == 409
    body = refused.json()["detail"]
    assert body["code"] == "meeting_agenda_stale"
    # 지금 있는 것을 함께 낸다 — 사람이 차이를 보고 정한다.
    assert [line["text"] for line in body["current"]["lines"] if line["track"] == "final"] == ["다른 탭이 먼저 저장한 줄"]

    fresh = _the_final(client, meeting_id)["last_saved_at"]
    assert client.patch(path, headers=MINA, json={"lines": [{"text": "이번엔 통과"}], "expected_last_saved_at": fresh}).status_code == 200


def test_promoting_a_candidate_creates_a_work_request_that_points_back_at_the_meeting(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, agenda_id = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]

    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote",
        headers=MINA,
        json={"assignee_id": "jiho"},
    )
    assert promoted.status_code == 201, promoted.text
    linked = promoted.json()["linked"]
    assert linked["work_request_id"] and linked["task_id"] is None

    request = client.get(f"/api/work-requests/{linked['work_request_id']}", headers=MINA).json()
    assert request["title"] == "계약서를 검토한다" and request["assignee_id"] == "jiho"

    # 출처는 **열로** 남는다 — description 문장이 아니라 두 id 로 좇는다 (§9-5 D20).
    from ax_workspace.platform.persistence import WorkRequestRecord

    with application._session_factory() as session:
        row = session.get(WorkRequestRecord, UUID(linked["work_request_id"]))
        assert str(row.source_meeting_id) == meeting_id
        assert str(row.source_agenda_id) == agenda_id
        assert row.initial_checklist == ["초안 잡기", "검토 받기"]

    # 승격 뒤에도 후보가 목록에 남는다 (§9-6).
    [after] = _the_final(client, meeting_id)["todos"]
    assert after["linked"]["work_request_id"] == linked["work_request_id"]


def test_promoting_the_same_candidate_twice_does_not_make_two_requests(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]
    path = f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote"

    assert client.post(path, headers=MINA, json={"assignee_id": "jiho"}).status_code == 201
    assert client.post(path, headers=MINA, json={"assignee_id": "jiho"}).status_code == 409
    assert len(client.get("/api/work-requests", headers=MINA).json()) == 1


def test_promoting_without_an_assignee_does_not_proceed(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]
    path = f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote"

    assert client.post(path, headers=MINA, json={}).status_code == 422
    assert client.post(path, headers=MINA, json={"assignee_id": ""}).status_code == 422


def test_accepting_the_request_carries_the_source_columns_onto_the_task(tmp_path) -> None:
    """업무 → 회의: 수락으로 업무가 설 때 요청의 출처가 업무로 옮겨진다 (SPEC §9-7)."""
    client, application, agent = _stack(tmp_path)
    meeting_id, agenda_id = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]
    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote", headers=MINA, json={"assignee_id": "jiho"}
    ).json()
    request_id = promoted["linked"]["work_request_id"]

    request = client.get(f"/api/work-requests/{request_id}", headers=JIHO).json()
    accepted = client.post(
        f"/api/work-requests/{request_id}/accept", headers=JIHO, json={"expected_version": request["version"]}
    )
    assert accepted.status_code == 200, accepted.text

    from ax_workspace.platform.persistence import TaskRecord
    from sqlalchemy import select

    with application._session_factory() as session:
        task = session.scalar(select(TaskRecord).where(TaskRecord.source_work_request_id == UUID(request_id)))
        assert str(task.source_meeting_id) == meeting_id
        assert str(task.source_agenda_id) == agenda_id
        assert task.origin_kind == "meeting"


def test_a_candidate_nobody_wants_is_deleted_without_a_confirmation(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent, todos=[_todo("지울 후보"), _todo("남을 후보")])
    todos = _the_final(client, meeting_id)["todos"]
    target = next(row for row in todos if row["title"] == "지울 후보")

    assert client.delete(f"/api/meetings/{meeting_id}/todos/{target['todo_id']}", headers=MINA).status_code == 204
    remaining = _the_final(client, meeting_id)["todos"]
    assert [row["title"] for row in remaining] == ["남을 후보"]


def test_a_meeting_that_already_closed_is_never_merged_again(tmp_path) -> None:
    """**「완료」에서 「정리 중」으로 돌아가는 길이 없다** (SPEC §10-19 · `MEETING_TRANSITIONS`).

    이 시험이 걸던 것이 바뀌었다. 0.4.x 에서는 「합성을 한 번 더 돌려도 **승격된 후보는 남는다**」
    (§8-5)였고, 그 길이 제품에 없어 저장소 시점에서 status 를 강제 기입해 만들어 확인했다. 벌이
    갈리면서 최종 벌을 다시 지으면 **안건까지 전량 비우므로**(§8-5 · §8-8) 그 안건에 매달린 승격 후보가
    함께 죽는다 — 「승격된 후보가 있는 최종 벌을 다시 지으면」은 SPEC 에 없는 자리다.

    그래서 이제 걸 것은 **그 길이 열려 있지 않다**는 것이다: 전이표가 done → summarizing 을 거절하고,
    그것이 승격된 후보가 최종 벌 비우기를 만나지 않는 **유일한 이유**다 (코디 결정 2026-09-14, 갈래 ③).
    이 전이가 열리면 `clear_track` 이 승격된 후보를 조용히 죽인다.
    """
    from ax_workspace.modules.meetings.domain import MEETING_TRANSITIONS, MeetingStateConflict, ensure_transition

    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]
    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote", headers=MINA, json={"assignee_id": "jiho"}
    )
    assert promoted.status_code == 201, promoted.text

    # 전이표가 그 길을 갖고 있지 않다 — 이것이 이 불변식의 첫 다리다.
    assert (MeetingStatus.DONE, MeetingStatus.SUMMARIZING) not in MEETING_TRANSITIONS
    with pytest.raises(MeetingStateConflict):
        ensure_transition(MeetingStatus.DONE, MeetingStatus.SUMMARIZING)

    # [다시 시도] 표면도 「완료」에서는 열리지 않는다.
    refused = client.post(f"/api/meetings/{meeting_id}/finalize", headers=MINA)
    assert refused.status_code == 409, refused.text

    # 승격된 후보가 그대로 서 있다 — 최종 벌을 다시 비우는 일이 애초에 일어나지 않았다.
    [still] = _the_final(client, meeting_id)["todos"]
    assert still["title"] == "계약서를 검토한다" and still["linked"] is not None


def test_saving_the_final_track_keeps_the_lineage_of_every_line_it_did_not_change(tmp_path) -> None:
    """**계보는 줄 단위로만 사라진다** (SPEC-004 v0.5.1 §8-9 · §4.2-10 · 검수 F-3).

    저장이 그 안건 줄을 전부 새로 만들면 손대지 않은 줄의 계보가 **저장 한 번에 전멸**하고, 「내가 적은
    것이 최종본에 살아남았나」를 물을 재료(`OQ-308` 이 딛는 것)가 사라진다. 그래서 저장은 줄마다 id 를
    싣고 서버가 그 id 로 가른다 — 다섯 갈래를 한 번에 본다.
    """
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    [memo_agenda] = [row["agenda_id"] for row in made["agendas"] if row["track"] == "memo"]
    kept_memo = client.post(
        f"/api/meetings/{meeting_id}/agendas/{memo_agenda}/lines", headers=MINA, json={"text": "그대로 둘 줄의 뿌리"}
    ).json()["line_id"]
    edited_memo = client.post(
        f"/api/meetings/{meeting_id}/agendas/{memo_agenda}/lines", headers=MINA, json={"text": "고칠 줄의 뿌리"}
    ).json()["line_id"]
    _blocks(application, meeting_id)

    agent.script = [
        _output([
            _agenda("최종", merged_from=[memo_agenda], lines=[
                _line("손대지 않을 줄", from_lines=[kept_memo]),
                _line("사람이 고칠 줄", from_lines=[edited_memo]),
                _line("사람이 지울 줄", from_lines=[kept_memo, edited_memo]),
            ]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    final = _the_final(client, meeting_id)
    untouched, to_edit, to_drop = final["lines"]
    assert [row["from_lines"] for row in final["lines"]] == [
        [kept_memo], [edited_memo], [kept_memo, edited_memo]
    ]

    saved = client.patch(
        f"/api/meetings/{meeting_id}/agendas/{final['agenda_id']}",
        headers=MINA,
        json={"lines": [
            # ① id 가 왔고 본문이 그대로다 → 계보도 **그대로**
            {"line_id": untouched["line_id"], "text": untouched["text"]},
            # ② id 가 왔고 본문이 달라졌다 → **그 줄의** 계보만 지운다
            {"line_id": to_edit["line_id"], "text": "사람이 고쳐 쓴 줄"},
            # ③ id 없이 왔다 → 새 줄, 계보 없음
            {"text": "사람이 새로 더한 줄"},
            # ④ 모르는 id(그 안건의 줄이 아니다) → **그 줄만** 거절하고 저장 전체를 물리지 않는다
            {"line_id": kept_memo, "text": "다른 벌의 줄 id 를 실었다"},
            # ⑤ `to_drop` 은 목록에 없다 → 지워진 줄. 그 줄과 그 계보가 함께 사라진다
        ]},
    )
    assert saved.status_code == 200, saved.text

    rows = [(row["text"], row["from_lines"]) for row in saved.json()["lines"]]
    assert rows == [
        ("손대지 않을 줄", [kept_memo]),
        ("사람이 고쳐 쓴 줄", []),
        ("사람이 새로 더한 줄", []),
    ]
    assert [row["order"] for row in saved.json()["lines"]] == [1, 2, 3]
    # 같은 안건을 **몇 번 저장해도** 손대지 않은 줄의 계보는 그대로다 — 그것이 F-3 이 막은 자리다.
    again = client.patch(
        f"/api/meetings/{meeting_id}/agendas/{final['agenda_id']}",
        headers=MINA,
        json={"lines": [{"line_id": row["line_id"], "text": row["text"]} for row in saved.json()["lines"]]},
    )
    assert [row["from_lines"] for row in again.json()["lines"]] == [[kept_memo], [], []]
    # 안건 계보는 사람이 안건을 새로 세우거나 지우지 않는 한 그대로다 (§8-9).
    assert _the_final(client, meeting_id)["merged_from"] == [memo_agenda]
    # 원본 벌은 이 저장에 닿지 않는다.
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert [row["line_id"] for row in _track(detail, "memo")[0]["lines"]] == [kept_memo, edited_memo]


def test_the_export_is_html_and_carries_the_last_saved_notes(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)

    exported = client.get(f"/api/meetings/{meeting_id}/export", headers=MINA, params={"format": "html"})
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/html")
    body = exported.text
    assert "합성할 회의" in body and "합성이 낸 줄" in body and "계약서를 검토한다" in body
    # 저장 위치·provider 참조 같은 내부 값은 실리지 않는다.
    assert "storage_key" not in body and "meetings/audio" not in body and "soniox" not in body.lower()
    # 회의록에서 업무로 가는 링크를 두지 않는다 (SPEC §9-6).
    assert "/api/work-requests" not in body and "<a " not in body
    # 표로 늘어놓지 않는다 — 스레드 축 위에 안건이 선다 (§8-10 · D39).
    assert "<table" not in body and 'class="thread"' in body and 'class="thread-item"' in body


def test_an_export_format_the_demo_does_not_do_is_refused(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    for unsupported in ("pdf", "docx"):
        answer = client.get(f"/api/meetings/{meeting_id}/export", headers=MINA, params={"format": unsupported})
        assert answer.status_code == 422, unsupported


def test_someone_outside_the_meeting_cannot_export_or_promote(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]

    assert client.get(f"/api/meetings/{meeting_id}/export", headers=SORA).status_code == 404
    assert client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote", headers=SORA, json={"assignee_id": "sora"}
    ).status_code == 404

    # 참석자는 승격한다 — 팀장도 자기가 앉아 있던 회의의 후속을 넘긴다 (D30).
    assert client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote", headers=JIHO, json={"assignee_id": "jiho"}
    ).status_code == 201


# --------------------------------------------------------------------- 실물 e2e 가 드러낸 것


def test_someone_who_was_not_at_the_meeting_may_still_take_the_work(tmp_path) -> None:
    """**담당 후보에 제한이 없다** (D40) — 보내는 쪽이 시스템이라 조직 경계를 걸 자리가 없다.

    경계는 「누가 누구에게 요청할 수 있는가」의 규칙이고, 그것은 사람이 보낼 때의 규칙이다. 회의에서
    나온 일은 회의가 보낸다 — 회의에 없던 사람에게도, 다른 조직 사람에게도 넘어간다.
    """
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]

    # 소라(법무)는 이 회의에 없었고 민아와 조직도 다르다 — 그래도 받는다.
    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote", headers=MINA, json={"assignee_id": "sora"}
    )
    assert promoted.status_code == 201, promoted.text
    assert promoted.json()["linked"]["work_request_id"]


def test_a_name_that_is_not_a_working_person_is_still_refused(tmp_path) -> None:
    """경계는 걷었지만 **아무 글자나 담당이 되지는 않는다** — 지금 일하고 있는 사람인지는 그대로 본다."""
    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id)["todos"]

    refused = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote",
        headers=MINA,
        json={"assignee_id": "nobody-by-that-name"},
    )
    assert refused.status_code == 422
    assert "eligible assignee" in refused.json()["detail"]


def test_a_spoken_date_survives_as_the_due_candidate(tmp_path) -> None:
    client, application, agent = _stack(tmp_path)
    meeting_id, human = _finalized(
        client, application, agent, todos=[_todo("초안을 낸다", due="2026-09-18")]
    )
    [todo] = _the_final(client, meeting_id)["todos"]
    assert todo["due_candidate"] == "2026-09-18"


def test_the_evidence_a_screen_reads_is_named_the_way_the_contract_names_it(tmp_path) -> None:
    """근거 칩은 `start_ms`·`end_ms` 를 읽는다 — AI 스키마의 내부 이름이 응답으로 새면 시각을 못 읽는다."""
    client, application, agent = _stack(tmp_path)
    meeting_id, human = _finalized(client, application, agent)

    agenda = _the_final(client, meeting_id)
    [line] = [row for row in agenda["lines"] if row["track"] == "final"]
    assert [set(span) for span in line["evidence"]] == [{"start_ms", "end_ms"}]
    assert line["evidence"][0]["start_ms"] == 0 and line["evidence"][0]["end_ms"] == 900


# --------------------------------------------------------------------- D40 · 승격 요청자는 시스템이다


def _promoted_request(client: TestClient, application, agent, *, assignee_id: str = "jiho", headers=MINA) -> dict:
    """후보 하나를 승격시키고 그 업무 요청 상세를 돌려준다."""
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id, headers)["todos"]
    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote", headers=headers, json={"assignee_id": assignee_id}
    )
    assert promoted.status_code == 201, promoted.text
    request_id = promoted.json()["linked"]["work_request_id"]
    detail = client.get(f"/api/work-requests/{request_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()


def test_a_promoted_request_is_sent_by_the_system_and_remembers_who_pressed_it(tmp_path) -> None:
    """승격은 「내가 너에게 부탁한다」가 아니라 「회의에서 이 일이 나왔다」이다 (D40).

    요청자 자리에 누른 사람을 앉히면 회의에서 나온 일이 그 사람의 부탁으로 읽힌다 — 보낸 쪽은 시스템이고
    누른 사람은 참조로 남는다.
    """
    client, application, agent = _stack(tmp_path)
    request = _promoted_request(client, application, agent)

    assert request["requester_id"] == "system:meeting"
    assert request["requester_kind"] == "system"
    assert request["promoted_by_member_id"] == "mina"
    # 누른 사람은 cc 로 들어간다 — 시스템이 보낸 요청이라도 자기가 만든 것은 읽어야 한다.
    assert "mina" in request["cc_member_ids"]
    # 화면이 「회의 · {회의명}」으로 그릴 이름.
    assert request["source_meeting_title"] == "합성할 회의"


def test_a_request_nobody_promoted_still_names_the_person_who_sent_it(tmp_path) -> None:
    """사람이 보내는 기존 경로는 한 글자도 달라지지 않는다."""
    client, application, agent = _stack(tmp_path)
    made = client.post(
        "/api/work-requests", headers=MINA, json={"title": "사람이 보낸 요청", "assignee_id": "jiho"}
    )
    assert made.status_code == 201, made.text
    request = client.get(f"/api/work-requests/{made.json()['request_id']}", headers=MINA).json()

    assert request["requester_id"] == "mina"
    assert request["requester_kind"] == "member"
    assert request["promoted_by_member_id"] is None
    assert request["source_meeting_title"] is None


def test_the_person_who_pressed_promote_may_still_amend_and_withdraw_it(tmp_path) -> None:
    """시스템은 로그인하지 않는다 — 이 자리를 양보하지 않으면 승격된 요청은 **아무도 고칠 수 없는 요청**이 된다."""
    client, application, agent = _stack(tmp_path)
    request = _promoted_request(client, application, agent)
    request_id = request["request_id"]

    amended = client.post(
        f"/api/work-requests/{request_id}/amend",
        headers=MINA,
        json={"title": "누른 사람이 고친 제목", "expected_version": request["version"]},
    )
    assert amended.status_code == 200, amended.text
    assert amended.json()["title"] == "누른 사람이 고친 제목"

    # 상관 없는 사람은 요청자 자리에 서지 못한다 — 자리를 양보한 것은 **누른 사람 하나**다.
    intruder = client.post(
        f"/api/work-requests/{request_id}/amend",
        headers=SORA,
        json={"title": "남이 고친다", "expected_version": amended.json()["version"]},
    )
    assert intruder.status_code in {403, 404, 422}

    # 거두기는 아직 HTTP 표면이 없다 — 같은 판정(`_is_requester`)을 쓰는 명령을 직접 부른다.
    principal = application.authenticated_principal("mina")
    with application._session_factory() as session:
        withdrawn = application._work_requests(session).withdraw(
            principal, UUID(request_id), amended.json()["version"]
        )
        session.commit()
    assert withdrawn["state"] == "withdrawn"


def test_the_first_line_of_the_history_says_which_meeting_it_came_from(tmp_path) -> None:
    """「시스템이 보냄」으로 끝내면 사람이 왜 이 요청을 받았는지 읽을 수 없다 (D40)."""
    client, application, agent = _stack(tmp_path)
    request = _promoted_request(client, application, agent)

    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers=MINA)
    assert timeline.status_code == 200, timeline.text
    [created] = [row for row in timeline.json()["activity"] if row["event_kind"] == "work_request.created"]
    assert created["safe_summary"].startswith("회의 합성할 회의에서 민아이 업무 요청을 만들었다")
    # 행위자는 **누른 사람**이다 — 시스템이 아니다.
    assert created["actor_id"] == "mina"


def test_accepting_a_promoted_request_still_carries_the_meeting_onto_the_task(tmp_path) -> None:
    """요청자가 시스템으로 바뀌어도 수락과 출처 복사는 그대로다 (SPEC-004 §9-7)."""
    client, application, agent = _stack(tmp_path)
    request = _promoted_request(client, application, agent)

    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept", headers=JIHO, json={"expected_version": request["version"]}
    )
    assert accepted.status_code == 200, accepted.text

    from sqlalchemy import select

    from ax_workspace.platform.persistence import TaskRecord

    with application._session_factory() as session:
        task = session.scalar(
            select(TaskRecord).where(TaskRecord.source_work_request_id == UUID(request["request_id"]))
        )
    assert task.source_meeting_id is not None and task.source_agenda_id is not None
    assert task.origin_kind == "meeting"


# --------------------------------------------------------------------- D39 · 주제 스레드형 조판


def _exported(client: TestClient, meeting_id: str) -> str:
    answer = client.get(f"/api/meetings/{meeting_id}/export", headers=MINA, params={"format": "html"})
    assert answer.status_code == 200, answer.text
    return answer.text


def test_the_graph_hangs_a_promoted_request_on_its_meeting_rather_than_on_a_person_who_is_not_one(tmp_path) -> None:
    """승격 요청의 요청자는 `system:meeting` 이고 그것은 사람 명부에 없는 id 다 (D40 미결 ④).

    그대로 사람 점으로 그리면 그래프에 **사람이 아닌 사람**이 하나 서고 이름 자리에 id 가 나온다.
    보낸 쪽이 시스템이면 사람 점을 만들지 않고 그 요청이 나온 회의에 잇는다.
    """
    client, application, agent = _stack(tmp_path)
    request = _promoted_request(client, application, agent)

    graph = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"work_request:{request['request_id']}"})
    assert graph.status_code == 200, graph.text
    nodes = graph.json()["nodes"]

    assert not [node for node in nodes if node["kind"] == "person" and "system:" in node["id"]]
    assert not any("system:meeting" in str(node) for node in nodes)
    # 대신 그 요청이 나온 회의가 서고, 둘이 이어져 있다.
    [meeting] = [node for node in nodes if node["kind"] == "meeting"]
    assert meeting["title"] == "합성할 회의"
    assert any(
        edge["from"] == f"meeting:{meeting['id']}" and edge["to"] == f"work_request:{request['request_id']}"
        for edge in graph.json()["edges"]
    )


def test_the_graph_still_draws_the_person_who_sent_an_ordinary_request(tmp_path) -> None:
    """사람이 보낸 요청은 그대로 사람 점에 걸린다 — 바뀐 것은 시스템 행위자 하나뿐이다."""
    client, _, _ = _stack(tmp_path)
    made = client.post("/api/work-requests", headers=MINA, json={"title": "사람이 보낸 요청", "assignee_id": "jiho"})
    assert made.status_code == 201, made.text
    request_id = made.json()["request_id"]

    graph = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"work_request:{request_id}"}).json()
    assert any(edge["from"] == "person:mina" and edge["to"] == f"work_request:{request_id}" for edge in graph["edges"])


# ------------------------------------------- 통합 회의록 · 재료로 처음부터 새로 쓴다 (2026-09-11)


def test_a_meeting_opened_with_no_agenda_finalizes_on_what_the_batch_built(tmp_path) -> None:
    """**사람 벌이 비어도 합성은 돈다 — 남은 재료로 짓는다** (SPEC §8-3 · W-1).

    사람이 한 줄도 안 적는 회의는 흔하다. 비었다는 이유로 사람에게 묻지 않고(D52), 남은 한 벌을 최종
    벌로 그대로 옮기지도 않는다(§8-5) — 최종 벌은 언제나 **새로** 짓는다.
    """
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application, agendas=())
    meeting_id = made["meeting"]["meeting_id"]
    assert made["agendas"] == []
    _blocks(application, meeting_id)
    _ai_agenda_with_lines(application, meeting_id, "배치가 세운 안건", ["배치가 낸 줄"])
    [built] = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
    # AI 벌의 안건이다 — 출처는 갖지 않는다 (§4.1-2).
    assert built["track"] == "ai" and built["source"] is None

    agent.script = [
        _output([
            _agenda("배치가 세운 안건", merged_from=[built["agenda_id"]], lines=[_line("합성이 낸 줄")])
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "done"
    assert detail["meeting"]["failure_reason"] is None
    # **최종 벌은 새로 선 안건이다** — AI 벌의 안건을 이어 쓰지 않고 계보로 가리킨다 (§8-5 · §4.1-3).
    [final] = _track(detail, "final")
    assert final["agenda_id"] != built["agenda_id"]
    assert final["merged_from"] == [built["agenda_id"]]
    assert [row["text"] for row in final["lines"]] == ["합성이 낸 줄"]
    # AI 벌은 그대로다 — 무엇을 보고 썼는지가 지워지지 않는다 (D53).
    assert [row["text"] for row in _track(detail, "ai")[0]["lines"]] == ["배치가 낸 줄"]
    # 사람 벌은 빈 채로 남는다 — 없는 것을 지어내지 않는다.
    assert _track(detail, "memo") == []
    assert len(agent.runs) == 1


def test_a_merge_may_stand_up_agendas_nobody_asked_for(tmp_path) -> None:
    """안건 목록도 AI 가 다시 잡는다 — 사람이 적지 않은 안건을 새로 세워도 된다."""
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)

    agent.script = [
        _output([
            _agenda("첫 안건", merged_from=[human], lines=[_line("이어 쓴 줄")]),
            _agenda("AI 가 새로 세운 안건", lines=[_line("새로 쓴 줄")]),
        ])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    finals = _track(detail, "final")
    assert [row["title"] for row in finals] == ["첫 안건", "AI 가 새로 세운 안건"]
    # 최종 벌의 안건은 **출처를 갖지 않는다** — 출처는 사람 벌 안의 값이다 (§4.1-2).
    assert [row["source"] for row in finals] == [None, None]
    # 계보가 어느 원본에서 나왔는지를 가른다 — 두 번째 것은 전사에만 있던 이야기라 비어 있다 (§4.1-3).
    assert [row["merged_from"] for row in finals] == [[human], []]
    # 순서는 **벌 안에서** 1 부터 다시 매겨진다 (§4.0-1).
    assert [row["order"] for row in finals] == [1, 2]
    # 사람 벌의 안건은 그대로 하나다.
    assert [row["title"] for row in _track(detail, "memo")] == ["첫 안건"]


def test_a_failure_reason_is_one_sentence_a_person_reads_and_carries_no_identifier(tmp_path) -> None:
    """「기대 [] · 실제 [uuid]」가 사람 화면에 뜨는 일이 실제로 있었다 — 사유는 사람 말 한 줄이다."""
    import re

    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    _blocks(application, meeting_id)
    # 스키마를 어긴 출력 — 시도 상한까지 같은 답이 온다.
    agent.script = ["{\"title_candidate\": null}"] * 3

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is False

    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["status"] == "failed"
    reason = head["failure_reason"]
    assert reason == "회의록을 만들지 못했습니다 — 출력 형식이 맞지 않았습니다"
    # uuid 도, 대괄호 목록도, 스키마 낱말도 사람 화면에 나오지 않는다.
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", reason) is None
    assert "[" not in reason and "agenda" not in reason.lower()


def test_a_failed_merge_can_be_retried_and_lands_under_the_new_rule(tmp_path) -> None:
    """[다시 시도] 는 새 규칙으로 돈다 — 예전 검사 때문에 실패한 회의가 그대로 살아난다."""
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application, agendas=())
    meeting_id = made["meeting"]["meeting_id"]
    _blocks(application, meeting_id)
    _ai_agenda_with_lines(application, meeting_id, "배치가 세운 안건", ["배치가 낸 줄"])
    [built] = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]

    agent.script = [RuntimeError("대역: 한 번 깨진다")] * 3
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is False
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "failed"

    agent.script = [
        _output([
            _agenda("배치가 세운 안건", merged_from=[built["agenda_id"]], lines=[_line("다시 시도로 실린 줄")])
        ])
    ]
    retried = client.post(f"/api/meetings/{meeting_id}/finalize", headers=MINA)
    assert retried.status_code == 200, retried.text
    assert application.finalize_meeting(UUID(meeting_id)) is True

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["meeting"]["status"] == "done"
    assert detail["meeting"]["failure_reason"] is None
    [final] = _track(detail, "final")
    assert [row["text"] for row in final["lines"]] == ["다시 시도로 실린 줄"]
    # 원본 AI 벌은 재시도에 닿지 않는다 — 몇 번을 다시 시도해도 바뀌지 않는다 (§8-8 · D53).
    assert [row["text"] for row in _track(detail, "ai")[0]["lines"]] == ["배치가 낸 줄"]


# --------------------------------------------------------------------- D44 · 종료 뒤 2-pass 재전사


class FakeFileTranscriber:
    """음원 하나를 통째로 전사하는 대역. **실제 Soniox 는 부르지 않는다.**"""

    def __init__(self, tokens=None, error: Exception | None = None) -> None:
        self.tokens = tokens or []
        self.error = error
        self.calls: list = []

    def transcribe(self, recording):
        self.calls.append(recording)
        if self.error is not None:
            raise self.error
        return list(self.tokens)


def _final_token(text: str, *, speaker: str, start_ms: int, end_ms: int):
    from ax_workspace.modules.meetings.stream import SttToken

    return SttToken(text=text, is_final=True, speaker=speaker, start_ms=start_ms, end_ms=end_ms)


def _with_recording(application, meeting_id: str, *, data: bytes = b"webm-bytes", started_late_ms: int = 0) -> None:
    """중계가 남긴 음원 한 벌을 세운다 — 실제 스트림 없이 재전사 입력만 만든다.

    `started_late_ms` 는 회의가 열리고 **얼마 뒤에** 녹음이 시작됐는가다. 기본은 0 — 시험이 그 차이를
    직접 정하지 않으면 파일의 0초가 회의의 0초다.
    """
    from datetime import timedelta

    from ax_workspace.platform.persistence import MeetingRecordingFileRecord, MeetingRecord

    key = application._recording_storage.append(meeting_id, data, extension="webm")
    with application._session_factory() as session:
        application._meetings(session)._repository.record_recording_file(
            UUID(meeting_id), storage_key=key, content_type="audio/webm"
        )
        session.flush()
        meeting = session.get(MeetingRecord, UUID(meeting_id))
        row = session.query(MeetingRecordingFileRecord).one()
        row.started_at = meeting.started_at + timedelta(milliseconds=started_late_ms)
        session.commit()
    application.meeting_finalize._transcriber = _EchoTranscriber(application, meeting_id)


def _transcript_of(client: TestClient, meeting_id: str) -> list[dict]:
    return client.get(f"/api/meetings/{meeting_id}/transcript", headers=MINA).json()["items"]


def test_ending_a_meeting_transcribes_the_whole_recording_again_and_replaces_the_script(tmp_path) -> None:
    """실시간 전사는 화자 분리가 부정확하다 — 끝난 뒤 음원 전체를 한 번 더 듣고 원문을 갈아 끼운다 (D44)."""
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)  # 실시간이 남긴 원문 2블록
    assert [row["content"] for row in _transcript_of(client, meeting_id)] == ["확정 발화 0", "확정 발화 1"]
    _with_recording(application, meeting_id)

    transcriber = FakeFileTranscriber([
        _final_token("다시 들으니 이렇게 말했다.", speaker="1", start_ms=0, end_ms=900),
        _final_token("두 번째 사람이 답했다.", speaker="2", start_ms=1_000, end_ms=1_800),
    ])
    application.meeting_finalize._transcriber = transcriber
    agent.script = [_output([_agenda("첫 안건", merged_from=[human], lines=[_line("합성 줄")])])]

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    # 원문이 통째로 갈렸다 — 화자도 새로 갈렸다.
    items = _transcript_of(client, meeting_id)
    assert [row["content"] for row in items] == ["다시 들으니 이렇게 말했다.", "두 번째 사람이 답했다."]
    assert [row["speakerLabel"] for row in items] == ["1", "2"]
    # `at_ms` 도 새 원문의 것이다 — 근거 타임칩이 이 값에 걸린다.
    assert [row["atMs"] for row in items] == [0, 1_000]
    assert application.meeting_finalize.retranscribes == 1

    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["status"] == "done" and head["transcript_source"] == "final"


def test_the_merge_reads_the_script_the_second_pass_wrote(tmp_path) -> None:
    """②가 읽는 원문은 ①이 새로 쓴 것이다 — 순서가 뒤집히면 합성이 옛 원문을 본다."""
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    _with_recording(application, meeting_id)
    # 세션이 없으면 콜드 스타트라 확정 발화 전량이 프롬프트에 실린다 — 무엇을 읽었는지 그 자리에서 본다.
    with application._session_factory() as session:
        from sqlalchemy import delete

        from ax_workspace.platform.persistence import MeetingAiSessionRecord

        session.execute(delete(MeetingAiSessionRecord).where(MeetingAiSessionRecord.meeting_id == UUID(meeting_id)))
        session.commit()

    application.meeting_finalize._transcriber = FakeFileTranscriber([
        _final_token("재전사만 아는 문장.", speaker="1", start_ms=0, end_ms=900),
    ])
    agent.script = [_output([_agenda("첫 안건", merged_from=[human])])]

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    prompt = agent.runs[0]["prompt"]
    assert "재전사만 아는 문장." in prompt
    assert "확정 발화 0" not in prompt


def test_retrying_a_failed_meeting_starts_from_the_second_pass_again(tmp_path) -> None:
    """[다시 시도] 는 재전사부터 돈다 — 처음 실패한 걸음이 그 걸음이기 때문이다."""
    from ax_workspace.modules.meetings.stream import SttUpstreamError

    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    _with_recording(application, meeting_id)

    broken = FakeFileTranscriber(error=SttUpstreamError("대역: 한 번 깨진다"))
    application.meeting_finalize._transcriber = broken
    agent.script = [_output([_agenda("첫 안건", merged_from=[human])])] * 3
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is False
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["status"] == "failed"
    assert agent.runs == []

    healthy = FakeFileTranscriber([_final_token("다시 들으니 들렸다.", speaker="1", start_ms=0, end_ms=900)])
    application.meeting_finalize._transcriber = healthy
    agent.script = [_output([_agenda("첫 안건", merged_from=[human], lines=[_line("합성 줄")])])]

    retried = client.post(f"/api/meetings/{meeting_id}/finalize", headers=MINA)
    assert retried.status_code == 200, retried.text
    assert application.finalize_meeting(UUID(meeting_id)) is True

    # 재전사부터 다시 돌았다 — 원문이 갈렸고 그 위에서 합성이 섰다.
    assert healthy.calls, "재전사를 다시 부르지 않았다"
    assert [row["content"] for row in _transcript_of(client, meeting_id)] == ["다시 들으니 들렸다."]
    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["status"] == "done" and head["transcript_source"] == "final"
    assert head["failure_reason"] is None


def test_a_recording_that_started_after_the_meeting_is_shifted_back_onto_the_meeting_clock(tmp_path) -> None:
    """파일의 0초는 회의의 0초가 아니다 — 근거 타임칩이 회의 시작 기준에 걸려 있어 그만큼 되돌린다."""
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)
    # 회의가 열리고 7초 뒤에 녹음이 붙었다.
    _with_recording(application, meeting_id, started_late_ms=7_000)

    application.meeting_finalize._transcriber = FakeFileTranscriber([
        _final_token("녹음 시작 직후의 말.", speaker="1", start_ms=0, end_ms=900),
    ])
    agent.script = [_output([_agenda("첫 안건", merged_from=[human])])]

    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    [item] = _transcript_of(client, meeting_id)
    assert item["atMs"] == 7_000 and item["endMs"] == 7_900


# ------------------- 결과를 받다 끊기면 같은 전사 건으로 결과만 다시 받는다 (D44 정정 ④)


def test_the_final_merge_clears_the_candidates_the_meeting_was_still_making(tmp_path) -> None:
    """회의 중 후보는 최종이 시작할 때 끝난다 (D46).

    남겨 두면 같은 일이 후보로 두 번 서고, 그중 하나는 아무도 승격할 수 없는 읽기 전용이다.
    """
    client, application, agent = _stack(tmp_path)
    made = _summarizing(client, application)
    meeting_id = made["meeting"]["meeting_id"]
    human = made["agendas"][0]["agenda_id"]
    _blocks(application, meeting_id)

    # 회의 중 배치가 후보를 남겨 둔 상태를 만든다. **잠정 후보는 AI 벌의 안건에 매달린다** (§4.0-6).
    _ai_agenda_with_lines(application, meeting_id, "배치가 세운 안건", ["배치가 낸 줄"])
    ai_agenda = next(
        row["agenda_id"]
        for row in client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"]
        if row["track"] == "ai"
    )
    with application._session_factory() as session:
        meetings = application._meetings(session)
        meeting = meetings._repository.meeting(UUID(meeting_id))
        meetings._repository.replace_provisional_todos(meeting, [{
            "agenda_id": UUID(ai_agenda), "order_index": 1, "title": "회의 중 후보",
            "description": "", "due_candidate": None, "checklist_candidate": [],
            "reference": {"meeting_id": meeting_id, "agenda_id": ai_agenda, "line_ids": []},
        }])
        session.commit()
    [standing] = _track(client.get(f"/api/meetings/{meeting_id}", headers=MINA).json(), "ai")[0]["todos"]
    assert standing["provisional"] is True

    agent.script = [
        _output([_agenda("첫 안건", merged_from=[human], todos=[_todo("최종이 낸 후보")])])
    ]
    client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
    assert application.finalize_meeting(UUID(meeting_id)) is True

    agenda = _the_final(client, meeting_id)
    assert [row["title"] for row in agenda["todos"]] == ["최종이 낸 후보"]
    # 최종이 낸 것은 확정 후보다 — 승격도 삭제도 받는다.
    assert agenda["todos"][0]["provisional"] is False
    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{agenda['todos'][0]['todo_id']}/promote",
        headers=MINA,
        json={"assignee_id": "jiho"},
    )
    assert promoted.status_code == 201, promoted.text
