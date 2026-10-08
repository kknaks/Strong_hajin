-- WORK-013 Phase WP1-BE — 기존 표 `notifications` 에 더하는 인덱스 · **운영 적용용 판**
--
-- 운영 적용 순서: ① `2026-10-08-notifications-v2.sql`(칸 · 시퀀스 · 트랜잭션) → ② 새 이미지 → ③ **이 파일**(인덱스).
-- 언제 쓰나: 실제 데이터가 사는 DB 에 적용할 때. **사람이 실행한다.** ① 의 칸이 먼저 있어야 한다.
--            `CONCURRENTLY` 는 쓰기를 막지 않는 대신 **트랜잭션 블록 안에서 돌 수 없다** — `BEGIN` 없이, `psql -1` 없이,
--            autocommit 으로:
--
--                psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 2026-10-08-notifications-v2.concurrent.sql
--
-- 실패했을 때: 중간에 죽으면 INVALID 인덱스가 남아 다음 시도를 막는다 — 찾아서 지우고 다시 한다:
--                SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE NOT i.indisvalid;
--                DROP INDEX CONCURRENTLY ix_notifications_recipient_seq;
-- 되돌리기: `DROP INDEX CONCURRENTLY ix_notifications_recipient_seq;` — 데이터에 영향이 없다.
--
-- **인덱스 정의는 `2026-10-08-notifications-v2-index.sql`(격리 검증용)과 같아야 한다.**

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_notifications_recipient_seq ON notifications (recipient_member_id, seq);
