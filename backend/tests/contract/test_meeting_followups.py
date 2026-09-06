"""회의에서 나온 일은 회의에 남지 않는다.

A summary can say "누가 무엇을 하기로 했다", but that sentence is not work. Someone has to decide to make it work,
and when they do it becomes an ordinary Task or WorkRequest — with the meeting kept as the context it came from, and
the summary statement it came from named. Nothing is created behind anyone's back, and a candidate is promoted once.
"""
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


class FollowupProvider:
    """Test injection only: a refinement pass and a summary whose statements include a followup candidate."""

    def generate(self, request) -> "AiGeneration":
        from ax_workspace.modules.ax_execution.ai import AiGeneration

        if "statements" in request.output_schema["properties"]:
            body = (
                '{"body":"## 논의 요약\\n계약서 검토를 진행하기로 했다.","statements":['
                '{"kind":"decision","text":"계약서를 이번 주에 본다",'
                '"refinement_start_sequence":1,"refinement_end_sequence":1},'
                '{"kind":"followup","text":"지호가 계약서를 검토한다",'
                '"refinement_start_sequence":1,"refinement_end_sequence":1}]}'
            )
        else:
            body = (
                '{"segments":[{"raw_start_source_key":"seg-1","raw_end_source_key":"seg-1",'
                '"start_ms":0,"end_ms":4000,"text":"지호가 계약서를 검토하기로 했습니다.",'
                '"speaker_label":null,"correction_kind":"none","confidence":0.95}]}'
            )
        return AiGeneration(
            provider_run_ref="codex-run-followup-test",
            provider_session_ref=None,
            body=body,
            requested_model="test",
            observed_model="test",
            requested_tier="test",
            observed_tier=None,
            latency_ms=1,
            usage=None,
        )


def _stack_with_summary(tmp_path, title: str = "후속이 나온 회의"):
    """A meeting whose summary carries a followup candidate — built through the real pipeline, not hand-written rows."""
    from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"), recordings_dir=str(tmp_path / "recordings")
    )
    app = create_app(settings, report_provider=FollowupProvider())
    client = TestClient(app)
    application = app.state.workflow_application

    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": title,
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    ).json()
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start", headers=MINA, json={"purpose": "후속 업무 테스트"}
    ).json()
    stopped = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=MINA,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    assert stopped.status_code == 200, stopped.text
    raw = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="test",
        provider_reference="async:followup",
        segments=[FinalTranscriptSegment("seg-1", 0, 4_000, "지호가 계약서를 검토하기로 했습니다.", "Speaker 1")],
    )
    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    summary = application.summarize_meeting_transcript(UUID(refined["refinement_revision_id"]))
    return client, {"meeting": meeting, "summary_id": summary["summary_id"]}


def test_a_followup_someone_decided_to_act_on_becomes_ordinary_work(tmp_path) -> None:
    client, made = _stack_with_summary(tmp_path)
    meeting_id = made["meeting"]["meeting_id"]

    # The candidate is visible as a candidate, and nothing exists yet.
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    [candidate] = [row for row in detail["summaries"][0]["statements"] if row["kind"] == "followup"]
    assert candidate["text"] == "지호가 계약서를 검토한다"
    assert candidate["promoted"] is False
    assert client.get("/api/my-work", headers=MINA).json() == []

    promoted = client.post(
        f"/api/meetings/{meeting_id}/summaries/{made['summary_id']}/statements/{candidate['statement_index']}/promote",
        headers=MINA,
        json={"kind": "task", "title": "계약서 검토"},
    )
    assert promoted.status_code == 201, promoted.text
    task_id = promoted.json()["task"]["task_id"]

    # It is an ordinary Task, and it keeps the meeting it came from as context.
    task = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    assert task["title"] == "계약서 검토" and task["state"] == "open"
    [material] = client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()
    assert material["source_kind"] == "resource_ref" and material["resource"]["type"] == "meeting"
    assert material["resource"]["id"] == meeting_id

    # The candidate says it was acted on, so nobody promotes it twice.
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    [candidate] = [row for row in detail["summaries"][0]["statements"] if row["kind"] == "followup"]
    assert candidate["promoted"] is True and candidate["promoted_task_id"] == task_id
    again = client.post(
        f"/api/meetings/{meeting_id}/summaries/{made['summary_id']}/statements/{candidate['statement_index']}/promote",
        headers=MINA,
        json={"kind": "task", "title": "계약서 검토"},
    )
    assert again.status_code == 200 and again.json()["task"]["task_id"] == task_id  # a receipt, not a second Task


def test_asking_someone_else_goes_through_the_request_ledger(tmp_path) -> None:
    client, made = _stack_with_summary(tmp_path, "부탁이 나온 회의")
    meeting_id = made["meeting"]["meeting_id"]

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    [candidate] = [row for row in detail["summaries"][0]["statements"] if row["kind"] == "followup"]
    asked = client.post(
        f"/api/meetings/{meeting_id}/summaries/{made['summary_id']}/statements/{candidate['statement_index']}/promote",
        headers=MINA,
        json={"kind": "work_request", "title": "계약서 검토", "assignee_id": "jiho"},
    )
    assert asked.status_code == 201, asked.text
    request_id = asked.json()["work_request"]["request_id"]

    # It is an ordinary request: the person asked judges it, and only then is there work.
    [item] = [row for row in client.get("/api/action-items", headers=JIHO).json() if row["subject"] == "계약서 검토"]
    assert item["kind"] == "work_request.acceptance"
    assert client.get(f"/api/work-requests/{request_id}", headers=MINA).json()["state"] == "pending"


def test_asking_nobody_is_refused_in_words_rather_than_breaking(tmp_path) -> None:
    """말이 되지 않는 요청은 거절이지 고장이 아니다."""
    client, made = _stack_with_summary(tmp_path, "부탁할 사람이 빠진 회의")
    meeting_id = made["meeting"]["meeting_id"]
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    [candidate] = [row for row in detail["summaries"][0]["statements"] if row["kind"] == "followup"]
    path = f"/api/meetings/{meeting_id}/summaries/{made['summary_id']}/statements/{candidate['statement_index']}/promote"

    nobody = client.post(path, headers=MINA, json={"kind": "work_request", "title": "계약서 검토"})
    assert nobody.status_code == 422, nobody.text
    assert "담당 후보" in nobody.json()["detail"]

    neither = client.post(path, headers=MINA, json={"kind": "report", "title": "계약서 검토"})
    assert neither.status_code == 422, neither.text

    # 거절된 뒤에도 원래 길은 그대로 열려 있다.
    assert client.post(path, headers=MINA, json={"kind": "task", "title": "계약서 검토"}).status_code == 201


def test_only_someone_who_may_read_the_meeting_may_act_on_it(tmp_path) -> None:
    client, made = _stack_with_summary(tmp_path, "권한을 보는 회의")
    meeting_id = made["meeting"]["meeting_id"]

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    statements = detail["summaries"][0]["statements"]
    [candidate] = [row for row in statements if row["kind"] == "followup"]
    [decision] = [row for row in statements if row["kind"] == "decision"]
    refused = client.post(
        f"/api/meetings/{meeting_id}/summaries/{made['summary_id']}/statements/{candidate['statement_index']}/promote",
        headers=SORA,
        json={"kind": "task", "title": "몰래 만든 업무"},
    )
    assert refused.status_code in {403, 404}
    assert "계약서" not in refused.text
    assert client.get("/api/my-work", headers=SORA).json() == []

    # A statement that is not a followup is not a candidate to act on.
    not_a_candidate = client.post(
        f"/api/meetings/{meeting_id}/summaries/{made['summary_id']}/statements/{decision['statement_index']}/promote",
        headers=MINA,
        json={"kind": "task", "title": "결정문으로 만든 업무"},
    )
    assert not_a_candidate.status_code == 422
