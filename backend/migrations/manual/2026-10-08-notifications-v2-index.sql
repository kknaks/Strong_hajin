-- WORK-013 Phase WP1-BE — 기존 표 `notifications` 에 더하는 인덱스 · **격리 검증용 판**
--
-- 언제 쓰나: 격리 PostgreSQL(`POSTGRES_TEST_URL`)과 demo DB 에서 검증할 때. 트랜잭션 안에서 돈다(`psql -1 -f`).
-- **운영에는 쓰지 않는다**: 트랜잭션 안의 `CREATE INDEX` 는 `notifications` 쓰기를 막는다. 운영 적용은 같은 정의의
--            `2026-10-08-notifications-v2.concurrent.sql` 이고 **사람이** 트랜잭션 밖에서 실행한다.
-- 운영 적용 순서(참고): ① `2026-10-08-notifications-v2.sql`(칸 · 시퀀스) → ② 이미지 → ③ `….concurrent.sql`(인덱스).
-- 왜 파일인가: `schema_sync`(= `make sync-demo-schema`)는 기존 표에 인덱스를 더하지 않는다(BASE-002 O-24).
-- 무엇을 위한 인덱스인가: 이어 받기(SPEC-011 §4.1-3) — 그 회원의 알림을 사건 순번으로 찾고 순번 순으로 읽는다.
-- 정의의 SoT 는 모델 metadata 다 — `platform/persistence.py` `NotificationRecord.__table_args__` 의 `ix_notifications_recipient_seq`.
-- **인덱스 정의는 `….concurrent.sql`(운영 적용용)과 같아야 한다.**

CREATE INDEX IF NOT EXISTS ix_notifications_recipient_seq ON notifications (recipient_member_id, seq);
