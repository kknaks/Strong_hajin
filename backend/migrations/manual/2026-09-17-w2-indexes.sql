-- W2 (WORK-002) — 기존 표에 더하는 인덱스 · **격리 검증용 판**
--
-- 언제 쓰나: 격리 PostgreSQL(`POSTGRES_TEST_URL`)과 demo DB 에서 검증할 때.
--            트랜잭션 안에서 돈다. `psql -1 -f` 로 통째로 돌려도 된다.
-- 운영에는 쓰지 않는다: 쓰기를 막는다. 운영 적용은 같은 정의의
--            `2026-09-17-w2-indexes.concurrent.sql` 이고 **사람이** 트랜잭션 밖에서 실행한다.
--
-- 왜 파일인가: 이 저장소에는 alembic 도 migrations 체계도 없다. `schema_sync`(= `make sync-demo-schema`)는
--            **없는 표를 만들고 없는 컬럼을 더할 뿐, 기존 표에 인덱스를 더하지 않는다**(BASE-002 O-24).
--            그래서 아래 둘은 자동으로 생기지 않는다. **이 파일이 적용 단위이자 증거다.**
--            이것은 migration 체계가 아니라 손으로 적용하는 .sql 이다.
--
-- 새 표(`task_proposals` · `work_request_list_entries`)의 인덱스는 여기 없다 —
-- 새 표는 `--sync` 가 인덱스까지 함께 만든다. `tasks.parent_task_id` 는 이미 있다.
--
-- 재적용 가능: `IF NOT EXISTS` 라 여러 번 돌려도 실패하지 않는다.

CREATE INDEX IF NOT EXISTS ix_work_requests_parent_task_id
    ON work_requests (parent_task_id);

CREATE INDEX IF NOT EXISTS ix_work_requests_supersedes_request_id
    ON work_requests (supersedes_request_id);
