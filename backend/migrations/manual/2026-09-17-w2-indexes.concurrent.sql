-- W2 (WORK-002) — 기존 표에 더하는 인덱스 · **운영 적용용 판**
--
-- 언제 쓰나: 실제 데이터가 사는 DB 에 적용할 때. Rollout ③-b 이고 **사람이 실행한다.**
--            `CONCURRENTLY` 는 쓰기를 막지 않는 대신 **트랜잭션 블록 안에서 돌 수 없다.**
--            그래서 `BEGIN` 없이, `psql -1` 없이, autocommit 으로 실행한다:
--
--                psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 2026-09-17-w2-indexes.concurrent.sql
--
--            (`psql` 은 기본이 autocommit 이다. `-1`/`--single-transaction` 을 붙이면 실패한다.)
--
-- 실패했을 때: `CONCURRENTLY` 가 중간에 죽으면 **INVALID 인덱스가 남아 다음 시도를 막는다.**
--            아래로 찾아서 지우고 처음부터 다시 한다 —
--
--                SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
--                 WHERE NOT i.indisvalid;
--                DROP INDEX CONCURRENTLY <그 이름>;
--
-- 되돌리기: `DROP INDEX CONCURRENTLY <이름>;` — 데이터에 영향이 없다.
--
-- **인덱스 정의는 `2026-09-17-w2-indexes.sql`(격리 검증용)과 같아야 한다.**
-- 두 판을 한 파일에 나란히 두지 않는다: 통째로 돌리면 트랜잭션 안에서 `CONCURRENTLY` 가 죽는다.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_work_requests_parent_task_id
    ON work_requests (parent_task_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_work_requests_supersedes_request_id
    ON work_requests (supersedes_request_id);
