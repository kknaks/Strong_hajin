"""AI 맥락 목록의 모양 (SPEC-010 §4.5) — 세 표 · 머리 · 도구는 보조."""
from __future__ import annotations

from datetime import UTC, date, datetime
import json

from ax_workspace.modules.ax_execution.context_catalog import (
    AiContextCatalog,
    ContextMember,
    ContextProject,
    ContextTask,
    catalog_payload,
    render_ai_context_catalog,
)


def test_the_catalog_carries_the_three_tables_in_the_contract_shape() -> None:
    catalog = AiContextCatalog(
        at=datetime(2026, 10, 7, 1, 0, tzinfo=UTC),
        projects=[ContextProject("p1", "CASTI", "active")],
        tasks=[ContextTask("t1", "배지 정리", "p1", "in_progress", date(2026, 10, 10))],
        members=[ContextMember("m1", "팀원 A", "제품팀", "팀장")],
    )
    assert catalog_payload(catalog) == {
        "projects": [{"id": "p1", "name": "CASTI", "status": "active"}],
        "tasks": [{"id": "t1", "title": "배지 정리", "project_id": "p1", "state": "in_progress", "due_date": "2026-10-10"}],
        "members": [{"id": "m1", "name": "팀원 A", "unit": "제품팀", "position": "팀장"}],
    }
    text = render_ai_context_catalog(catalog)
    assert text.startswith("## 조직 맥락 목록(이 호출 시각 2026-10-07T01:00:00+00:00 기준)")
    assert "목록에서 먼저 찾고" in text and "도구로 조회" in text
    body = text.strip().splitlines()[-1]
    assert json.loads(body) == catalog_payload(catalog)
