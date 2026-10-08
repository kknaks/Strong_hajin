"""사용자 사건 채널 — SSE `GET /api/events/stream` (SPEC-011 §4.1 · SPEC-008 v0.7.0 §4.4 · WORK-013 WP1-BE).

회원 하나의 연결 하나가 그 회원의 사건을 모두 받는다 — 메시지함 사건 넷(이름·필드 그대로)과 알림 사건 둘. 옛 WS
`/api/inbox/stream`(`http_inbox.py`)을 대체한다(그 라우트는 되돌림 여지로 남는다). 회의 WS 는 따로다(D-11).

- **인증** — 세션 쿠키(개발 `X-Demo-Persona` 도 REST 와 같은 이음새). 없으면 스트림을 열지 않고 `401` JSON. `Origin` 이
  있고 웹 origin 과 다르면 `403`(옛 WS 의 같은 출처 검사를 옮겼다 — 닫힘 코드가 아니라 HTTP 상태). 화면은 비-200 의 까닭을
  못 보므로 세션 확인은 `GET /api/auth/me` 가 한다(§4.1-6).
- **첫 줄** `retry: 3000` → `event: ready` · 사건이 없어도 **20초마다 `: ping`**(프록시 유휴 끊김 방지).
- **사건 id 는 `notification.upserted` 만**(= 알림의 사건 순번). 다시 붙을 때 마지막 순번은 `Last-Event-ID` 머리나
  `?last_event_id=`(둘 다면 머리) → 겹침 창 이어 받기(`replayed: true`) → `resync(reconnected)`. 200건을 넘으면
  `resync(replay_overflow)` 만, 기준 줄이 없으면 `resync(reconnected)` 만.
- **구독을 먼저 걸고 DB 를 읽는다** — 이어 받기와 실시간 사이 빈틈이 없고, 그사이 큐에 든 같은 순번은 한 번만 보낸다(§4.1-3-6).
- 큐가 넘쳐 버린 사건이 있으면 그 연결에 `resync(dropped)`(§4.1-4).

DB 읽기는 동기 세션이라 이벤트 루프를 막지 않게 스레드로 넘긴다. 이 파일은 `platform/` 을 모른다 — 허브·DB 는 조립된
`workflow_application` 을 거친다.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ax_workspace.entrypoints.http_auth import current_principal
from ax_workspace.entrypoints.http_inbox import _same_origin
from ax_workspace.modules.external_channels.events import EVENT_VERSION, INBOX_EVENT_TYPES, UserEvent, UserEventType
from ax_workspace.modules.organization_access.domain import Principal

#: 하트비트 간격(초) — Cloudflare 프록시가 빈 응답을 끊지 않게(SPEC-011 §4.1-1). 운영 실측으로 줄일 수 있는 상수다.
HEARTBEAT_SECONDS = 20.0
#: 네트워크 오류 뒤 브라우저 자체 재연결 간격(밀리초). 비-200 에는 브라우저가 다시 붙지 않는다(§4.1-6).
RETRY_MILLISECONDS = 3000
STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _frame(event: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _resync(reason: str) -> str:
    return _frame("resync", {"v": EVENT_VERSION, "reason": reason})


def _last_event_id(request: Request) -> tuple[bool, int | None]:
    """(다시 붙었나, 마지막 순번). 머리가 쿼리보다 앞선다. **정수가 아니면 없는 것으로 본다**(첫 연결처럼 — SPEC-011 Validation).
    음수는 정수이므로 다시 붙었지만 기준이 없는 것 → `resync`."""
    raw = request.headers.get("last-event-id")
    if raw is None or not raw.strip():
        raw = request.query_params.get("last_event_id")
    if raw is None or not raw.strip():
        return False, None
    try:
        value = int(raw.strip())
    except ValueError:
        return False, None
    return True, value if value >= 0 else None


def _inbox_data(event: UserEvent) -> dict[str, Any]:
    """메시지함 사건 — NOTIFY 페이로드 그대로(`{v, type, member_id, integration_id?, room_id?, message_id?, source_kind?, data?}`)."""
    return json.loads(event.to_payload())


def register_event_routes(app: FastAPI) -> None:
    @app.get("/api/events/stream", response_model=None)
    async def events_stream(request: Request) -> StreamingResponse | JSONResponse:
        workflow = request.app.state.workflow_application
        if not _same_origin(request.headers.get("origin"), workflow._settings.web_origin):
            return JSONResponse({"detail": "다른 출처에서는 사건 채널을 열 수 없습니다."}, status_code=403)
        try:
            principal = current_principal(request)
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        reconnected, last_seq = _last_event_id(request)
        return StreamingResponse(
            _stream(workflow, principal, reconnected=reconnected, last_seq=last_seq),
            media_type="text/event-stream; charset=utf-8",
            headers=STREAM_HEADERS,
        )


async def _stream(workflow: Any, principal: Principal, *, reconnected: bool, last_seq: int | None) -> AsyncIterator[str]:
    member_id = str(principal.id)
    hub = workflow.user_event_hub
    queue = hub.subscribe(member_id)  # 먼저 구독 — 그다음 DB(§4.1-3-6)
    sent: set[int] = set()
    try:
        yield f"retry: {RETRY_MILLISECONDS}\n\n"
        yield _frame("ready", {"v": EVENT_VERSION, "server_time": datetime.now(UTC).isoformat()})
        if reconnected:
            if last_seq is None:
                yield _resync("reconnected")
            else:
                replay = await asyncio.to_thread(workflow.notification_replay, principal, last_seq)
                if replay["outcome"] == "overflow":
                    yield _resync("replay_overflow")
                else:
                    for item in replay["items"]:
                        sent.add(item["seq"])
                        yield _frame(
                            "notification.upserted",
                            {"v": EVENT_VERSION, "notification": item, "created": False, "replayed": True},
                            event_id=item["seq"],
                        )
                    yield _resync("reconnected")
        while True:
            try:
                event: UserEvent = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if getattr(queue, "dropped", False):
                queue.dropped = False
                yield _resync("dropped")
            frame = await _live_frame(workflow, principal, event, sent)
            if frame is not None:
                yield frame
    finally:
        hub.unsubscribe(member_id, queue)


async def _live_frame(workflow: Any, principal: Principal, event: UserEvent, sent: set[int]) -> str | None:
    if event.type in INBOX_EVENT_TYPES:
        return _frame(event.type, _inbox_data(event))
    if event.type == UserEventType.NOTIFICATION_READ:
        return _frame(event.type, {"v": EVENT_VERSION, **event.data})
    if event.type == UserEventType.NOTIFICATION_UPSERTED and event.notification_id:
        if event.seq is not None and event.seq in sent:
            return None  # 이어 받기가 이미 보낸 순번 — 한 번만(§4.1-3-6)
        try:
            notification_id = UUID(event.notification_id)
        except ValueError:
            return None
        item = await asyncio.to_thread(workflow.notification_event_item, principal, notification_id)
        if item is None or item["seq"] in sent:
            return None
        sent.add(item["seq"])
        return _frame(
            event.type,
            {"v": EVENT_VERSION, "notification": item, "created": bool(event.created), "replayed": False},
            event_id=item["seq"],
        )
    return None  # 모르는 사건 — 내보내지 않는다
