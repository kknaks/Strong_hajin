"""Tool timeline summaries never carry material text, regardless of how the MCP transport serialized the result."""
from ax_workspace.platform.codex_cli import _summarize_tool_result

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
