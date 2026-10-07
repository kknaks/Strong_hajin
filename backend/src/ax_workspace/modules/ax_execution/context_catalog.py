"""AI 맥락 목록 — 회의록(웜스타트·최종 합성)과 AX 대화가 같이 싣는 **조직 맥락 한 덩어리** (SPEC-010 §4.5 · DEC-009 D-13·D-14·D-20).

- **별도 테이블을 두지 않는다.** AI 를 부를 때마다 그 순간 DB 를 조회해 조립한다 — 새로 생긴 프로젝트도 다음 호출부터 들어간다.
- **조직 전체**다(D-13 정본): 프로젝트 전부 · 업무는 완료도 취소도 안 된 것만(D-14) · 구성원 전부. 호출자 시야로 좁히지 않는다.
- **상한 없음**(코디 기본값 · 크기 관측 후 재검토 — WORK-012 I-6). 렌더 결과의 글자 수를 부르는 쪽이 로그로 남긴다.
- 쓰는 자리는 **세 곳이 이 함수 하나를 부른다** — 회의 웜스타트 · 최종 합성 · AX 대화(WP3). 세 곳에 따로 짜지 않는다.

이 파일은 DB 를 모른다. 조회는 `AiContextCatalogSource` 포트(조립층이 `platform/ai_context.py` 로 끼운다)가 하고,
여기는 모양과 글자만 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ContextProject:
    id: str
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class ContextTask:
    id: str
    title: str
    project_id: str | None
    state: str
    due_date: date | None


@dataclass(frozen=True, slots=True)
class ContextMember:
    id: str
    name: str
    unit: str | None
    position: str | None


@dataclass(frozen=True, slots=True)
class AiContextCatalog:
    """한 호출 시각의 조직 맥락 — 세 표다."""

    at: datetime
    projects: list[ContextProject] = field(default_factory=list)
    tasks: list[ContextTask] = field(default_factory=list)
    members: list[ContextMember] = field(default_factory=list)


class AiContextCatalogSource(Protocol):
    """조직 맥락을 **그 순간 DB 에서** 읽어 오는 포트."""

    def load(self) -> AiContextCatalog: ...


#: 프롬프트에서 이 덩어리를 가리키는 머리 — 웜스타트·최종·대화가 같은 이름으로 부른다.
CATALOG_HEADING = "조직 맥락 목록"


def catalog_payload(catalog: AiContextCatalog) -> dict:
    """세 표의 JSON 모양 — `{id, name, status}` · `{id, title, project_id, state, due_date}` · `{id, name, unit, position}`."""
    return {
        "projects": [{"id": row.id, "name": row.name, "status": row.status} for row in catalog.projects],
        "tasks": [
            {
                "id": row.id,
                "title": row.title,
                "project_id": row.project_id,
                "state": row.state,
                "due_date": row.due_date.isoformat() if row.due_date else None,
            }
            for row in catalog.tasks
        ],
        "members": [
            {"id": row.id, "name": row.name, "unit": row.unit, "position": row.position} for row in catalog.members
        ],
    }


def render_ai_context_catalog(catalog: AiContextCatalog) -> str:
    """프롬프트에 싣는 한 덩어리 — 머리 + 쓰는 법 + 세 표(JSON).

    「목록에 있는 것은 목록에서 먼저 찾고, 상세가 필요할 때만 도구로」(SPEC-010 §4.5 도구 조회 행) — 도구는 보조로 남는다.
    """
    stamp = catalog.at.isoformat(timespec="seconds")
    body = json.dumps(catalog_payload(catalog), ensure_ascii=False, separators=(",", ":"))
    return (
        f"## {CATALOG_HEADING}(이 호출 시각 {stamp} 기준)\n\n"
        "조직의 **프로젝트 전부 · 완료도 취소도 안 된 업무 · 구성원 전부**다. 이름·업무·프로젝트를 가리킬 때는 "
        "**이 목록에서 먼저 찾고**, 상세가 필요할 때만 도구로 조회해라. 목록에 없는 id 를 지어내지 마라.\n\n"
        f"{body}\n"
    )
