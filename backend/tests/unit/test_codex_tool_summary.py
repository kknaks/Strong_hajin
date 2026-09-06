"""Tool timeline summaries never carry material text, regardless of how the MCP transport serialized the result."""
import json

from ax_workspace.platform.codex_cli import CodexEventIngest, _summarize_tool_result


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def accept(self, event) -> None:
        self.events.append(event)

SECRET = "공급사는 한빛상사이고 납기일은 2026-09-30입니다"


def test_material_search_structured_result_reports_counts_and_names_only() -> None:
    payload = {
        "results": [{"name": "견적.md", "excerpt": SECRET}, {"name": "견적.md", "excerpt": SECRET}],
        "searched_materials": 2,
        "unavailable_materials": [{"name": "scan.png"}],
    }
    summary = _summarize_tool_result({"structured_content": payload}, tool_name="task_material_search")
    assert summary == "결과: 자료 2개 검색, 관련 구간 2건 (견적.md), 읽지 못한 자료 1개"
    assert "한빛상사" not in summary


def test_material_search_non_json_text_payload_is_never_echoed() -> None:
    raw = {"content": [{"type": "text", "text": f"excerpt: {SECRET}"}]}
    summary = _summarize_tool_result(raw, tool_name="task_material_search")
    assert "한빛상사" not in summary and "납기일" not in summary
    # Other tools keep the short string fallback.
    assert "결과: hello" == _summarize_tool_result({"content": [{"type": "text", "text": "hello"}]}, tool_name="task_get")


def test_a_failed_tool_says_why_it_failed() -> None:
    """도구가 왜 실패했는지는 결과 안에 있다. 그것을 버리면 사람은 `failed` 세 글자만 본다.

    The model receives the same MCP response either way; it is the person reading the execution rail who is left
    with nothing to act on.
    """
    sink = RecordingSink()
    ingest = CodexEventIngest(sink)
    ingest.consume_line(json.dumps({
        "type": "item.started",
        "item": {"id": "call-1", "type": "mcp_tool_call", "tool": "graph_search", "arguments": {"query": ""}},
    }))
    ingest.consume_line(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "call-1",
            "type": "mcp_tool_call",
            "tool": "graph_search",
            "status": "failed",
            # 서버는 이유를 여기 담아 보낸다. `error` 자리는 비어 있다.
            "result": {"isError": True, "content": [{"type": "text", "text": "찾을 내용을 입력하세요"}]},
        },
    }))
    [call] = [event.tool for event in sink.events if event.tool is not None and event.tool.state == "failed"]
    assert call.error_summary == "실패: 찾을 내용을 입력하세요"
