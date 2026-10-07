-- WORK-012 Phase WP4-BE — 기존 표 `tasks` 에 더하는 인덱스 · **격리 검증용 판**
--
-- 언제 쓰나: 격리 PostgreSQL(`POSTGRES_TEST_URL`)과 demo DB 에서 검증할 때. 트랜잭션 안에서 돈다(`psql -1 -f`).
-- **운영에는 쓰지 않는다**: 트랜잭션 안의 `CREATE INDEX` 는 `tasks` 쓰기를 막는다. 운영 적용은 같은 정의의
--            `2026-10-07-inbox-message-origin.concurrent.sql` 이고 **사람이** 트랜잭션 밖에서 실행한다.
-- 운영 적용 순서(참고): ① `2026-10-07-inbox-message-origin.sql`(칸 셋) → ② 이미지 → ③ `….concurrent.sql`(인덱스).
-- 왜 파일인가: `schema_sync`(= `make sync-demo-schema`)는 기존 표에 인덱스를 더하지 않는다(BASE-002 O-24) —
--            선례 `2026-09-28-w7-successor-index.sql` 과 같은 모양이다.
-- 무엇을 위한 인덱스인가: 「업무 만듦」 수(`made_task_count` · SPEC-008 §4.8 ④) — 메시지 id 로 업무를 센다. 부분 인덱스.
-- 정의의 SoT 는 모델 metadata 다 — `platform/persistence.py` `TaskRecord.__table_args__` 의 `ix_tasks_source_inbox_message_id`.
-- **인덱스 정의는 `….concurrent.sql`(운영 적용용)과 같아야 한다.**

CREATE INDEX IF NOT EXISTS ix_tasks_source_inbox_message_id ON tasks (source_inbox_message_id)
	WHERE source_inbox_message_id IS NOT NULL;
