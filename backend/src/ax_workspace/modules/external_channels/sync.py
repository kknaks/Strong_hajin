"""연동 수집 — **BE-2 소유** (WORK-011 Phase BE-2 · SPEC-008 §5 동기화).

여기에 들어올 것: 슬랙 Socket Mode 이벤트 → 사람별 팬아웃(`(team, channel)` 을 고른 «모든» 연동에 복제 ·
키 `(연동, channel, ts)`) · Gmail `users.watch`(매일 갱신)·Pub/Sub pull·history 증분 · 최초 백필 · 재시작 메우기.

BE-1 이 놓아 둔 것(이 파일 밖):

- 표·동기화 상태 칸 — `platform/persistence.py` 의 `External*Record`(연동 `sync_cursor`·`watch_expires_at`·
  `backfill_*`·`synced_count`·`last_synced_at`·`disconnected_*` / 방 `last_message_key`·`backfill_*`).
  **BE-2 는 이 파일을 다시 열지 않는다.**
- 토큰 복호 — `platform/external_tokens.py` 의 `FernetTokenCipher`.
- 사건 내기 — `modules/external_channels/events.py`(계약) · `platform/user_events.py`(`publish`).
- 깨움 신호 — 연결·재연결·방 추가 때 back 이 `SYNC_WAKE_CHANNEL` 로 낸다(놓쳐도 상태 칸으로 다시 찾는다).

워커 진입점은 `entrypoints/external_worker.py` + `bootstrap/external_worker.py`(신규) — `http.py` 를 건드리지 않는다.
"""
