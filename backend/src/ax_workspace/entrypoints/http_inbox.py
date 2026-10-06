"""메시지함·답장·사용자 WS·카톡 수신·프로필 설정 라우트 (SPEC-008 §4.4·§4.6·§4.7 · WORK-011 Phase BE-3).

`create_app` 이 `register_inbox_routes(app)` 한 줄로 붙인다(BE-1 의 `http.py` 를 크게 열지 않는다). 인증은 두 갈래다
(R3-F1): 웹 라우트는 로그인 세션(`developer_principal` = `current_principal`), **카톡 수신 라우트는 기기 토큰**
(`device_principal` — 세션·페르소나를 받지 않는다). 소유는 아래층이 가린다 — 남의 것은 404.

오류 → HTTP 상태는 여기서만 정한다(Case Matrix): 404 없음/남의 것 · 403 고르지 않은 카톡 방 · 409 끊김·조회 전용·
handshake 전 · 410 카톡 첨부 만료 · 413 한도 · 415 형식 · 400 이미지 프록시 규칙 · 422 요청 모양 · 502 상류 실패.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.websockets import WebSocketState

from ax_workspace.entrypoints.http_auth import connection_principal, developer_principal, device_principal, session_id_from
from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.external_channels import inbox as inbox_rules
from ax_workspace.modules.external_channels.inbox import (
    AttachmentGone,
    Download,
    InboxPage,
    IntegrationUnavailable,
    InvalidInboxRequest,
    MailView,
    OutgoingFile,
    PayloadTooLarge,
    RemoteImageRejected,
    ReplyAcceptedView,
    RoomMessagesPage,
    UpstreamFailed,
)
from ax_workspace.modules.external_channels.kakao_ingest import (
    KAKAO_ATTACHMENT_LIMIT_BYTES,
    KakaoAttachmentSlotClosed,
    KakaoHandshakeRequired,
    KakaoMessagesAccepted,
    KakaoMessagesCommand as KakaoMessagesRequest,
    KakaoRoomNotSelected,
    KakaoStatusAccepted,
    KakaoStatusCommand as KakaoStatusRequest,
)
from ax_workspace.modules.meetings.material_policy import inline_media_type
from ax_workspace.modules.organization_access.credentials import PasswordRejected
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.organization_access.profile_settings import (
    PROFILE_IMAGE_LIMIT_BYTES,
    CurrentPasswordIncorrect,
    PasswordChangeCommand as ChangePasswordRequest,
    ProfileImageMissing,
    ProfileImageSaved,
    ProfileImageTooLarge,
    ProfileImageUnsupported,
)

CLOSE_UNAUTHORIZED = 4401
#: 첨부 응답 — 내용이 aid 로 고정이라 이 브라우저(private)가 잠깐 기억해도 된다. 방을 다시 열면 즉시(BE 수정 판 6).
ATTACHMENT_CACHE = "private, max-age=600"
CLOSE_FORBIDDEN_ORIGIN = 4403


def _same_origin(origin: str | None, web_origin: str) -> bool:
    """`Origin` 이 웹 origin 과 같은가. 루프백끼리(`localhost`·`127.0.0.1`)는 같은 포트면 같다고 본다 — 로컬 개발."""
    if not origin:
        return True
    from urllib.parse import urlsplit

    left, right = urlsplit(origin.rstrip("/")), urlsplit(web_origin)
    loopback = {"localhost", "127.0.0.1", "::1"}
    same_host = left.hostname == right.hostname or (left.hostname in loopback and right.hostname in loopback)
    return left.scheme == right.scheme and same_host and left.port == right.port
#: 중계 응답 중 브라우저가 «그 자리에서» 그려도 되는 것 — 래스터 이미지와 `inline_media_type` 허용 목록(PDF·markdown).
INLINE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
#: 중계 바이트는 우리 origin 에서 나간다 — 스크립트가 되지 못하게 묶는다(F-3).
RELAY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class RoomReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    up_to_ts: str | int = Field(title="이 메시지(슬랙 ts · 카톡 logId)까지 읽음")


def _inbox_error(error: Exception) -> HTTPException:
    if isinstance(error, KakaoRoomNotSelected):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "room_not_selected", "message": str(error)})
    if isinstance(error, (ResourceNotFound, ProfileImageMissing)):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="찾을 수 없습니다")
    if isinstance(error, IntegrationUnavailable):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": error.code, "message": str(error)})
    if isinstance(error, KakaoAttachmentSlotClosed):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "attachment_not_accepted", "message": str(error)})
    if isinstance(error, KakaoHandshakeRequired):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"code": "handshake_required", "message": str(error)})
    if isinstance(error, AttachmentGone):
        return HTTPException(status.HTTP_410_GONE, detail={"code": error.code})
    if isinstance(error, (PayloadTooLarge, ProfileImageTooLarge)):
        return HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=str(error))
    if isinstance(error, ProfileImageUnsupported):
        return HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error))
    if isinstance(error, RemoteImageRejected):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "remote_image_rejected", "message": str(error)})
    if isinstance(error, (InvalidInboxRequest, PasswordRejected)):
        code = "password_rejected" if isinstance(error, PasswordRejected) else "invalid_request"
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": code, "message": str(error)})
    if isinstance(error, CurrentPasswordIncorrect):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "current_password_incorrect", "message": str(error)})
    if isinstance(error, UpstreamFailed):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"code": "upstream_failed", "reason": error.code})
    raise error


def _download(download: Download, *, cache: str = "private, no-store") -> Response:
    declared = (download.content_type or "application/octet-stream").split(";")[0].strip().lower()
    inline = declared if declared in INLINE_IMAGE_TYPES else inline_media_type(download.name, declared)
    disposition = "inline" if inline else "attachment"
    return Response(
        content=download.data,
        media_type=inline or declared or "application/octet-stream",
        headers={
            **RELAY_HEADERS,
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(download.name or 'attachment')}",
            "Cache-Control": cache,
        },
    )


async def _outgoing(files: list[UploadFile]) -> list[OutgoingFile]:
    """보낼 첨부를 읽는다 — 한도를 **읽으면서** 잰다(검수 W-8). 개수가 넘으면 읽기 전에, 하나가 50MB 를 넘으면 그 한
    바이트에서 멈춘다(메일 합계 25MB 는 접수가 다시 잰다)."""
    if len(files) > inbox_rules.MAX_REPLY_FILES:
        message = f"파일은 한 번에 {inbox_rules.MAX_REPLY_FILES}개까지입니다"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"code": "invalid_request", "message": message})
    outgoing: list[OutgoingFile] = []
    for item in files:
        limit = inbox_rules.SLACK_FILE_LIMIT_BYTES
        data = await item.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="첨부는 하나당 50MB 까지입니다")
        outgoing.append(OutgoingFile(
            name=(item.filename or "file")[:300], content_type=item.content_type or "application/octet-stream", data=data,
        ))
    return outgoing


def register_inbox_routes(app: FastAPI) -> None:
    # --- 메시지함 (§4.4) --------------------------------------------------------------------

    @app.get("/api/inbox/messages")
    def inbox_messages(
        source: str = "all",
        unread: bool = False,
        cursor: str | None = None,
        principal: Principal = Depends(developer_principal),
    ) -> InboxPage:
        """카드 목록 — 메일은 한 통, 슬랙·카톡은 방 하나가 카드다(D-08). `unread_counts` 가 레일 숫자다."""
        try:
            return app.state.workflow_application.inbox_messages(principal, source, unread, cursor)
        except Exception as error:
            raise _inbox_error(error) from error

    @app.get("/api/inbox/mail/{message_id}")
    def inbox_mail(message_id: UUID, response: Response, principal: Principal = Depends(developer_principal)) -> MailView:
        """메일 원문 머리 + **소독한 안전본 HTML**(F-3) + 첨부 메타 + 우리가 보낸 답장(D-47)."""
        try:
            view = app.state.workflow_application.inbox_mail(principal, message_id)
        except Exception as error:
            raise _inbox_error(error) from error
        response.headers["Cache-Control"] = "private, no-store"
        return view

    @app.get("/api/inbox/rooms/{room_id}/messages")
    def inbox_room_messages(
        room_id: UUID,
        cursor: str | None = None,
        thread_ts: str | None = None,
        principal: Principal = Depends(developer_principal),
    ) -> RoomMessagesPage:
        """방 대화 — 최신 페이지부터, `next_cursor` 로 위(과거)로. `thread_ts` 면 그 스레드(부모 포함)."""
        try:
            return app.state.workflow_application.inbox_room_messages(principal, room_id, cursor, thread_ts)
        except Exception as error:
            raise _inbox_error(error) from error

    @app.post("/api/inbox/rooms/{room_id}/read", status_code=status.HTTP_204_NO_CONTENT)
    def mark_inbox_room_read(
        room_id: UUID, request: RoomReadRequest, principal: Principal = Depends(developer_principal)
    ) -> Response:
        try:
            app.state.workflow_application.mark_inbox_room_read(principal, room_id, str(request.up_to_ts))
        except Exception as error:
            raise _inbox_error(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/inbox/mail/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT)
    def mark_inbox_mail_read(message_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
        try:
            app.state.workflow_application.mark_inbox_mail_read(principal, message_id)
        except Exception as error:
            raise _inbox_error(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/inbox/read-all", status_code=status.HTTP_204_NO_CONTENT)
    def mark_inbox_read_all(source: str = "all", principal: Principal = Depends(developer_principal)) -> Response:
        try:
            app.state.workflow_application.mark_inbox_read_all(principal, source)
        except Exception as error:
            raise _inbox_error(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/inbox/mail/{message_id}/attachments/{aid}")
    def inbox_mail_attachment(message_id: UUID, aid: str, principal: Principal = Depends(developer_principal)) -> Response:
        """그 회원의 Gmail 토큰으로 **그때 받아 넘긴다** — 저장하지 않는다(D-29)."""
        try:
            download = app.state.workflow_application.inbox_mail_attachment(principal, message_id, aid)
        except Exception as error:
            raise _inbox_error(error) from error
        return _download(download, cache=ATTACHMENT_CACHE)

    @app.get("/api/inbox/rooms/{room_id}/attachments/{aid}")
    def inbox_room_attachment(
        room_id: UUID,
        aid: str,
        variant: Literal["thumb"] | None = None,
        principal: Principal = Depends(developer_principal),
    ) -> Response:
        """슬랙 = 그 회원 토큰으로 중계 · 카톡 = 저장본(만료 410). `variant=thumb` 이면 슬랙 이미지의 썸네일(대화 미리보기용)."""
        try:
            download = app.state.workflow_application.inbox_room_attachment(principal, room_id, aid, variant)
        except Exception as error:
            raise _inbox_error(error) from error
        return _download(download, cache=ATTACHMENT_CACHE)

    @app.get("/api/inbox/mail/{message_id}/remote-image")
    def inbox_remote_image(
        message_id: UUID, u: str = Query(min_length=1, max_length=4000), principal: Principal = Depends(developer_principal)
    ) -> Response:
        """「이미지 보기」— 그 메일 안전본에 실제로 있는 주소만 서버가 받아 넘긴다(N-5 · SSRF 규칙)."""
        try:
            download = app.state.workflow_application.inbox_remote_image(principal, message_id, u)
        except Exception as error:
            raise _inbox_error(error) from error
        return _download(download, cache="private, max-age=86400")

    @app.post("/api/inbox/rooms/{room_id}/reply", status_code=status.HTTP_202_ACCEPTED)
    async def reply_inbox_room(
        room_id: UUID,
        text: str = Form(default=""),
        thread_ts: str | None = Form(default=None),
        files: list[UploadFile] = File(default=[]),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        principal: Principal = Depends(developer_principal),
    ) -> ReplyAcceptedView:
        """슬랙 채널·스레드 답장 — **내 이름**(사용자 토큰) · 파일 하나당 50MB · 결과는 `/api/inbox/stream` 사건으로."""
        outgoing = await _outgoing(files)
        try:
            accepted = await asyncio.to_thread(
                app.state.workflow_application.accept_inbox_slack_reply,
                principal,
                room_id,
                text=text,
                thread_ts=thread_ts,
                files=outgoing,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            raise _inbox_error(error) from error
        return {"local_id": accepted.local_id}

    @app.post("/api/inbox/mail/{message_id}/reply", status_code=status.HTTP_202_ACCEPTED)
    async def reply_inbox_mail(
        message_id: UUID,
        body: str = Form(default=""),
        reply_all: bool = Form(default=False),
        to: list[str] = Form(default=[]),
        cc: list[str] = Form(default=[]),
        files: list[UploadFile] = File(default=[]),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        principal: Principal = Depends(developer_principal),
    ) -> ReplyAcceptedView:
        """메일 답장/전체 답장 — `Re:` · 스레드 이어짐 · 첨부 **합계** 25MB(413) · 보낸 답장 기록(D-47)."""
        outgoing = await _outgoing(files)
        try:
            accepted = await asyncio.to_thread(
                app.state.workflow_application.accept_inbox_mail_reply,
                principal,
                message_id,
                reply_all=reply_all,
                body=body,
                to=to,
                cc=cc,
                files=outgoing,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            raise _inbox_error(error) from error
        return {"local_id": accepted.local_id}

    @app.websocket("/api/inbox/stream")
    async def inbox_stream(websocket: WebSocket) -> None:
        """사용자 사건 채널(P-4) — 핸드셰이크의 세션 쿠키로 사람을 풀고 **그 사람 것만** 민다.

        내려가는 것은 `{"type":"ready"}` 다음 `UserEvent` 의 JSON(`type`·`integration_id`·`room_id`·`message_id`·
        `source_kind`·`data`). 본문은 싣지 않는다 — 화면이 API 로 다시 읽는다. 올라오는 프레임은 읽고 버린다.
        """
        await websocket.accept()
        if not _same_origin(websocket.headers.get("origin"), app.state.workflow_application._settings.web_origin):
            # 쿠키 인증 WS 를 다른 사이트가 열지 못하게(검수 W-9). Origin 이 없는 것(브라우저 밖)은 쿠키로만 판단한다.
            await websocket.close(code=CLOSE_FORBIDDEN_ORIGIN, reason="origin")
            return
        principal = connection_principal(websocket)
        if principal is None:
            await websocket.close(code=CLOSE_UNAUTHORIZED, reason="unauthorized")
            return
        member_id = str(principal.id)
        hub = app.state.workflow_application.user_event_hub
        queue = hub.subscribe(member_id)

        async def drain() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        reader = asyncio.ensure_future(drain())
        client_left = False
        try:
            await websocket.send_json({"type": "ready"})
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    getter.cancel()
                    client_left = True
                    return
                event = getter.result()
                body: dict[str, Any] = {"type": event.type}
                for key in ("integration_id", "room_id", "message_id", "source_kind"):
                    value = getattr(event, key)
                    if value is not None:
                        body[key] = value
                if event.data:
                    body["data"] = event.data
                await websocket.send_json(body)
        except (WebSocketDisconnect, RuntimeError):
            client_left = True
            return
        finally:
            hub.unsubscribe(member_id, queue)
            reader.cancel()
            # 화면이 먼저 떠났으면 닫을 상대가 없다 — 닫기를 보내면 끊긴 소켓에 쓰다 예외가 난다.
            if not client_left and websocket.application_state == WebSocketState.CONNECTED:
                try:
                    await websocket.close()
                except (WebSocketDisconnect, RuntimeError):
                    pass

    # --- 카톡 수신 — 기기 토큰만 (§4.6) ----------------------------------------------------------

    @app.post("/api/integrations/kakao/messages", status_code=status.HTTP_202_ACCEPTED)
    def kakao_messages(request: KakaoMessagesRequest, principal: Principal = Depends(device_principal)) -> KakaoMessagesAccepted:
        """메시지 묶음 — **서버의 고른 방**이 아니면 403 · 500건 초과 413 · 중복 `(연동, chatId, logId)` 은 버린다."""
        try:
            return app.state.workflow_application.kakao_ingest_messages(principal, request)
        except Exception as error:
            raise _inbox_error(error) from error

    @app.post("/api/integrations/kakao/attachments/{aid}", status_code=status.HTTP_201_CREATED)
    async def kakao_attachment(
        aid: str,
        file: UploadFile = File(...),
        name: str | None = Form(default=None),
        mime: str | None = Form(default=None),
        principal: Principal = Depends(device_principal),
    ) -> Response:
        """사진·앨범·파일 바이트 — hostPath 저장 · DB 는 경로만 · 50MB 초과 413(앱은 메타만 올린다)."""
        if file.size is not None and file.size > KAKAO_ATTACHMENT_LIMIT_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="카톡 첨부는 하나당 50MB 까지입니다")
        data = await file.read(KAKAO_ATTACHMENT_LIMIT_BYTES + 1)
        try:
            await asyncio.to_thread(
                app.state.workflow_application.kakao_store_attachment,
                principal,
                aid,
                data,
                name or file.filename,
                mime or file.content_type,
            )
        except Exception as error:
            raise _inbox_error(error) from error
        return Response(status_code=status.HTTP_201_CREATED)

    @app.post("/api/integrations/kakao/status")
    def kakao_status(request: KakaoStatusRequest, principal: Principal = Depends(device_principal)) -> KakaoStatusAccepted:
        """30초 주기 상태 보고 — 응답의 `selected_rooms_version` 이 바뀌었으면 앱이 handshake 를 다시 부른다(W3-3)."""
        try:
            return app.state.workflow_application.kakao_report_status(principal, request)
        except Exception as error:
            raise _inbox_error(error) from error

    # --- 프로필 설정 (§4.7) ----------------------------------------------------------------------

    @app.get("/api/profile/image")
    def profile_image(principal: Principal = Depends(developer_principal)) -> Response:
        try:
            data, content_type = app.state.workflow_application.profile_image(principal)
        except Exception as error:
            raise _inbox_error(error) from error
        return Response(
            content=data,
            media_type=content_type,
            headers={**RELAY_HEADERS, "Cache-Control": "private, max-age=31536000, immutable"},
        )

    @app.put("/api/profile/image")
    async def save_profile_image(
        file: UploadFile = File(...), principal: Principal = Depends(developer_principal)
    ) -> ProfileImageSaved:
        """고르면 바로 저장 — 1MB 이하 PNG·JPG(바이트 머리로 판정) · 넘으면 413, 형식이 아니면 415."""
        data = await file.read(PROFILE_IMAGE_LIMIT_BYTES + 1)
        try:
            return await asyncio.to_thread(app.state.workflow_application.save_profile_image, principal, data)
        except Exception as error:
            raise _inbox_error(error) from error

    @app.delete("/api/profile/image", status_code=status.HTTP_204_NO_CONTENT)
    def delete_profile_image(principal: Principal = Depends(developer_principal)) -> Response:
        app.state.workflow_application.delete_profile_image(principal)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/profile/password", status_code=status.HTTP_204_NO_CONTENT)
    def change_password(
        request: ChangePasswordRequest, http_request: Request, principal: Principal = Depends(developer_principal)
    ) -> Response:
        """현재 확인 → 새 비밀번호 저장 → **이 세션만 남기고 다른 로그인 모두 해제 + 기기 토큰 모두 철회**(R3-F1 ⑤)."""
        try:
            app.state.workflow_application.change_password(principal, request, session_id_from(http_request))
        except Exception as error:
            raise _inbox_error(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)
