"""메시지함·읽음·첨부 중계·이미지 프록시·답장 — **BE-3 소유** (WORK-011 Phase BE-3 · SPEC-008 §4.4).

여기에 들어올 것: `GET /api/inbox/messages` 목록(출처·미읽음·cursor) · 메일 본문(소독 안전본) · 방 메시지
페이지네이션 · 읽음(`external_read_states`) · 첨부 중계(메일·슬랙 = 회원 토큰으로 · 카톡 = 저장본) ·
`remote-image`(SSRF 규칙) · 답장(슬랙·메일, `external_sent_replies` · `Idempotency-Key`) · 사용자 WS 가 듣는
`events.USER_EVENTS_CHANNEL`.

소유 검사는 BE-1 의 규칙을 그대로 쓴다 — 연동·방·메시지는 연결한 회원 것, 남의 것은 `ResourceNotFound`(404).
"""
