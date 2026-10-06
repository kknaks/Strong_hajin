"""카톡 수신 — **BE-3 소유** (SPEC-008 §4.6 · 수집기 = SPEC-009).

여기에 들어올 것: `POST /api/integrations/kakao/messages`(서버 고른 방 아니면 403 · 묶음 500건 · 중복 키
`(연동, chatId, logId)` · `backfill_done` → 방 `live`) · `POST …/kakao/attachments/{aid}`(50MB · hostPath) ·
`POST …/kakao/status`(30초 주기 · 응답 `selected_rooms_version`).

BE-1 이 놓아 둔 것: 기기 토큰 인증(`entrypoints/http_auth.device_principal` — 수집기 라우트만) · handshake(첫
호출이 카톡 연동을 만든다 · `selected_rooms[{room_id, external_id, last_logId}]`) · `domain.kakao_attachment_aid` ·
`domain.effective_room_status` · 연동의 `collector_status`/`collector_reported_at`/`selected_rooms_version` 칸.
reset-account 는 **웹 세션 라우트**다(4차 검수 ★2) — 수집기는 handshake 의 `reset_at` 으로 안다.
"""
