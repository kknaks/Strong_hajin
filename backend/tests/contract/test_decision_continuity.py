"""ERD decision continuity: RequestThread, Subject versions, Submissions, ReviewAssignments, ReviewDecisions, Task lineage.

**판단 회차는 W1 이후 과거 행에만 있다** — 신규 요청은 `assigned` 로 서고 그 회차를 만들지 않는다
(WORK-001 Phase 4). 회차의 연속성을 다루는 코드와 데이터는 그대로이므로, 예전 배포가 남긴 모양에서 본다.
신규 요청이 세우는 것(스레드 · Subject 판 · 출처 · 활성 담당)은 그대로 여기서도 본다.
"""
from fastapi.testclient import TestClient
from legacy_acceptance import make_request_look_pending, pending_request

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    client.database_url = database_url
    return client


def test_a_new_request_opens_a_thread_and_a_task_but_no_judgement_round(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/work-requests", headers=MINA, json={"title": "검토 요청", "assignee_id": "jiho", "due_date": "2026-09-12"}).json()
    # v2: 발송이 업무를 세우고 **받는 사람에게 던지는 질문 하나**를 함께 연다 (SPEC-002 판단 계약).
    # W1 은 이 회차를 비웠다 — 판단 없이 담당이 섰으니 물을 것이 없었다. v2 는 답해야 하므로 회차가 있다.
    assert created["request_thread_id"] and created["submission_version"] == 1
    assert created["state"] == "pending" and created["task_id"]
    timeline = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=JIHO).json()
    assert timeline["decision_item"] is not None and len(timeline["submissions"]) == 1
    # 회차는 열려 있고 **답은 아직 없다** — 받는 사람 하나가 답할 사람으로 서 있다.
    assert [row["reviewer_member_id"] for row in timeline["review_assignments"]] == ["jiho"]
    assert timeline["review_decisions"] == []
    assert any(e["event_kind"] == "work_request.created" for e in timeline["activity"])


def test_a_past_pending_request_keeps_its_round_and_its_reviewer(tmp_path) -> None:
    """과거 행은 그대로 읽힌다 — 회차·판단자·감사 줄이 지워지지 않는다."""
    client = _client(tmp_path)
    created = pending_request(client, client.database_url, MINA, title="검토 요청", assignee_id="jiho", due_date="2026-09-12")
    timeline = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=JIHO).json()
    assert timeline["decision_item"]["kind"] == "work_request.acceptance" and timeline["decision_item"]["status"] == "open"
    assert [s["submission_version"] for s in timeline["submissions"]] == [1]
    assert timeline["submissions"][0]["snapshot"]["title"] == "검토 요청"
    assert timeline["review_assignments"][0]["reviewer_member_id"] == "jiho"
    assert timeline["review_assignments"][0]["status"] == "pending"
    assert any(e["event_kind"] == "work_request.created" for e in timeline["activity"])


def test_negotiate_then_resubmit_keeps_prior_decision_and_shows_diff(tmp_path) -> None:
    client = _client(tmp_path)
    created = pending_request(client, client.database_url, MINA, title="검토 요청", assignee_id="jiho", due_date="2026-09-12")
    negotiated = client.post(
        f"/api/work-requests/{created['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": created["version"], "conditions": {"note": "9월 15일까지면 가능"}},
    ).json()
    assert negotiated["state"] == "negotiating"

    # 요청자만, 협의 중일 때만 재상신할 수 있다.
    assert client.post(f"/api/work-requests/{created['request_id']}/resubmit", headers=JIHO, json={"expected_version": negotiated["version"], "due_date": "2026-09-15"}).status_code in {403, 422}
    resubmitted = client.post(
        f"/api/work-requests/{created['request_id']}/resubmit",
        headers=MINA,
        json={"expected_version": negotiated["version"], "due_date": "2026-09-15"},
    )
    assert resubmitted.status_code == 200, resubmitted.text
    body = resubmitted.json()
    assert body["state"] == "pending" and body["due_date"] == "2026-09-15" and body["submission_version"] == 2

    timeline = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=MINA).json()
    assert [s["submission_version"] for s in timeline["submissions"]] == [1, 2]
    assert timeline["submissions"][1]["revises_id"] == timeline["submissions"][0]["submission_id"]
    assert timeline["submissions"][1]["diff"] == {"due_date": {"before": "2026-09-12", "after": "2026-09-15"}}
    decisions = timeline["review_decisions"]
    assert len(decisions) == 1 and decisions[0]["decision"] == "negotiate" and decisions[0]["reason"] == "9월 15일까지면 가능"
    statuses = [a["status"] for a in timeline["review_assignments"]]
    assert statuses == ["decided", "pending"]
    assert timeline["decision_item"]["status"] == "open"

    accepted = client.post(f"/api/work-requests/{created['request_id']}/accept", headers=JIHO, json={"expected_version": body["version"]}).json()
    task = client.get(f"/api/tasks/{accepted['task_id']}", headers=JIHO).json()
    assert task["origin_kind"] == "request_effect"
    assert task["organization_unit_id"] == "product"
    assert task["lineage"]["source_work_request_id"] == created["request_id"]
    assert task["lineage"]["request_thread_id"] == created["request_thread_id"]
    assert task["lineage"]["source_submission_id"] == timeline["submissions"][1]["submission_id"]
    assert task["lineage"]["source_review_decision_id"]
    final = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=MINA).json()
    assert [d["decision"] for d in final["review_decisions"]] == ["negotiate", "accept"]
    assert final["decision_item"]["status"] == "resolved"


def test_direct_task_records_attribution_and_activity(tmp_path) -> None:
    client = _client(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "직접 업무"}).json()
    assert task["origin_kind"] == "direct" and task["organization_unit_id"] == "product" and task["visibility"] == "scope_default"
    started = client.post(f"/api/tasks/{task['task_id']}/start", headers=MINA, json={"expected_version": 1}).json()
    assert started["state"] == "in_progress"


def test_comments_live_on_the_thread_without_changing_state(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/work-requests", headers=MINA, json={"title": "논의 요청", "assignee_id": "jiho"}).json()
    comment = client.post(f"/api/work-requests/{created['request_id']}/comments", headers=JIHO, json={"body": "승인할게요"})
    assert comment.status_code == 201 and comment.json()["author_member_id"] == "jiho"
    assert client.post(f"/api/work-requests/{created['request_id']}/comments", headers=MINA, json={"body": "  "}).status_code == 422
    timeline = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=MINA).json()
    assert [c["body"] for c in timeline["comments"]] == ["승인할게요"]
    # 댓글은 상태를 바꾸지 않는다 — 판단이 아니기 때문이다.
    assert timeline["request"]["state"] == "pending" and timeline["review_decisions"] == []
