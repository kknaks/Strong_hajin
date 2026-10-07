"""메시지함 → AX — 참고 자료 `inbox_message` · 맥락 조합 · 업무 출처 · 「업무 만듦」 (SPEC-008 §4.8 · §4.4 · WORK-012 WP4-BE).

연동·방·원문은 표에 직접 심는다(`test_external_inbox._seed`). AX provider 는 대역이다 — 실메시지 1건으로 맥락을
조합하는 실물 확인은 코디 E2E 몫이다.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from ax_workspace.bootstrap.conversation_worker import ConversationWorker
from ax_workspace.modules.ax_execution.ai import AiConversationRequest, AiConversationResult, AiDelegatedToolContext
from ax_workspace.modules.external_channels.message_context import (
    MessageContextComposer,
    MessageNotFound,
    render_slack_text,
)
from ax_workspace.platform.codex_cli import CodexCliProviderAdapter
from ax_workspace.platform.claude_cli import ClaudeCliProviderAdapter
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.platform.external_channels_inbox_store import SqlAlchemyInboxStore
from ax_workspace.platform.persistence import (
    ConversationTurnRecord,
    ExternalIntegrationRecord,
    ExternalMessageRecord,
    ExternalRoomRecord,
    TaskRecord,
    WorkRequestRecord,
)

import pytest

from test_external_inbox import JIHO, MINA, T0, _seed, _stack


# ── 발판 ────────────────────────────────────────────────────────────────────────────────


def _channel(sessions, seeded, *, count: int, name: str = "big", users: dict | None = None) -> list[str]:
    """최상위 메시지 `count` 개짜리 슬랙 채널 하나 — 1분 간격. 메시지 id 를 시각 순으로 돌려준다."""
    with sessions() as session:
        room = ExternalRoomRecord(
            integration_id=UUID(seeded["slack"]), external_id=f"C-{name}", room_type="channel", name=name, status="live",
            room_meta={"users": users or {}}, created_at=T0, updated_at=T0,
        )
        session.add(room)
        session.flush()
        ids = []
        for index in range(count):
            ts = f"{1800000000 + index}.000100"
            row = ExternalMessageRecord(
                integration_id=room.integration_id, room_id=room.id, source_kind="slack", container_key=room.external_id,
                external_key=ts, thread_key=None, sent_at=T0 + timedelta(minutes=index), author="U-OTHER",
                preview=f"줄 {index}", raw={"ts": ts, "user": "U-OTHER", "text": f"줄 {index}"}, created_at=T0,
            )
            session.add(row)
            session.flush()
            ids.append(str(row.id))
        session.commit()
        return ids


def _compose(app, member: str, message_id: str):
    with app.state.workflow_application._session_factory() as session:
        return MessageContextComposer(SqlAlchemyInboxStore(session)).compose(member, UUID(message_id))


def _reference(message_id: str, *, version: int = 1) -> dict:
    return {"resource_type": "inbox_message", "resource_id": message_id, "resource_version": version, "included": True}


def _send(client, headers, body: str, context: list[dict], *, conversation_id: str | None = None):
    if conversation_id is None:
        conversation_id = client.post("/api/conversations", headers=headers, json={"title": "메시지함"}).json()["conversation_id"]
    accepted = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"body": body, "context": context},
    )
    return conversation_id, accepted


class _Provider:
    def __init__(self) -> None:
        self.requests: list[AiConversationRequest] = []

    def converse(self, request, **kwargs):
        self.requests.append(request)
        return AiConversationResult(None, None, "확인했습니다.", [])


def _worker(app, provider):
    application = app.state.workflow_application
    return ConversationWorker(
        application._settings, provider=provider,
        queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue),
    )


# ── ① 참고 자료 종류 · 해석기 (SPEC-008 §4.8 ①) ──────────────────────────────────────────────────────────────


def test_an_inbox_message_reference_is_accepted_and_resolved_on_the_server(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    conversation_id, accepted = _send(client, MINA, "이 메시지 요약해 줘", [_reference(seeded["slack_rows"][1])])
    assert accepted.status_code == 202, accepted.text
    [reference] = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["context_references"]
    assert reference["resource_type"] == "inbox_message" and reference["resource_id"] == seeded["slack_rows"][1]
    assert reference["summary"] == "슬랙 메시지" and reference["resource_version"] == 1
    assert reference["label"] is None  # 조합 전 — 화면은 줄을 그리지 않는다(WP4 계약 고정 1)


def test_someone_elses_message_is_404(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    _, refused = _send(client, JIHO, "이 메일 요약해 줘", [_reference(seeded["mails"][0])])
    assert refused.status_code == 404, refused.text
    # 대화가 사라진 줄 알지 않게 — 메시지 쪽 문구와 코드(검수 W-6).
    assert refused.json()["detail"] == {
        "code": "INBOX_MESSAGE_NOT_FOUND", "message": "참고한 메시지를 찾을 수 없습니다 — 지워졌거나 방을 나갔습니다",
    }
    _, unknown = _send(client, MINA, "이 메일 요약해 줘", [_reference(str(uuid4()))])
    assert unknown.status_code == 404


def test_a_message_of_a_removed_room_or_integration_is_404(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    with sessions() as session:
        session.get(ExternalRoomRecord, UUID(seeded["channel"])).removed_at = datetime.now(UTC)
        session.get(ExternalIntegrationRecord, UUID(seeded["mail"])).removed_at = datetime.now(UTC)
        session.commit()
    for message_id in (seeded["slack_rows"][0], seeded["mails"][0]):
        refused = _send(client, MINA, "요약", [_reference(message_id)])[1]
        assert refused.status_code == 404 and refused.json()["detail"]["code"] == "INBOX_MESSAGE_NOT_FOUND"


def test_the_version_of_a_message_is_always_one(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    assert _send(client, MINA, "요약", [_reference(seeded["mails"][0], version=2)])[1].status_code == 422


# ── ② 맥락 조합 — 서버 함수 하나 (SPEC-008 §4.8 ② · D-31 · D-32) ─────────────────────────────────────────────


def test_a_channel_message_carries_a_hundred_lines_above_and_below(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    ids = _channel(sessions, seeded, count=251)
    composed = _compose(app, "mina", ids[150])
    lines = composed.document["lines"]
    assert composed.document["range"] == "around_100" and composed.document["room"] == "#big"
    assert len(lines) == 201 and lines[100]["target"] is True and lines[100]["text"] == "줄 150"
    assert lines[0]["text"] == "줄 50" and lines[-1]["text"] == "줄 250"
    assert sum(1 for line in lines if line.get("target")) == 1
    assert composed.label == "슬랙 · #big · 10/01 18:50~10/01 22:10 · 201건"


def test_near_the_top_of_a_room_it_carries_what_there_is(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    ids = _channel(sessions, seeded, count=150)
    composed = _compose(app, "mina", ids[10])
    lines = composed.document["lines"]
    assert len(lines) == 10 + 1 + 100 and lines[10]["target"] is True
    tail = _compose(app, "mina", ids[-1])
    assert len(tail.document["lines"]) == 101 and tail.document["lines"][-1]["target"] is True
    assert tail.label.endswith("· 101건")


def test_a_channel_message_counts_its_replies_but_does_not_carry_them(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    parent = seeded["slack_rows"][2]  # 스레드 부모(답글 하나)
    with sessions() as session:
        row = session.get(ExternalMessageRecord, UUID(parent))
        row.raw = {**row.raw, "reply_count": 1}
        session.commit()
    composed = _compose(app, "mina", parent)
    texts = [line["text"] for line in composed.document["lines"]]
    assert composed.document["range"] == "around_100"
    assert "슬랙 3" not in texts  # 답글 본문은 넣지 않는다(D-32)
    target = next(line for line in composed.document["lines"] if line.get("target"))
    assert target["thread_reply_count"] == 1


def test_a_thread_reply_carries_the_whole_thread_with_its_parent(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    composed = _compose(app, "mina", seeded["slack_rows"][3])
    assert composed.document["range"] == "thread"
    assert [line["text"] for line in composed.document["lines"]] == ["슬랙 2", "슬랙 3"]
    assert [bool(line.get("target")) for line in composed.document["lines"]] == [False, True]
    assert composed.label == "슬랙 · 스레드 · 2건"


def test_a_mail_is_that_one_mail_as_plain_text_without_the_quoted_part(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    composed = _compose(app, "mina", seeded["mails"][1])
    [line] = composed.document["lines"]
    assert composed.document == {**composed.document, "source": "mail", "room": None, "range": "mail"}
    assert line["sender"] == "Partner <partner@example.com>" and line["target"] is True
    assert line["text"].startswith("메일 1") and "안녕하세요" in line["text"] and "좋은 링크" in line["text"]
    assert "예전 메일" not in line["text"]  # 인용 접기는 뺀다(OQ-819)
    assert "<" not in line["text"] and "steal" not in line["text"]  # HTML·스크립트 아님
    assert line["attachments"] == ["견적서.pdf"]
    assert composed.label == "메일 · 메일 1"


def test_lines_carry_our_shape_only_with_names_resolved(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    ids = _channel(sessions, seeded, count=3, name="names", users={"U-OTHER": {"name": "다른 사람"}, "U-2": {"name": "둘째"}})
    with sessions() as session:
        row = session.get(ExternalMessageRecord, UUID(ids[1]))
        row.raw = {**row.raw, "text": "<@U-2> 확인 <#C2|random> <https://a.example|문서> &lt;끝&gt;",
                   "blocks": [{"type": "rich_text"}], "reactions": [{"name": "+1"}]}
        deleted = session.get(ExternalMessageRecord, UUID(ids[2]))
        deleted.raw = {**deleted.raw, "ax_deleted": True}
        session.commit()
    lines = _compose(app, "mina", ids[1]).document["lines"]
    assert set(lines[1]) == {"at", "sender", "text", "attachments", "thread_reply_count", "target"}
    assert set(lines[0]) == {"at", "sender", "text", "attachments", "thread_reply_count"}
    assert lines[1]["sender"] == "다른 사람"
    assert lines[1]["text"] == "@둘째 확인 #random 문서 (https://a.example) <끝>"
    assert lines[2]["text"] == "(삭제된 메시지)"
    assert lines[0]["at"].endswith("+09:00")


def test_a_kakao_room_reads_as_kakao(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    with sessions() as session:
        row = ExternalMessageRecord(
            integration_id=UUID(seeded["kakao"]), room_id=UUID(seeded["chat"]), source_kind="kakao", container_key="18200",
            external_key="9001", sent_at=T0, author="엄마", preview="밥 먹자", raw={"logId": 9001, "author": "엄마", "text": "밥 먹자"},
            created_at=T0,
        )
        session.add(row)
        session.commit()
        message_id = str(row.id)
    composed = _compose(app, "mina", message_id)
    assert composed.document["source"] == "kakao" and composed.document["room"] == "팀방"
    assert composed.document["lines"][0] == {
        "at": composed.document["lines"][0]["at"], "sender": "엄마", "text": "밥 먹자", "attachments": [],
        "thread_reply_count": 0, "target": True,
    }
    assert composed.label == "카톡 · 팀방 · 10/01 18:00~10/01 18:00 · 1건"


def test_the_composer_hides_someone_elses_message(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    with pytest.raises(MessageNotFound):
        _compose(app, "jiho", seeded["mails"][0])


def test_slack_text_rendering_follows_the_inbox_rules() -> None:
    assert render_slack_text("<!here> <!subteam^S1|@dev> <https://x.example>", {}) == "@here @dev https://x.example"
    assert render_slack_text("<@U9>", {}) == "@U9"


# ── 실행 직전 조합 · 프롬프트 · 말풍선 아래 한 줄 (§4.8 ① · WP4 계약 고정 1) ──────────────────────────────────


def test_the_turn_carries_the_composed_context_and_the_label_shows_under_the_bubble(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    conversation_id, accepted = _send(client, MINA, "이 메시지 읽고 업무를 생성해 줘", [_reference(seeded["slack_rows"][3])])
    assert accepted.status_code == 202
    provider = _Provider()
    assert asyncio.run(_worker(app, provider).run_once())
    [request] = provider.requests
    [reference] = request.context_references
    document = json.loads(reference["context"])
    assert document["range"] == "thread" and document["label"] == "슬랙 · 스레드 · 2건"
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    [row] = view["context_references"]
    assert row["label"] == "슬랙 · 스레드 · 2건" and row["turn_id"] == accepted.json()["turn_id"]


def test_the_prompt_carries_the_context_json_as_its_own_block() -> None:
    document = {"label": "메일 · 견적", "source": "mail", "room": None, "range": "mail", "lines": []}
    request = AiConversationRequest(
        "이 메일 요약해 줘", None,
        [{"resource_type": "inbox_message", "resource_id": "m1", "resource_version": "1", "summary": "메일 메시지",
          "context": json.dumps(document, ensure_ascii=False)},
         {"resource_type": "task", "resource_id": "t1", "resource_version": "3", "summary": "업무: 보고서"}],
        AiDelegatedToolContext("mina", "x"),
    )
    for adapter in (CodexCliProviderAdapter, ClaudeCliProviderAdapter):
        prompt = adapter._conversation_prompt(request)
        assert "- inbox_message:m1: 메일 메시지" in prompt  # 한 줄 요약은 그대로
        assert "Inbox message context" in prompt and json.dumps(document, ensure_ascii=False) in prompt
        assert prompt.index("Inbox message context") < prompt.index("User message:")


# ── ③ 업무 출처 · ④ 「업무 만듦」 · `inbox.message_updated` (§4.8 ③④ · §4.4) ───────────────────────────────────


def _propose(app, client, conversation_id: str, accepted, action_type: str, payload: dict) -> dict:
    application = app.state.workflow_application
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    proposal = application.propose_action(application.authenticated_principal("mina"), execution_id, action_type, "업무 확인", payload)
    return client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()


def _confirm(client, item: dict):
    return client.post(
        f"/api/action-items/{item['action_item_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": item["expected_version"], "base_submission_version": item["submission_version"]},
    )


def test_a_task_confirmed_from_a_message_links_back_and_the_message_shows_made_task(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    message_id = seeded["slack_rows"][1]
    conversation_id, accepted = _send(client, MINA, "이 메시지 읽고 업무를 생성해 줘", [_reference(message_id)])
    item = _propose(app, client, conversation_id, accepted, "task.create_self", {"title": "슬랙에서 온 일"})
    app.state.workflow_application.local_user_events.clear()
    confirmed = _confirm(client, item)
    assert confirmed.status_code == 200, confirmed.text
    with sessions() as session:
        task = session.query(TaskRecord).filter(TaskRecord.title == "슬랙에서 온 일").one()
        assert task.source_inbox_message_id == UUID(message_id)
        task_id = str(task.id)
    origin = client.get(f"/api/tasks/{task_id}", headers=MINA).json()["origin"]
    assert origin["message"] == {
        "message_id": message_id, "source_kind": "slack", "room_id": seeded["channel"], "label": "슬랙 #general",
    }
    assert origin["source"]["type"] == "action_item"  # 「판단 보기」 는 그대로, 원래 메시지가 함께
    messages = client.get(f"/api/inbox/rooms/{seeded['channel']}/messages", headers=MINA).json()["messages"]
    counts = {row["id"]: row["made_task_count"] for row in messages}
    assert counts[message_id] == 1 and counts[seeded["slack_rows"][0]] == 0
    # 커밋 뒤 사건 하나 — 받는 사람은 그 메시지의 주인, 본문 없음(§4.4 · H-5).
    [event] = [json.loads(row) for row in app.state.workflow_application.local_user_events]
    assert event["type"] == "inbox.message_updated" and event["member_id"] == "mina"
    assert event["message_id"] == message_id and event["room_id"] == seeded["channel"]


def test_a_request_from_a_mail_records_the_mail_on_the_request_and_its_task(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    mail_id = seeded["mails"][1]
    conversation_id, accepted = _send(client, MINA, "이 메일 읽고 업무를 생성해 줘", [_reference(mail_id)])
    assert asyncio.run(_worker(app, _Provider()).run_once())
    # 단계 카드로 한 턴 더 이어진 뒤 제안이 나왔다 — 그 대화에 실린 메시지가 출처다.
    _, follow_up = _send(client, MINA, "지호에게 요청으로 보내줘", [], conversation_id=conversation_id)
    assert follow_up.status_code == 202 and follow_up.json()["turn_id"] != accepted.json()["turn_id"]
    item = _propose(app, client, conversation_id, follow_up, "work_request.create", {"title": "메일에서 온 요청", "assignee_id": "jiho"})
    confirmed = _confirm(client, item)
    assert confirmed.status_code == 200, confirmed.text
    with sessions() as session:
        request = session.query(WorkRequestRecord).filter(WorkRequestRecord.title == "메일에서 온 요청").one()
        task = session.query(TaskRecord).filter(TaskRecord.source_work_request_id == request.id).one()
        assert request.source_inbox_message_id == UUID(mail_id) and task.source_inbox_message_id == UUID(mail_id)
    mail = client.get(f"/api/inbox/mail/{mail_id}", headers=MINA).json()
    assert mail["made_task_count"] == 1
    assert client.get(f"/api/inbox/mail/{seeded['mails'][0]}", headers=MINA).json()["made_task_count"] == 0


def test_a_summary_only_conversation_marks_nothing_and_a_plain_task_has_no_message(tmp_path) -> None:
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    conversation_id, accepted = _send(client, MINA, "요약해 줘", [])
    item = _propose(app, client, conversation_id, accepted, "task.create_self", {"title": "그냥 일"})
    app.state.workflow_application.local_user_events.clear()
    assert _confirm(client, item).status_code == 200
    with sessions() as session:
        task = session.query(TaskRecord).filter(TaskRecord.title == "그냥 일").one()
        assert task.source_inbox_message_id is None
        task_id = str(task.id)
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).json()["origin"]["message"] is None
    assert app.state.workflow_application.local_user_events == []
    del seeded


def test_the_origin_link_does_not_name_someone_elses_message(tmp_path) -> None:
    """원래 메시지가 남의 것이 되면 링크는 서되 이름(채널·제목)을 싣지 않는다 — 누르면 「메시지를 찾을 수 없습니다」."""
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    message_id = seeded["mails"][1]
    conversation_id, accepted = _send(client, MINA, "이 메일 읽고 업무를 생성해 줘", [_reference(message_id)])
    item = _propose(app, client, conversation_id, accepted, "task.create_self", {"title": "메일 일"})
    assert _confirm(client, item).status_code == 200
    with sessions() as session:
        task_id = str(session.query(TaskRecord).filter(TaskRecord.title == "메일 일").one().id)
        session.get(ExternalIntegrationRecord, UUID(seeded["mail"])).removed_at = datetime.now(UTC)
        session.commit()
    message = client.get(f"/api/tasks/{task_id}", headers=MINA).json()["origin"]["message"]
    assert message == {"message_id": message_id, "source_kind": "mail", "room_id": None, "label": "원래 메일"}
    assert client.get(f"/api/inbox/mail/{message_id}", headers=MINA).status_code == 404


def test_the_reference_stops_attaching_once_a_task_from_it_is_confirmed(tmp_path) -> None:
    """검수 W-1(코디 판정 ①) — 참고 자료 턴 ~ 제안 턴 사이에 다른 업무 확정이 없을 때만 붙는다.
    참고 자료 → 업무 확정 → (같은 대화의) 다른 업무 확정 = 둘째엔 출처가 없고 「업무 만듦」 도 늘지 않는다."""
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    message_id = seeded["slack_rows"][1]
    conversation_id, accepted = _send(client, MINA, "이 메시지 읽고 업무를 생성해 줘", [_reference(message_id)])
    first = _propose(app, client, conversation_id, accepted, "task.create_self", {"title": "메시지에서 온 첫 일"})
    assert _confirm(client, first).status_code == 200
    assert asyncio.run(_worker(app, _Provider()).run_once())
    _, later = _send(client, MINA, "그런데 다른 일 하나 만들어 줘", [], conversation_id=conversation_id)
    second = _propose(app, client, conversation_id, later, "task.create_self", {"title": "무관한 둘째 일"})
    app.state.workflow_application.local_user_events.clear()
    assert _confirm(client, second).status_code == 200
    with sessions() as session:
        origins = {task.title: task.source_inbox_message_id for task in session.query(TaskRecord).filter(
            TaskRecord.title.in_(["메시지에서 온 첫 일", "무관한 둘째 일"]))}
    assert origins == {"메시지에서 온 첫 일": UUID(message_id), "무관한 둘째 일": None}
    counts = {row["id"]: row["made_task_count"] for row in
              client.get(f"/api/inbox/rooms/{seeded['channel']}/messages", headers=MINA).json()["messages"]}
    assert counts[message_id] == 1
    assert app.state.workflow_application.local_user_events == []
    # 새 메시지를 다시 실으면 새 흐름이다 — 그 다음 확정에는 그 메시지가 붙는다.
    assert asyncio.run(_worker(app, _Provider()).run_once())
    other = seeded["slack_rows"][0]
    _, again = _send(client, MINA, "이 메시지로도 업무 만들어 줘", [_reference(other)], conversation_id=conversation_id)
    third = _propose(app, client, conversation_id, again, "task.create_self", {"title": "새 메시지의 일"})
    assert _confirm(client, third).status_code == 200
    with sessions() as session:
        assert session.query(TaskRecord).filter(TaskRecord.title == "새 메시지의 일").one().source_inbox_message_id == UUID(other)


def test_a_proposal_later_in_the_same_flow_still_carries_the_message(tmp_path) -> None:
    """단계 카드로 몇 턴 뒤에 나온 제안도 — 그 사이 확정이 없으면 — 그 참고 자료의 흐름이다."""
    client, app, _, cipher, sessions = _stack(tmp_path)
    seeded = _seed(sessions, cipher)
    message_id = seeded["slack_rows"][1]
    conversation_id, accepted = _send(client, MINA, "이 메시지 읽고 업무를 생성해 줘", [_reference(message_id)])
    rejected = _propose(app, client, conversation_id, accepted, "task.create_self", {"title": "거절할 초안"})
    assert client.post(
        f"/api/action-items/{rejected['action_item_id']}/commands/reject", headers=MINA,
        json={"expected_version": rejected["expected_version"]},
    ).status_code == 200
    assert asyncio.run(_worker(app, _Provider()).run_once())
    _, later = _send(client, MINA, "기한은 금요일로", [], conversation_id=conversation_id)
    item = _propose(app, client, conversation_id, later, "task.create_self", {"title": "다듬은 일"})
    assert _confirm(client, item).status_code == 200
    with sessions() as session:
        assert session.query(TaskRecord).filter(TaskRecord.title == "다듬은 일").one().source_inbox_message_id == UUID(message_id)
