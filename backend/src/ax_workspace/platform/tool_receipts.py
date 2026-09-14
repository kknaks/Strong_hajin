"""Provider-neutral redaction of MCP tool calls into human-readable receipts.

Shared by every CLI adapter so the redaction rules — what argument keys are safe to show, which tools carry
document content that must never be echoed — stay in one place instead of drifting per provider.
"""
from __future__ import annotations

import json
from typing import Any

from ax_workspace.modules.ax_execution.tool_catalog import tool_display_title

_SAFE_ARGUMENT_KEYS = frozenset(
    {"title", "task_id", "request_id", "report_id", "draft_id", "expected_version", "assignee_id", "report_date", "state", "action", "limit", "query"}
)
_RESULT_KEYS = ("title", "state", "status", "draft_version", "version", "task_id", "request_id", "report_id", "action_id", "searched_materials")
_CONTENT_BEARING_TOOLS = frozenset({"material_search"})


def display_name(item_type: str, tool_name: str, item: dict[str, Any]) -> str:
    del item
    if item_type == "mcp_tool_call":
        return tool_display_title(tool_name)
    if item_type == "command_execution":
        return "명령 실행"
    if item_type == "web_search":
        return "웹 검색"
    if item_type == "file_change":
        return "파일 변경"
    return tool_name.replace("_", " ")


def truncate(text: str, limit: int = 48) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_tool_arguments(arguments: dict[str, Any]) -> str:
    """Keep identifiers and short titles; mask free text such as bodies, reasons, and notes."""
    if not arguments:
        return "입력 없음"
    parts: list[str] = []
    for key, value in arguments.items():
        if key in _SAFE_ARGUMENT_KEYS and isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={truncate(str(value))}")
        elif isinstance(value, list):
            parts.append(f"{key}=[{len(value)}건]")
        else:
            parts.append(f"{key}=비공개")
    return "입력: " + ", ".join(parts)


def summarize_tool_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        if message:
            return f"실패: {truncate(str(message), 80)}"
    if isinstance(error, str) and error.strip():
        return f"실패: {truncate(error, 80)}"
    return "도구 실행 실패"


def summarize_tool_result(result: Any, *, tool_name: str = "") -> str:
    """Summarize an MCP tool result without echoing raw payloads.

    Tools whose results carry document text never fall back to echoing a string payload, whatever the transport's
    serialization shape; their evidence is projected separately as bounded excerpts.
    """
    payload: Any = None
    if isinstance(result, dict):
        payload = result.get("structured_content") or result.get("structuredContent")
        if payload is None:
            for content in result.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    try:
                        payload = json.loads(content["text"])
                    except json.JSONDecodeError:
                        payload = content["text"]
                    break
        if result.get("isError") or result.get("is_error"):
            return summarize_tool_error(payload if isinstance(payload, str) else result.get("error"))
    elif result is not None:
        payload = result
    if isinstance(payload, list):
        return f"결과: {len(payload)}건 조회"
    if isinstance(payload, dict) and isinstance(payload.get("results"), list) and "searched_materials" in payload:
        # This timeline has no complete candidate identity set to reauthorize historic counts or names.
        # Details belong to the separately reauthorized material evidence, even for a no-hit search.
        return "결과 수신 (자료 내용은 근거 카드에만 표시)"
    if tool_name in _CONTENT_BEARING_TOOLS:
        return "결과 수신 (자료 내용은 근거 카드에만 표시)"
    if isinstance(payload, dict):
        facts = [f"{key}={truncate(str(payload[key]), 32)}" for key in _RESULT_KEYS if key in payload and payload[key] not in (None, "")]
        if facts:
            return "결과: " + ", ".join(facts)
        return f"결과: 항목 {len(payload)}개 수신"
    if isinstance(payload, str) and payload.strip():
        return f"결과: {truncate(payload, 80)}"
    return "결과 없음"
