"""답변이 여러 개를 말하면, 그 하나하나가 열리는 정본이어야 한다.

An answer that lists work is a list of resources, not a paragraph to parse. What a delegated turn actually read is
kept as canonical ids and the versions the tools saw, in the order they were read — and every time the conversation
is read back, each reference is asked of the module that owns it. A person who has since lost access to one of them
sees no title, no placeholder and no count.
"""
import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.conversation_worker import ConversationWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.modules.ax_execution.ai import AiConversationResult

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def test_structured_answer_survives_reload_and_rechecks_titles(tmp_path):
    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "실제 업무 제목"}).json()
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "structured-answer")
    principal = application.authenticated_principal("mina")
    application.record_answer_resources(principal, UUID(execution_id), [
        {"resource_type": "task", "resource_id": task["task_id"], "resource_version": task["version"]},
    ])

    class Provider:
        def converse(self, request, **kwargs):
            return AiConversationResult(None, None, "먼저 {{task}}를 정리하세요.", [], answer_elements=[
                {"key": "task", "type": "resource_reference", "ref": f"task:{task['task_id']}"},
            ])

    worker = ConversationWorker(settings, provider=Provider(), queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue))
    assert asyncio.run(worker.run_once())
    path = f"/api/conversations/{conversation['conversation_id']}"
    first = client.get(path, headers=MINA).json()
    assert first["turns"][0]["state"] == "completed"
    message = next(row for row in first["messages"] if row["role"] == "assistant")
    assert message["answer_document"] == {"version": 1, "elements": [
        {"key": "task", "type": "resource_reference", "ref": first["answer_resources"][0]["reference_id"]},
    ]}
    client.patch(f"/api/tasks/{task['task_id']}", headers=MINA, json={"expected_version": task["version"], "title": "수정한 제목"})
    reloaded = client.get(path, headers=MINA).json()
    assert next(row for row in reloaded["messages"] if row["role"] == "assistant")["answer_document"] == message["answer_document"]
    assert reloaded["answer_resources"][0]["title"] == "수정한 제목"
    pack = application.conversation_context_pack(principal, UUID(conversation["conversation_id"]), include_exchanges=True)
    exchange = next(row for row in pack["exchanges"] if row["role"] == "assistant")
    assert "수정한 제목" in exchange["body"]
    assert f"task:{task['task_id']}" in exchange["body"]
    assert first["answer_resources"][0]["reference_id"] not in exchange["body"]
    # A follow-up can cite a prior turn's observation without inventing a second receipt or trusting provider memory.
    accepted = client.post(path + "/messages", headers=MINA, json={"body": "그 업무를 다시 보여줘", "context": []})
    assert accepted.status_code == 202
    assert asyncio.run(worker.run_once())
    follow_up = client.get(path, headers=MINA).json()
    assert follow_up["turns"][-1]["state"] == "completed"
    assert follow_up["messages"][-1]["answer_document"] == message["answer_document"]


@pytest.mark.parametrize("read_method", ["get_task", "task_subtasks"])
def test_subtasks_returned_by_a_read_can_be_used_in_a_structured_answer(tmp_path, monkeypatch, read_method):
    client, settings, database_url, application = _stack(tmp_path)
    parent = client.post("/api/tasks", headers=MINA, json={"title": "상위 업무"}).json()
    child = client.post("/api/tasks", headers=MINA, json={"title": "하위 업무", "parent_task_id": parent["task_id"]}).json()
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "subtask-answer")
    with monkeypatch.context() as env:
        env.setenv("AX_MCP_CAUSATION_ID", execution_id)
        observed = getattr(McpReportsFacade(settings, "mina"), read_method)(parent["task_id"])
    assert [row["task_id"] for row in observed["children"]] == [child["task_id"]]

    class Provider:
        def converse(self, request, **kwargs):
            return AiConversationResult(None, None, "{{children}}", [], answer_elements=[
                {"key": "children", "type": "resource_list", "ordered": False, "items": [
                    {"ref": f"task:{child['task_id']}", "description": "시작 전"},
                ]},
            ])

    worker = ConversationWorker(settings, provider=Provider(), queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue))
    assert asyncio.run(worker.run_once())
    detail = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert detail["turns"][0]["state"] == "completed"
    ref = detail["messages"][-1]["answer_document"]["elements"][0]["items"][0]["ref"]
    assert next(row for row in detail["answer_resources"] if row["reference_id"] == ref)["resource_id"] == child["task_id"]


def test_unobserved_answer_reference_fails_once_without_repeating_the_agent(tmp_path):
    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "읽을 수 있지만 이 대화에서는 조회하지 않은 업무"}).json()
    conversation, _ = _delegated_turn(client, database_url, MINA, "unobserved-answer")

    class Provider:
        calls = 0

        def converse(self, request, **kwargs):
            self.calls += 1
            return AiConversationResult(None, None, "{{task}}", [], answer_elements=[
                {"key": "task", "type": "resource_reference", "ref": f"task:{task['task_id']}"},
            ])

    provider = Provider()
    worker = ConversationWorker(settings, provider=provider, queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue))
    assert asyncio.run(worker.run_once())
    after = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert after["turns"][0]["state"] == "failed"
    assert not asyncio.run(worker.run_once()) and provider.calls == 1
    assert all("{{task}}" not in row["body"] for row in after["messages"])


def test_repeated_task_references_share_reads_only_within_the_current_projection(tmp_path) -> None:
    from ax_workspace.bootstrap.application import _SessionAnswerResources

    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "반복해서 언급한 업무"}).json()
    principal = application.authenticated_principal("mina")
    reference = {"resource_type": "task", "resource_id": task["task_id"], "resource_version": task["version"]}
    queries = []
    engine = application._session_factory.kw["bind"]

    def count_query(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        with application._session_factory() as session:
            resolver = _SessionAnswerResources(application, session)
            assert len(resolver.resolve(principal, [reference])) == 1
        single_read_count = len(queries)
        queries.clear()
        with application._session_factory() as session:
            resolver = _SessionAnswerResources(application, session)
            repeated = resolver.resolve(principal, [dict(reference, turn_id=f"turn-{n}") for n in range(8)])
        assert len(repeated) == 8, "Each turn must keep its own evidence reference"
        assert len(queries) <= single_read_count + 2, "Repeated references must not multiply task detail queries"
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    changed = client.patch(f"/api/tasks/{task['task_id']}", headers=MINA,
                           json={"expected_version": task["version"], "title": "새 제목"})
    assert changed.status_code == 200
    with application._session_factory() as session:
        [fresh] = _SessionAnswerResources(application, session).resolve(principal, [reference])
    assert fresh["title"] == "새 제목" and fresh["changed_since"] is True


def test_repeated_work_request_references_share_one_read_not_two_per_citation(tmp_path) -> None:
    """The task path above already deduplicated and single-read; the sibling kinds never got either fix."""
    from ax_workspace.bootstrap.application import _SessionAnswerResources

    client, settings, database_url, application = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "반복 인용될 요청", "assignee_id": "jiho"}).json()
    principal = application.authenticated_principal("mina")
    reference = {"resource_type": "work_request", "resource_id": request["request_id"], "resource_version": request["version"]}
    queries: list[str] = []
    engine = application._session_factory.kw["bind"]

    def count_query(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        with application._session_factory() as session:
            resolver = _SessionAnswerResources(application, session)
            [resolved] = resolver.resolve(principal, [reference])
        # One fetch answers both title/state and version — not the two separate reads `_read`/`_current_version`
        # used to make.
        single_read_count = len(queries)
        assert resolved["current_version"] == request["version"]
        queries.clear()
        with application._session_factory() as session:
            resolver = _SessionAnswerResources(application, session)
            repeated = resolver.resolve(principal, [dict(reference, turn_id=f"turn-{n}") for n in range(8)])
        assert len(repeated) == 8, "Each turn must keep its own evidence reference"
        assert len(queries) <= single_read_count + 2, "Repeated citations must not multiply work_request reads"
    finally:
        event.remove(engine, "before_cursor_execute", count_query)


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), settings, database_url, app.state.workflow_application


def _delegated_turn(client, database_url, headers, title: str) -> tuple[dict, str]:
    conversation = client.post("/api/conversations", headers=headers, json={"title": title}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": title},
        json={"body": "오늘 하는 일 알려줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    return conversation, str(execution_id)


def test_what_a_turn_read_becomes_something_each_answer_item_can_open(tmp_path, monkeypatch) -> None:
    client, settings, database_url, application = _stack(tmp_path)
    first = client.post("/api/tasks", headers=MINA, json={"title": "먼저 읽은 업무"}).json()
    second = client.post("/api/tasks", headers=MINA, json={"title": "다음에 읽은 업무"}).json()
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "답변이 가리킬 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": [],
        },
    ).json()["meeting"]
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "resource-turn")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    facade = McpReportsFacade(settings, "mina")
    facade.get_task(first["task_id"])
    facade.get_task(second["task_id"])
    # Reading the same thing twice in one turn is one reference, not two.
    facade.get_task(first["task_id"])
    facade.get_meeting(meeting["meeting_id"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    detail = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    references = detail["answer_resources"]
    assert [(row["resource_type"], row["resource_id"]) for row in references] == [
        ("task", first["task_id"]),
        ("task", second["task_id"]),
        ("meeting", meeting["meeting_id"]),
    ]
    assert [row["title"] for row in references] == ["먼저 읽은 업무", "다음에 읽은 업무", "답변이 가리킬 회의"]
    # The version the tool saw is kept, so the reference says what the answer stood on.
    assert references[0]["resource_version"] == first["version"]
    assert all(row["turn_id"] for row in references)
    assert [row["sequence"] for row in references] == sorted(row["sequence"] for row in references)
    from ax_workspace.entrypoints.mcp import _create_bound_persona_server
    server = _create_bound_persona_server(McpReportsFacade(settings, 'mina'))
    observed = asyncio.run(server.call_tool('conversation', {'conversation_id': conversation['conversation_id']})).structured_content
    assert observed == client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    found = asyncio.run(server.call_tool('conversation_search', {'query': '오늘 하는 일'})).structured_content
    assert found['turns'][0]['conversation_id'] == conversation['conversation_id']
    assert found['turns'][0]['excerpt'] == '오늘 하는 일 알려줘'


def test_a_reference_says_where_in_the_source_and_whether_it_moved_since(tmp_path, monkeypatch) -> None:
    """근거는 자리를 가리키는 것이지 내용을 옮겨 적는 것이 아니다. 그리고 그 뒤에 바뀌었으면 말해야 한다.

    링크를 열기 전에 무엇이 달라졌을 수 있는지 사람이 알아야 한다. 감추면 답을 계속 지금의 사실로 읽게 된다.
    """
    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "회차가 움직일 업무"}).json()
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "version-drift")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    McpReportsFacade(settings, "mina").get_task(task["task_id"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    [before] = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["answer_resources"]
    assert before["resource_version"] == task["version"] and before["changed_since"] is False
    assert before["source_locator"] is None, "말해 주지 않은 자리를 지어냈습니다"

    client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": task["version"], "title": "회차가 움직인 업무"},
    )
    [after] = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["answer_resources"]
    # 답이 딛고 선 회차는 그대로이고, 지금 회차가 달라졌다는 사실이 함께 온다.
    assert after["resource_version"] == task["version"]
    assert after["current_version"] == task["version"] + 1
    assert after["changed_since"] is True
    assert after["title"] == "회차가 움직인 업무"


@pytest.mark.serial
def test_a_material_the_answer_read_says_which_page_without_repeating_the_text(tmp_path, monkeypatch) -> None:
    """자료를 읽고 답했으면 그 자료도 답이 가리키는 것이다. 어디였는지는 함께 가고, 원문은 가지 않는다.

    같은 글을 두 곳에 복제하면 권한이 회수된 뒤에도 한쪽에 남는다. 발췌는 근거 카드가 갖고 여기에는 자리만 둔다.
    """
    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "자료가 붙은 업무"}).json()
    uploaded = client.post(
        f"/api/tasks/{task['task_id']}/materials",
        headers=MINA,
        data={"kind": "input"},
        files={"file": ("견적.md", "# briefing\n\n공급사는 한빛상사이고 납기일은 2026-09-30입니다.\n".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    assert asyncio.run(worker.run_once()) is True

    conversation, execution_id = _delegated_turn(client, database_url, MINA, "material-locator")
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    found = McpReportsFacade(settings, 'mina').search_materials('납기일', resource_type='task', resource_id=task['task_id'])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    assert found["results"], "자료에서 아무것도 찾지 못했습니다"

    named = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["answer_resources"]
    [material] = [row for row in named if row["resource_type"] == "material"]
    assert material["title"] == "견적.md" and material["source_contexts"][0]["resource_id"] == task["task_id"]
    # 원문은 여기 없다. 있다면 발췌가 두 곳에 남는다.
    assert "한빛상사" not in str(material) and "납기일" not in str(material)


def test_a_reference_is_asked_of_its_owner_again_every_time_it_is_read(tmp_path, monkeypatch) -> None:
    client, settings, database_url, application = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "공유가 끊길 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": [],
        },
    ).json()["meeting"]
    shared = client.post(
        f"/api/meetings/{meeting['meeting_id']}/shares",
        headers=JIHO,
        json={"member_ids": ["mina"]},
    )
    assert shared.status_code in {200, 201}, shared.text
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "revoked-turn")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    McpReportsFacade(settings, "mina").get_meeting(meeting["meeting_id"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    before_detail = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    before = before_detail["answer_resources"]
    assert [row["title"] for row in before] == ["공유가 끊길 회의"]

    class Provider:
        def converse(self, request, **kwargs):
            return AiConversationResult(None, None, "{{meeting}}", [], answer_elements=[
                {"key": "meeting", "type": "resource_list", "ordered": False, "items": [
                    {"ref": f"meeting:{meeting['meeting_id']}", "description": "공유가 끊길 회의의 비공개 설명"},
                ]},
            ])

    worker = ConversationWorker(settings, provider=Provider(), queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue))
    assert asyncio.run(worker.run_once())

    # The share is taken back. The stored reference is still a row; what it says is asked again.
    revoked = client.delete(f"/api/meetings/{meeting['meeting_id']}/shares/mina", headers=JIHO)
    assert revoked.status_code in {200, 204}, revoked.text
    after = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    # No title, no placeholder, and nothing left to count.
    assert after["answer_resources"] == []
    assert "공유가 끊길 회의" not in str(after)
    message = next(row for row in after["messages"] if row["role"] == "assistant")
    assert message["answer_document"]["elements"][0]["items"] == [{"ref": None, "description": ""}]
    assert client.get(f"/api/meetings/{meeting['meeting_id']}", headers=MINA).status_code == 404
    assert McpReportsFacade(settings, "mina").graph_search("공유가 끊길 회의")["nodes"] == []
    pack = application.conversation_context_pack(application.authenticated_principal("mina"), UUID(conversation["conversation_id"]), include_exchanges=True)
    assert "공유가 끊길 회의" not in str(pack)
    assert meeting["meeting_id"] not in str(pack)


def test_a_turn_records_nothing_for_a_conversation_that_is_not_its_own(tmp_path, monkeypatch) -> None:
    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "남의 대화"}).json()
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "not-mine")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    # 지호 may read his own work, but this execution belongs to 민아's conversation: nothing is written.
    try:
        McpReportsFacade(settings, "jiho").get_task(task["task_id"])
    except Exception:
        pass
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    assert client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["answer_resources"] == []


def test_a_follow_up_starts_from_what_this_conversation_already_read(tmp_path, monkeypatch) -> None:
    """`그중`은 provider의 기억이 아니라 이 대화가 실제로 읽은 canonical id에서 출발한다.

    The seeds are re-read for whoever is asking now, so a follow-up cannot inherit access to something that has since
    been taken away — and when the provider kept no checkpoint, what was said is rebuilt from the conversation itself.
    """
    from ax_workspace.platform.codex_cli import CodexCliProviderAdapter

    client, settings, database_url, application = _stack(tmp_path)
    kept = client.post("/api/tasks", headers=MINA, json={"title": "이어서 물어볼 업무"}).json()
    meeting = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "뒤에 공유가 끊길 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": [],
        },
    ).json()["meeting"]
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/shares",
        headers=JIHO,
        json={"member_ids": ["mina"]},
    )
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "seed-turn")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    facade = McpReportsFacade(settings, "mina")
    facade.get_task(kept["task_id"])
    facade.get_meeting(meeting["meeting_id"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    application = client.app.state.workflow_application
    mina = application.authenticated_principal("mina")
    pack = application.conversation_context_pack(mina, UUID(conversation["conversation_id"]), include_exchanges=True)
    assert [row["ref"] for row in pack["seeds"]] == [f"task:{kept['task_id']}", f"meeting:{meeting['meeting_id']}"]
    # No provider checkpoint: what was actually said is rebuilt from the canonical conversation.
    assert any("오늘 하는 일 알려줘" in row["body"] for row in pack["exchanges"])
    assert all(row["turn_id"] for row in pack["exchanges"])

    client.delete(f"/api/meetings/{meeting['meeting_id']}/shares/mina", headers=JIHO)
    after = application.conversation_context_pack(mina, UUID(conversation["conversation_id"]), include_exchanges=False)
    assert [row["ref"] for row in after["seeds"]] == [f"task:{kept['task_id']}"]
    assert after["exchanges"] == []

    # The provider is told the order to work in, and is handed those ids rather than being asked to remember them.
    prompt = CodexCliProviderAdapter._conversation_prompt(
        __import__("dataclasses").replace(
            _request_for(client, database_url, conversation),
            seed_references=tuple(after["seeds"]),
            recent_exchanges=(),
        )
    )
    assert "graph_search" in prompt and "graph_neighbors" in prompt
    assert "관계 의도는 목록 의도보다 우선한다" in prompt
    assert "관계 의도가 없을 때만" in prompt
    assert f"task:{kept['task_id']}" in prompt
    assert meeting["meeting_id"] not in prompt

    with_turns = CodexCliProviderAdapter._conversation_prompt(
        __import__("dataclasses").replace(
            _request_for(client, database_url, conversation),
            recent_exchanges=tuple(pack["exchanges"]),
        )
    )
    assert f"[turn:{pack['exchanges'][0]['turn_id']}]" in with_turns


def _request_for(client, database_url: str, conversation: dict):
    """The request the worker would hand the provider for this conversation's latest turn."""
    from ax_workspace.platform.conversations import SqlAlchemyConversationRepository
    from ax_workspace.platform.persistence import ConversationTurnRecord
    from sqlalchemy import select

    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    with make_session_factory(database_url)() as session:
        turn = session.scalars(
            select(ConversationTurnRecord)
            .where(ConversationTurnRecord.conversation_id == UUID(conversation["conversation_id"]))
            .order_by(ConversationTurnRecord.started_at.desc())
        ).first()
        return SqlAlchemyConversationRepository(session, None, actions=application._action_repository(session)).request_for(turn, principal)


def test_a_stored_walk_is_checked_again_before_it_is_shown(tmp_path, monkeypatch) -> None:
    """권한이 끊기면 그때 걸었던 발자국도 이름을 남기지 않는다.

    A receipt keeps what a tool returned. Whether the reader may still see it is asked again at display time, so a
    revoked share leaves neither a title nor a step someone could count.
    """
    client, settings, database_url, application = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "발자국에 남을 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": [],
        },
    ).json()["meeting"]
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/shares",
        headers=JIHO,
        json={"member_ids": ["mina"]},
    )
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "walk-turn")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    McpReportsFacade(settings, "mina").graph_neighbors(f"meeting:{meeting['meeting_id']}")
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    walked = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["graph_receipts"]
    assert walked and any("발자국에 남을 회의" in str(step) for step in walked)

    # 민아 is taken off the meeting. The stored steps are still rows; none of them reaches her screen.
    client.delete(f"/api/meetings/{meeting['meeting_id']}/shares/mina", headers=JIHO)
    after = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert "발자국에 남을 회의" not in str(after)
    assert all(f"meeting:{meeting['meeting_id']}" not in str(step) for step in after["graph_receipts"])


def test_a_tool_receipt_stops_naming_what_the_reader_may_no_longer_open(tmp_path, monkeypatch) -> None:
    """도구 영수증에 적힌 제목도 표시 전에 다시 확인한다. 호출한 사실은 남고, 이름은 사라진다."""
    client, settings, database_url, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=JIHO, json={"title": "요약에 남을 업무"}).json()
    conversation, execution_id = _delegated_turn(client, database_url, JIHO, "summary-turn")

    from ax_workspace.platform.persistence import ToolInvocationRecord, ConversationTurnRecord
    from sqlalchemy import select

    with make_session_factory(database_url)() as session:
        turn = session.scalars(
            select(ConversationTurnRecord).where(ConversationTurnRecord.execution_id == UUID(execution_id))
        ).one()
        session.add(
            ToolInvocationRecord(
                turn_id=turn.id,
                sequence=1,
                provider_call_id="call-1",
                tool_name="task_get",
                display_name="업무 조회",
                input_summary="입력: task_id",
                state="completed",
                result_summary=f"결과: title=요약에 남을 업무, state=open, task_id={task['task_id']}",
            )
        )
        session.commit()

    seen = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=JIHO).json()["tool_invocations"]
    assert any("요약에 남을 업무" in str(row["result_summary"]) for row in seen)

    # 지호 hands the work to 민아 and keeps no relationship to it.
    # v2: **제안만으로는 담당이 바뀌지 않는다** (정책 V-18) — 민아가 수락해야 교체가 일어나고,
    # 그때 지호의 관계가 끊긴다. 이 테스트가 보는 것은 관계가 끊긴 **뒤**의 요약이다.
    handed = client.post(
        f"/api/tasks/{task['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": task["version"], "assignee_id": "mina", "reason": "인수인계"},
    )
    assert handed.status_code == 200, handed.text
    taken = client.post(
        f"/api/task-assignments/{handed.json()['assignment_id']}/accept",
        headers={"X-Demo-Persona": "mina"},
    )
    assert taken.status_code == 200, taken.text
    after = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=JIHO).json()
    hidden = [row for row in after["tool_invocations"] if row["tool_name"] == "task_get"]
    assert hidden and hidden[0]["result_summary"] == "결과를 볼 수 없습니다"
    assert "요약에 남을 업무" not in str(after["tool_invocations"])
