"""E-6 — AX 도 사람과 같은 「수정」: `save_draft` → 같은 카드의 다음 회차 (WORK-012 2루프 · 사용자 결정 2026-10-07).

운영(2026-10-07 17:43): AX 회의 생성 초안에 「참석자 추가」 를 `save_draft` 로 4번 시도해 전부 실패했다 — 위임 턴이
`ax.*` 항목의 모든 명령을 막았고(`mcp.py`), 사유가 모델·실행 기록에서 가려졌다(`be-loop2-report.md`).

지금 계약:
1. 위임 턴(AX 대화)도 `save_draft` 는 한다 — 서버가 그 카드에 저장을 열어 둔 때만. 등록(확정)·거절은 여전히 사람만.
2. **부분 수정** — 보낸 칸만 지금 회차 위에 덮는다 · `null` = 비우기 · 목록 칸은 전체 교체 · 모르는·바꿀 수 없는 칸은 422.
   사람의 [수정]→저장(전체 draft)도 같은 병합을 탄다 — 결과가 같다. 고친 것이 없으면 회차가 오르지 않는다.
3. 회차의 「누가 고쳤나」(사람/AX)는 감사 기록(`action_item_audit_events` · `draft_saved`)에 남는다.
4. 거절 사유는 `ToolError("<코드>: <한 줄>")` 로 모델에, Codex 실행 기록 `error_summary` 에 남는다(C-1 · C-2).
"""
from __future__ import annotations

import asyncio
import json
from uuid import UUID

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from sqlalchemy import select

from ax_workspace.entrypoints.mcp import McpDelegatedActionAccessDenied, McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.codex_cli import CodexEventIngest
from ax_workspace.platform.persistence import ActionItemAuditEventRecord, ConversationTurnRecord, make_session_factory

from test_mcp_action_items import _delegated_turn, _stack

MINA = {"X-Demo-Persona": "mina"}
PREFIX = "Error executing tool action_item_command: "


def _proposal(client, application, kind: str, payload: dict, key: str) -> dict:
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "초안"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": key}, json={"body": "만들어 줘", "context": []},
    ).json()
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(application.authenticated_principal("mina"), execution_id, kind, "확인", payload)
    return _item(client, proposal["action_id"])


def _item(client, action_item_id: str) -> dict:
    return client.get(f"/api/action-items/{action_item_id}", headers=MINA).json()


def _task(client, application) -> dict:
    return _proposal(client, application, "task.create_self", {
        "title": "주간 보고", "description": "금요일까지", "checklist": ["자료 모으기", "초안 쓰기", "검토 받기"],
    }, "e6-task")


def _meeting(client, application) -> dict:
    return _proposal(client, application, "meeting.reservation.create", {
        "title": "주간 회의", "starts_at": "2026-12-05T10:00:00+09:00", "ends_at": "2026-12-05T11:00:00+09:00",
    }, "e6-meeting")


def _ax_save(settings, item: dict, draft: dict):
    return McpReportsFacade(settings, "mina").run_action_command(
        item["action_item_id"], "save_draft", expected_version=item["expected_version"],
        base_submission_version=item["submission_version"], draft=draft,
    )


def _edits(application, action_item_id: str) -> list[tuple[int, str]]:
    with make_session_factory(application._settings.database_url)() as session:
        rows = session.scalars(
            select(ActionItemAuditEventRecord)
            .where(ActionItemAuditEventRecord.action_id == UUID(action_item_id), ActionItemAuditEventRecord.event_type == "draft_saved")
            .order_by(ActionItemAuditEventRecord.occurred_at)
        ).all()
        return [(row.payload["submission_version"], row.payload["edited_by"]) for row in rows]


# ── AX 의 수정 = 같은 카드의 다음 회차 · 바뀐 칸만 ─────────────────────────────────────────────────────────


def test_ax_shortens_a_task_checklist_as_round_two_and_keeps_the_other_fields(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _task(client, application)
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    _ax_save(settings, item, {"checklist": ["초안 쓰기"], "description": None})
    after = _item(client, item["action_item_id"])
    assert after["submission_version"] == 2 and after["status"] == "awaiting_review"  # 등록은 사람이 한다
    values = after["edit_contract"]["values"]
    assert values["checklist"] == ["초안 쓰기"]  # 목록은 보낸 목록으로 교체
    assert values["description"] is None  # null = 비우기
    assert values["title"] == "주간 보고"  # 안 보낸 칸은 그대로
    assert _edits(application, item["action_item_id"]) == [(2, "ax")]


def test_ax_adds_meeting_attendees_and_moves_the_time(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _meeting(client, application)
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    _ax_save(settings, item, {"attendee_ids": ["jiho"], "starts_at": "2026-12-05T14:00:00+09:00",
                              "ends_at": "2026-12-05T15:00:00+09:00"})
    after = _item(client, item["action_item_id"])
    values = after["edit_contract"]["values"]
    assert after["submission_version"] == 2
    assert values["attendee_ids"] == ["jiho"] and values["title"] == "주간 회의"
    assert values["starts_at"] == "2026-12-05T05:00:00Z"


def test_draft_then_person_then_ax_is_round_three_and_keeps_the_persons_edit(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _meeting(client, application)
    # 사람이 [수정]→저장 — 화면은 편집 계약 값 **전체**를 보낸다.
    person = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/save_draft", headers=MINA,
        json={"expected_version": item["expected_version"], "base_submission_version": 1,
              "draft": {**item["edit_contract"]["values"], "title": "사람이 고친 회의", "purpose": "분기 점검"}},
    )
    assert person.status_code == 200, person.text
    round_two = _item(client, item["action_item_id"])
    assert round_two["submission_version"] == 2
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    _ax_save(settings, round_two, {"attendee_ids": ["jiho"]})
    after = _item(client, item["action_item_id"])
    values = after["edit_contract"]["values"]
    assert after["submission_version"] == 3
    assert (values["title"], values["purpose"], values["attendee_ids"]) == ("사람이 고친 회의", "분기 점검", ["jiho"])
    assert [row["submission_version"] for row in after["rounds"]] == [1, 2, 3]  # 옛 회차는 회차 이력에 남는다
    assert _edits(application, item["action_item_id"]) == [(2, "person"), (3, "ax")]


def test_nothing_changed_does_not_open_a_round(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _task(client, application)
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    _ax_save(settings, item, {"title": "주간 보고"})
    assert _item(client, item["action_item_id"])["submission_version"] == 1
    assert _edits(application, item["action_item_id"]) == []


def test_a_person_saving_the_whole_draft_gets_the_same_result_as_before(tmp_path) -> None:
    """회귀 — 화면의 전체 draft 저장은 병합을 타도 결과가 같다(전체 = 모든 칸을 보낸 부분 수정)."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _task(client, application)
    full = {**item["edit_contract"]["values"], "title": "사람이 고친 보고", "checklist": ["하나"]}
    saved = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/save_draft", headers=MINA,
        json={"expected_version": item["expected_version"], "base_submission_version": 1, "draft": full},
    )
    assert saved.status_code == 200, saved.text
    values = _item(client, item["action_item_id"])["edit_contract"]["values"]
    assert {key: values[key] for key in full} == full


# ── 막히는 것 · 사유 ───────────────────────────────────────────────────────────────────────────────────────


def test_an_unknown_or_locked_field_is_refused_with_its_reason(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    task, meeting = _task(client, application), _meeting(client, application)
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))

    def call(item, draft):
        with pytest.raises(ToolError) as raised:
            asyncio.run(server.call_tool("action_item_command", {
                "action_item_id": item["action_item_id"], "command": "save_draft", "expected_version": item["expected_version"],
                "base_submission_version": item["submission_version"], "draft": draft,
            }))
        assert not isinstance(raised.value, UnexpectedToolError)  # 예상한 거절 — 모델이 사유를 읽는다
        return str(raised.value).removeprefix(PREFIX)

    unknown = call(task, {"colour": "red"})
    assert unknown.split(": ", 1)[0].isupper() and "colour" in unknown
    locked = call(meeting, {"location": "강남역 카페"})
    assert "location" in locked  # 장소는 회의실로만(W-r2-6)
    required = call(task, {"title": None})  # 비울 수 없는 칸
    assert required.split(": ", 1)[0].isupper()
    assert _item(client, task["action_item_id"])["submission_version"] == 1
    # 사람 경로도 같은 규칙 — 422.
    refused = client.post(
        f"/api/action-items/{task['action_item_id']}/commands/save_draft", headers=MINA,
        json={"expected_version": task["expected_version"], "base_submission_version": 1, "draft": {"colour": "red"}},
    )
    assert refused.status_code == 422


@pytest.mark.parametrize("command", ["confirm", "reject", "approve"])
def test_a_delegated_turn_still_cannot_decide_an_ax_draft(tmp_path, monkeypatch, command) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _meeting(client, application)
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    with pytest.raises(McpDelegatedActionAccessDenied, match="사람이 합니다") as refused:
        McpReportsFacade(settings, "mina").run_action_command(
            item["action_item_id"], command, expected_version=item["expected_version"],
            base_submission_version=item["submission_version"],
        )
    assert refused.value.code == "AX_DECISION_REFUSED"
    assert _item(client, item["action_item_id"])["status"] == "awaiting_review"


def test_ax_cannot_save_a_card_that_no_longer_offers_saving(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _task(client, application)
    confirmed = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/confirm", headers=MINA,
        json={"expected_version": item["expected_version"], "base_submission_version": 1},
    )
    assert confirmed.status_code == 200, confirmed.text
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    with pytest.raises(McpDelegatedActionAccessDenied) as refused:
        _ax_save(settings, item, {"title": "늦은 수정"})
    assert refused.value.code == "AX_DRAFT_SAVE_UNAVAILABLE"


def test_domain_refusals_carry_their_reason_and_bugs_stay_unexpected(tmp_path, monkeypatch) -> None:
    """C-1 — 도메인 예외(낡은 판)는 코드·한 줄로 · 프로그램 오류는 그대로 「예상 못 한 실패」."""
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    item = _task(client, application)
    facade = McpReportsFacade(settings, "mina")
    server = _create_bound_persona_server(facade)
    with pytest.raises(ToolError) as stale:
        asyncio.run(server.call_tool("action_item_command", {
            "action_item_id": item["action_item_id"], "command": "save_draft", "expected_version": item["expected_version"] + 7,
            "base_submission_version": item["submission_version"], "draft": {"title": "x"},
        }))
    assert not isinstance(stale.value, UnexpectedToolError)
    code, _, reason = str(stale.value).removeprefix(PREFIX).partition(": ")
    assert code and code == code.upper() and reason

    def broken(*args, **kwargs):
        raise KeyError("bug")

    monkeypatch.setattr(facade, "run_action_command", broken)
    with pytest.raises(UnexpectedToolError):
        asyncio.run(server.call_tool("action_item_command", {
            "action_item_id": item["action_item_id"], "command": "save_draft", "expected_version": item["expected_version"],
        }))


def test_a_failed_codex_tool_call_keeps_its_reason_even_without_an_error_flag() -> None:
    """C-2 — 실행 기록이 「실패: failed」 대신 사유를 남긴다. 결과의 글자 조각만 읽는다."""
    ingest = CodexEventIngest(None)
    item = {"id": "call-1", "type": "mcp_tool_call", "server": "scax", "tool": "action_item_command",
            "arguments": {"command": "confirm"}, "status": "failed",
            "result": {"content": [{"type": "text", "text": "AX_DECISION_REFUSED: AX 제안의 등록(확정)·거절은 사람이 합니다"}]}}
    ingest.consume_line(json.dumps({"type": "item.completed", "item": item}))
    [invocation] = ingest.tool_invocations()
    assert invocation.error_summary == "실패: AX_DECISION_REFUSED: AX 제안의 등록(확정)·거절은 사람이 합니다"
    flagged = CodexEventIngest(None)
    flagged.consume_line(json.dumps({"type": "item.completed", "item": {**item, "result": {**item["result"], "isError": True}}}))
    assert flagged.tool_invocations()[0].error_summary == invocation.error_summary
    bare = CodexEventIngest(None)
    bare.consume_line(json.dumps({"type": "item.completed", "item": {**item, "result": {"content": []}}}))
    assert bare.tool_invocations()[0].error_summary == "실패: failed"  # 사유가 정말 없을 때만
