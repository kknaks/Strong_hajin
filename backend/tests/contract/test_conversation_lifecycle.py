"""Turn lifecycle projection: streaming assistant text, user-facing progress, retry lineage, canonical commands."""
import asyncio
from datetime import UTC, datetime
from threading import Event
import time
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.conversation_worker import ConversationWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiConversationResult, AiFollowUpCandidate, AiProviderEvent, AiToolInvocation, ProviderCancelled, ProviderRequestFailed
from ax_workspace.platform.conversation_jobs import ConversationJobQueue

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


class ScriptedProvider:
    """Emits observed events through the sink like the Codex adapter would, then completes or fails."""

    def __init__(self, *, fail=False, cancel_midway: Event | None = None, release: Event | None = None, candidates=None) -> None:
        self.fail = fail
        self.cancel_midway = cancel_midway
        self.release = release
        self.candidates = candidates or []
        self.calls = 0

    def converse(self, request, *, sink=None, cancel=None):
        self.calls += 1
        now = datetime.now(UTC)
        sink.accept(AiProviderEvent("turn_started", now, provider_run_ref="run-1", provider_session_ref="thread-1"))
        sink.accept(AiProviderEvent("item_completed", now, item_id="m1", item_type="agent_message", text="먼저 업무를 조회합니다."))
        tool = AiToolInvocation("call-1", "task_list", "task list", "입력 없음", "running", None, None, None, started_at=now)
        sink.accept(AiProviderEvent("item_started", now, item_id="call-1", item_type="mcp_tool_call", tool=tool))
        if self.release is not None:
            self.release.wait(timeout=10)
        if cancel is not None and cancel.is_set():
            raise ProviderCancelled("cancelled by user")
        done = AiToolInvocation("call-1", "task_list", "task list", "입력 없음", "completed", "결과: 3건 조회", None, 12, started_at=now, completed_at=now)
        sink.accept(AiProviderEvent("item_completed", now, item_id="call-1", item_type="mcp_tool_call", tool=done))
        if self.fail:
            raise ProviderRequestFailed("provider failed after partial output")
        sink.accept(AiProviderEvent("item_completed", now, item_id="m2", item_type="agent_message", text="내 업무는 3개입니다."))
        sink.accept(AiProviderEvent("turn_completed", now, usage={"input_tokens": 5, "output_tokens": 2}))
        return AiConversationResult(
            "run-1",
            "thread-1",
            "내 업무는 3개입니다.",
            [done],
            usage={"input_tokens": 5, "output_tokens": 2},
            follow_up_candidates=self.candidates,
        )


def _stack(tmp_path, provider):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings, report_provider=provider)
    client = TestClient(app)
    application = app.state.workflow_application
    worker = ConversationWorker(settings, provider=provider, queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue, worker_id="test"))
    return client, worker


def _send(client, headers, body: str, key: str):
    conversation = client.post("/api/conversations", headers=headers, json={"title": "lifecycle"}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, "Idempotency-Key": key}, json={"body": body, "context": []})
    assert accepted.status_code == 202, accepted.text
    return conversation["conversation_id"], accepted.json()["turn_id"]


def test_completed_turn_projects_progress_timings_streamed_text_and_final_body(tmp_path) -> None:
    client, worker = _stack(tmp_path, ScriptedProvider())
    conversation_id, turn_id = _send(client, MINA, "내 업무 수를 알려줘", "k-1")
    before = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    [turn] = before["turns"]
    assert turn["progress_state"] == "queued" and turn["queue_wait_ms"] is None and turn["run_ms"] is None
    user = [m for m in before["messages"] if m["role"] == "user"][0]
    assert user["idempotency_key"] == "k-1" and user["body_state"] == "final"

    assert asyncio.run(worker.run_once()) is True
    after = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    [turn] = after["turns"]
    assert turn["state"] == "completed" and turn["progress_state"] == "completed" and turn["current_tool_display_name"] is None
    assert turn["queue_wait_ms"] is not None and turn["run_ms"] is not None and turn["usage"] == {"input_tokens": 5, "output_tokens": 2}
    assistant = [m for m in after["messages"] if m["role"] == "assistant"]
    assert len(assistant) == 1 and assistant[0]["body_state"] == "final" and assistant[0]["body"] == "내 업무는 3개입니다."
    [tool] = after["tool_invocations"]
    assert tool["state"] == "completed" and tool["started_at"] and tool["completed_at"] and tool["latency_ms"] == 12


def test_completed_turn_projects_only_normalized_distinct_follow_up_candidates(tmp_path) -> None:
    provider = ScriptedProvider(
        candidates=[
            AiFollowUpCandidate(" 기한순으로 보기 ", " 그 업무를 기한순으로 정리해줘 "),
            AiFollowUpCandidate("다른 표현", "그   업무를 기한순으로 정리해줘"),
            AiFollowUpCandidate("", "빈 label은 제외해줘"),
            AiFollowUpCandidate("우선순위 제안", "먼저 할 업무를 제안해줘"),
            AiFollowUpCandidate("담당자별 정리", "담당자별로 묶어줘"),
            AiFollowUpCandidate("네 번째", "이 후보는 상한 밖이야"),
        ]
    )
    client, worker = _stack(tmp_path, provider)
    conversation_id, turn_id = _send(client, MINA, "내 업무 수를 알려줘", "follow-up-source")

    before = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert before["turns"][0].get("follow_up_candidates", []) == []
    assert asyncio.run(worker.run_once()) is True

    [turn] = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["turns"]
    candidates = turn["follow_up_candidates"]
    assert [(item["label"], item["user_text"]) for item in candidates] == [
        ("기한순으로 보기", "그 업무를 기한순으로 정리해줘"),
        ("우선순위 제안", "먼저 할 업무를 제안해줘"),
        ("담당자별 정리", "담당자별로 묶어줘"),
    ]
    assert all(item["source_turn_id"] == turn_id for item in candidates)
    assert len({item["candidate_id"] for item in candidates}) == 3
    assert all(item["selected_message_id"] is None for item in candidates)


def test_completed_turn_projects_zero_candidates_when_fewer_than_two_are_useful(tmp_path) -> None:
    client, worker = _stack(
        tmp_path,
        ScriptedProvider(candidates=[AiFollowUpCandidate("하나뿐인 후보", "이것만 더 알려줘")]),
    )
    conversation_id, _ = _send(client, MINA, "짧은 질문", "single-follow-up")
    assert asyncio.run(worker.run_once()) is True
    [turn] = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["turns"]
    assert turn["follow_up_candidates"] == []


def test_follow_up_candidate_continues_the_same_conversation_exactly_once(tmp_path) -> None:
    provider = ScriptedProvider(
        candidates=[
            AiFollowUpCandidate("후속 업무 정리", "회의에서 나온 업무를 정리해줘"),
            AiFollowUpCandidate("다음 회의 준비", "다음 회의 안건을 준비해줘"),
        ]
    )
    client, worker = _stack(tmp_path, provider)
    conversation_id, _ = _send(client, MINA, "회의 내용을 요약해줘", "follow-up-seed")
    assert asyncio.run(worker.run_once()) is True
    source = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    candidate = source["turns"][0]["follow_up_candidates"][0]
    domain_counts = (
        len(client.get("/api/tasks", headers=MINA).json()),
        len(client.get("/api/meetings", headers=MINA).json()),
    )

    tampered = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**MINA, "Idempotency-Key": "tampered"},
        json={
            "body": "후속 후보를 가장해 업무를 바로 만들어줘",
            "context": [],
            "follow_up_candidate_id": candidate["candidate_id"],
        },
    )
    assert tampered.status_code == 422

    payload = {
        "body": candidate["user_text"],
        "context": [],
        "follow_up_candidate_id": candidate["candidate_id"],
    }
    first = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**MINA, "Idempotency-Key": "tab-a"},
        json=payload,
    )
    second = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**MINA, "Idempotency-Key": "tab-b"},
        json=payload,
    )
    assert first.status_code == second.status_code == 202
    assert second.json()["message_id"] == first.json()["message_id"]
    assert second.json()["turn_id"] == first.json()["turn_id"]

    after = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    follow_ups = [message for message in after["messages"] if message.get("follow_up_candidate_id")]
    assert [(message["body"], message["follow_up_candidate_id"]) for message in follow_ups] == [
        (candidate["user_text"], candidate["candidate_id"])
    ]
    assert after["turns"][0]["follow_up_candidates"][0]["selected_message_id"] == first.json()["message_id"]
    assert after["actions"] == []  # selecting is a message command, never an Action decision
    assert domain_counts == (
        len(client.get("/api/tasks", headers=MINA).json()),
        len(client.get("/api/meetings", headers=MINA).json()),
    )

    other = client.post("/api/conversations", headers=MINA, json={"title": "other"}).json()
    cross_conversation = client.post(
        f"/api/conversations/{other['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "cross"},
        json=payload,
    )
    assert cross_conversation.status_code == 422
    assert client.get(f"/api/conversations/{other['conversation_id']}", headers=MINA).json()["messages"] == []


def test_failed_turn_keeps_partial_text_and_retry_creates_a_linked_idempotent_turn(tmp_path) -> None:
    provider = ScriptedProvider(fail=True)
    client, worker = _stack(tmp_path, provider)
    conversation_id, turn_id = _send(client, MINA, "내 업무 수를 알려줘", "k-2")
    # First provider failure: the transport backs off and the user sees `retrying`, not a silent stall.
    assert asyncio.run(worker.run_once()) is True
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert view["turns"][0]["state"] == "running" and view["turns"][0]["progress_state"] == "retrying"
    # Max attempts (3) exhausted after the backoff windows (1s, 2s).
    for _ in range(8):
        time.sleep(1.1)
        asyncio.run(worker.run_once())
        view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
        if view["turns"][0]["state"] == "failed":
            break
    failed = view["turns"][0]
    assert failed["state"] == "failed" and failed["progress_state"] == "failed" and failed["error"]
    assistant = [m for m in view["messages"] if m["role"] == "assistant"]
    assert assistant[0]["body_state"] == "failed" and assistant[0]["body"] == "먼저 업무를 조회합니다."  # partial text survives
    assert view["tool_invocations"] and view["tool_invocations"][0]["state"] == "completed"

    provider.fail = False
    first = client.post(f"/api/conversations/{conversation_id}/turns/{turn_id}/retry", headers=MINA)
    assert first.status_code == 202, first.text
    second = client.post(f"/api/conversations/{conversation_id}/turns/{turn_id}/retry", headers=MINA)
    assert second.status_code == 202 and second.json()["turn_id"] == first.json()["turn_id"]  # idempotent one-click retry
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert [t["state"] for t in view["turns"]] == ["failed", "pending"]
    retry = view["turns"][1]
    assert retry["retry_of_turn_id"] == turn_id and retry["progress_state"] == "queued"
    retry_inputs = [m for m in view["messages"] if m["turn_id"] == retry["turn_id"] and m["role"] == "user"]
    assert [m["body"] for m in retry_inputs] == ["내 업무 수를 알려줘"]  # original fragments re-submitted, failed turn preserved
    assert asyncio.run(worker.run_once()) is True
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert [t["state"] for t in view["turns"]] == ["failed", "completed"]
    assert client.post(f"/api/conversations/{conversation_id}/turns/{turn_id}/retry", headers=MINA).json()["turn_id"] == first.json()["turn_id"]
    # Only failed/cancelled turns can be retried; other people cannot touch the conversation.
    assert client.post(f"/api/conversations/{conversation_id}/turns/{retry['turn_id']}/retry", headers=MINA).status_code == 422
    assert client.post(f"/api/conversations/{conversation_id}/turns/{turn_id}/retry", headers=JIHO).status_code == 404


def test_cancelling_a_turn_keeps_what_was_said_and_ignores_what_came_after(tmp_path) -> None:
    """Stopping is a decision the ledger makes first; the provider's later words do not undo it."""
    from threading import Thread

    release = Event()
    provider = ScriptedProvider(release=release)
    client, worker = _stack(tmp_path, provider)
    conversation_id, turn_id = _send(client, MINA, "오래 걸리는 요청", "k-cancel")

    running = Thread(target=lambda: asyncio.run(worker.run_once()))
    running.start()
    try:
        for _ in range(50):
            view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
            if view["turns"][0]["progress_state"] in {"tool_running", "preparing"}:
                break
            time.sleep(0.1)
        current = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
        cancelled = client.post(
            f"/api/conversations/{conversation_id}/cancel",
            headers=MINA,
            json={"expected_version": current["version"]},
        )
        assert cancelled.status_code in {200, 202}, cancelled.text

        view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
        assert view["turns"][0]["state"] == "cancelled" and view["turns"][0]["progress_state"] == "cancelled"
        # What was already said stays, marked for what it is.
        assistant = [message for message in view["messages"] if message["role"] == "assistant"]
        assert assistant[0]["body"] == "먼저 업무를 조회합니다." and assistant[0]["body_state"] == "cancelled"
    finally:
        release.set()
        running.join(timeout=15)

    # The provider kept talking after the decision; none of it moved the turn.
    settled = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert settled["turns"][0]["state"] == "cancelled"
    assistant = [message for message in settled["messages"] if message["role"] == "assistant"]
    assert assistant[0]["body"] == "먼저 업무를 조회합니다." and assistant[0]["body_state"] == "cancelled"
    assert all(message["body"] != "내 업무는 3개입니다." for message in settled["messages"])

    # A stopped turn can be tried again, as a new turn that says what it came from.
    retried = client.post(f"/api/conversations/{conversation_id}/turns/{turn_id}/retry", headers=MINA)
    assert retried.status_code == 202, retried.text
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert view["turns"][1]["retry_of_turn_id"] == turn_id


def test_action_commands_come_from_the_server_and_follow_capability(tmp_path, monkeypatch) -> None:
    provider = ScriptedProvider()
    client, worker = _stack(tmp_path, provider)
    conversation_id, turn_id = _send(client, MINA, "업무 요청을 만들어줘", "k-3")
    application = client.app.state.workflow_application
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    execution_id = None
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(turn_id)).execution_id
    principal = application.authenticated_principal("mina")
    action = application.propose_action(
        principal,
        execution_id,
        "task.create_self",
        "업무 생성 확인",
        {"title": "AX가 만든 업무", "due_date": "2026-09-30"},
    )
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    [projected] = view["actions"]
    expected_commands = [
        {"id": "confirm", "label": "이 내용으로 업무 생성", "tone": "primary"},
        {"id": "reject", "label": "거절", "tone": "neutral"},
    ]
    assert projected["action_id"] == action["action_id"] and projected["commands"] == expected_commands
    assert client.get("/api/actions", headers=MINA).json()[0]["commands"] == expected_commands
    decided = client.post(f"/api/actions/{action['action_id']}/decide", headers=MINA, json={"expected_version": projected["version"], "decision": "approve"})
    assert decided.status_code == 200, decided.text
    # The conversation projection re-reads the canonical Action row on every query: state, version, result, and
    # commands all follow the ledger, not a cached copy from proposal time.
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    canonical = client.get("/api/actions", headers=MINA).json()[0]
    assert view["actions"][0]["state"] == canonical["state"] == "approved"
    assert view["actions"][0]["version"] == canonical["version"] == projected["version"] + 1
    assert view["actions"][0]["result"] == canonical["result"] and view["actions"][0]["audit_ref"] == canonical["audit_ref"]
    assert view["actions"][0]["commands"] == [] and canonical["commands"] == []


def test_action_preview_is_structured_and_permission_safe(tmp_path) -> None:
    provider = ScriptedProvider()
    client, worker = _stack(tmp_path, provider)
    conversation_id, turn_id = _send(client, MINA, "업무 요청을 만들어줘", "k-4")
    application = client.app.state.workflow_application
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(turn_id)).execution_id
    mina = application.authenticated_principal("mina")
    jiho = application.authenticated_principal("jiho")

    # Creation-type Actions: the subject is the real work title; the operation is a separate label; only fields that are
    # actually in the command appear, members are named through the principal's organization visibility.
    action = application.propose_action(
        mina, execution_id, "work_request.create", "업무 요청 생성 확인",
        {"title": "견적서 재검토", "assignee_id": "jiho", "description": "9월 견적 재검토", "due_date": "2026-09-30", "cc_member_ids": ["sora", "no-such-member"]},
    )
    assert action["subject"] == "견적서 재검토" and action["operation_label"] == "업무 요청" and action["title"] == "업무 요청 생성 확인"
    assert action["preview"] == [
        {"id": "description", "label": "설명", "value": "9월 견적 재검토", "kind": "text"},
        {"id": "requester", "label": "요청자", "value": "민아 (구성원)", "kind": "person"},
        {"id": "assignee", "label": "요청 대상", "value": "지호 (팀장)", "kind": "person"},
        {"id": "due_date", "label": "기한", "value": "2026-09-30", "kind": "date"},
        {"id": "cc", "label": "참조자", "value": "소라 (법무 자문), 확인할 수 없는 구성원", "kind": "people"},
    ]
    assert "assignee_id" not in str(action["preview"]) and "no-such-member" not in str(action["preview"])
    # The chat projection and the decision inbox carry the identical presentation.
    projected = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["actions"][0]
    canonical = client.get("/api/actions", headers=MINA).json()[0]
    for key in ("subject", "operation_label", "preview"):
        assert projected[key] == canonical[key] == action[key]
    assert projected["commands"] == canonical["commands"] != []

    # Resource-referencing Actions: the referenced Task title is shown only when the approver can read that Task.
    own = application.create_task(mina, "민아의 업무", idempotency_key="conv-own")
    other = application.create_task(jiho, "지호의 업무", idempotency_key="conv-other")
    readable = application.propose_action(mina, execution_id, "task.update", "업무 수정 확인", {"task_id": own["task_id"], "expected_version": own["version"], "changes": {"due_date": "2026-10-01"}})
    assert readable["subject"] == "민아의 업무"
    assert readable["preview"] == [
        {"id": "task", "label": "대상 업무", "value": "민아의 업무", "kind": "text"},
        {"id": "due_date", "label": "기한", "value": "2026-10-01", "kind": "date"},
    ]
    # Another Action type on the same execution has its own slot; this one references a Task the approver cannot read.
    hidden = application.propose_action(mina, execution_id, "task.transition", "업무 상태 변경 확인", {"task_id": other["task_id"], "expected_version": other["version"], "target": "in_progress"})
    assert hidden["subject"] == "업무 상태 변경 확인", "a Task the approver cannot read must not leak its title"
    assert hidden["preview"] == [{"id": "target", "label": "변경 상태", "value": "진행 중", "kind": "state"}]
    assert "지호의 업무" not in str(hidden)


def test_one_presenter_never_reuses_another_principals_visible_names(tmp_path) -> None:
    """A reused presenter must resolve names per principal, not hand the first caller's candidate list to the next."""
    provider = ScriptedProvider()
    client, worker = _stack(tmp_path, provider)
    conversation_id, turn_id = _send(client, MINA, "요청을 만들어줘", "k-5")
    application = client.app.state.workflow_application
    from ax_workspace.platform.actions import ActionPresenter
    from ax_workspace.platform.persistence import ActionItemRecord, ConversationTurnRecord, make_session_factory

    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(turn_id)).execution_id
    mina = application.authenticated_principal("mina")
    jiho = application.authenticated_principal("jiho")
    proposed = application.propose_action(mina, execution_id, "work_request.create", "업무 요청 생성 확인", {"title": "공용 요청", "assignee_id": "jiho"})

    with make_session_factory(application._settings.database_url)() as session:
        record = session.get(ActionItemRecord, UUID(proposed["action_id"]))
        presenter = ActionPresenter(session, services=application._action_services(session))
        first = presenter.present(record, mina)
        second = presenter.present(record, jiho)
        # Each principal is named as themselves; the requester row is resolved through that principal's own visibility.
        assert {row["id"]: row["value"] for row in first["preview"]}["requester"] == "민아 (구성원)"
        assert {row["id"]: row["value"] for row in second["preview"]}["requester"] == "민아 (구성원)"
        assert presenter._names(mina) is not presenter._names(jiho)
        assert presenter._names(mina)["mina"] == "민아 (구성원)" and presenter._names(jiho)["jiho"] == "지호 (팀장)"
        # The cache is keyed, so a second principal never receives the first principal's map.
        assert set(presenter._name_cache) == {"mina", "jiho"}
