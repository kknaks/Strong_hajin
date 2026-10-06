"""외부 채널 연동 · 메시지함 (SPEC-008 · WORK-011).

파일 하나 = Phase 하나의 소유. 뒤 Phase 가 **같은 파일을 열지 않고** 나란히 가도록 BE-1 이 갈라 둔다.

| 파일 | 소유 | 무엇 |
|---|---|---|
| `domain.py` | BE-1 | 종류·상태 enum · 오류 · 파생 규칙(카톡 `aid` · 방 `paused` · 고른 방 버전) |
| `events.py` | BE-1 | **NOTIFY 계약** — 채널 이름·페이로드(BE-2 가 내고 BE-3 WS 가 듣는다) |
| `application.py` | BE-1 | 연결(OAuth `state`)·연동 목록·해제·고른 방·기기 토큰·카톡 handshake/reset-account |
| `sync.py` | BE-2 | 연동 수집 — 슬랙 Socket Mode·Gmail watch/pull·백필·메우기·팬아웃 |
| `inbox.py` | BE-3 | 메시지함·읽음·첨부 중계·이미지 프록시·답장 |
| `kakao_ingest.py` | BE-3 | 카톡 수신 — messages·attachments·status |

`domain.py`·`application.py` 는 `fastapi`·`mcp`·`sqlalchemy` 를 모른다(`tests/architecture`).
"""
