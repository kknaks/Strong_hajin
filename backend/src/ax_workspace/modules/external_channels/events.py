"""사용자 사건 채널 — **NOTIFY 계약** (SPEC-008 §4.4 실시간 갱신 · F-4 ③ · N-6 · 4차 검수 W4-3).

새 메시지를 받는 곳은 연동 워커(다른 프로세스, BE-2)이고 사용자 WS `/api/inbox/stream` 은 back(BE-3)에 산다.
둘을 Postgres `LISTEN/NOTIFY` 로 잇는다. 이 파일이 그 사이의 **유일한 계약**이다 — 채널 이름과 페이로드 모양을
양쪽이 여기서 가져다 쓴다(글자를 따로 적지 않는다).

- **`USER_EVENTS_CHANNEL`** (`ax_user_events`) — 워커·back → back WS. 「메시지함 전용」이 아니라 2단계(AX 판단·알림)도
  쓸 **사용자 사건 채널**이다(P-4). 페이로드는 아래 `UserEvent.to_payload()` 의 JSON 한 줄(8000바이트 한도 안 — 본문을
  싣지 않고 **무엇이 바뀌었는지만** 싣는다. WS 는 받으면 회원 것만 거르고, 화면이 API 로 다시 읽는다).
- **`SYNC_WAKE_CHANNEL`** (`ax_external_sync`) — back → 연동 워커. 연결·재연결·방 추가가 일어나면 워커가 다음 폴링을
  기다리지 않고 바로 백필을 집게 깨운다. 놓쳐도 된다 — 워커는 상태 칸(`status='backfilling'`·`backfill_done_at`)을
  주기적으로 훑어 같은 일을 찾는다(at-least-once 의 보조 신호).

NOTIFY 는 **보내는 트랜잭션이 커밋될 때** 전달된다 — 저장과 같은 트랜잭션에서 내면 「알림은 왔는데 행이 없다」가
없다. 보내는 쪽 구현은 `platform/user_events.py` 의 `publish`(PostgreSQL 만 · 그 밖의 DB 에선 아무것도 안 함).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

USER_EVENTS_CHANNEL = "ax_user_events"
SYNC_WAKE_CHANNEL = "ax_external_sync"

#: 페이로드 판. 모양을 바꾸면 올린다 — 듣는 쪽은 모르는 판을 버린다.
EVENT_VERSION = 1


class UserEventType:
    #: 새 메시지가 저장됐다 — 메일 단건이면 `message_id`, 방이면 `room_id`(+ 마지막 `message_id`).
    MESSAGE_ARRIVED = "inbox.message_arrived"
    #: 답장(202) 결과 — `local_id`(= 보낸 답장 id)와 `status`(`sent`|`failed`)·`error`.
    REPLY_RESULT = "inbox.reply_result"
    #: 연동·방 상태가 바뀌었다(백필 끝·끊김·되살림·카톡 수집기 상태) — 설정·배너가 다시 읽는다(D-50).
    INTEGRATION_CHANGED = "integration.changed"


@dataclass(frozen=True, slots=True)
class UserEvent:
    """한 회원에게 가는 사건 하나. `member_id` 는 **받는 사람**이다 — WS 는 이것으로 거른다."""

    type: str
    member_id: str
    integration_id: str | None = None
    room_id: str | None = None
    message_id: str | None = None
    source_kind: str | None = None
    #: 사건마다 더 실을 작은 값(`local_id`·`status`·`error`·`count` 등). 본문·토큰은 싣지 않는다.
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> str:
        body: dict[str, Any] = {"v": EVENT_VERSION, "type": self.type, "member_id": self.member_id}
        for key in ("integration_id", "room_id", "message_id", "source_kind"):
            value = getattr(self, key)
            if value is not None:
                body[key] = value
        if self.data:
            body["data"] = self.data
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_payload(cls, payload: str) -> "UserEvent | None":
        """듣는 쪽(BE-3 WS)의 해석. 모르는 판·깨진 JSON 은 `None` — 채널 하나가 WS 를 죽이지 않는다."""
        try:
            body = json.loads(payload)
        except (TypeError, ValueError):
            return None
        if not isinstance(body, dict) or body.get("v") != EVENT_VERSION:
            return None
        if not isinstance(body.get("type"), str) or not isinstance(body.get("member_id"), str):
            return None
        return cls(
            type=body["type"],
            member_id=body["member_id"],
            integration_id=body.get("integration_id"),
            room_id=body.get("room_id"),
            message_id=body.get("message_id"),
            source_kind=body.get("source_kind"),
            data=body.get("data") or {},
        )


def sync_wake_payload(integration_id: str, reason: str) -> str:
    """`SYNC_WAKE_CHANNEL` 페이로드 — `{"v":1,"integration_id":…,"reason":"connected"|"reconnected"|"rooms"}`."""
    return json.dumps(
        {"v": EVENT_VERSION, "integration_id": integration_id, "reason": reason}, separators=(",", ":")
    )
