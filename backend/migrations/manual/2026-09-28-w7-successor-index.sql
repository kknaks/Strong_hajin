-- W7 (WORK-007) — 기존 표에 더하는 인덱스 · **격리 검증용 판**
--
-- 언제 쓰나: 격리 PostgreSQL(`POSTGRES_TEST_URL`)과 demo DB 에서 검증할 때.
--            트랜잭션 안에서 돈다. `psql -1 -f` 로 통째로 돌려도 된다.
-- 운영에는 쓰지 않는다: 쓰기를 막는다. 운영 적용은 같은 정의의
--            `2026-09-28-w7-successor-index.concurrent.sql` 이고 **사람이** 트랜잭션 밖에서 실행한다.
--
-- 왜 파일인가: 이 저장소에는 alembic 도 migrations 체계도 없다. `schema_sync`(= `make sync-demo-schema`)는
--            **없는 표를 만들고 없는 컬럼을 더할 뿐, 기존 표에 인덱스를 더하지 않는다**(BASE-002 O-24 ·
--            `bootstrap/schema_sync.py:30-33` 이 `CreateIndex` 를 «없는 표» 갈래에서만 낸다).
--            그래서 아래 하나는 자동으로 생기지 않는다. **이 파일이 적용 단위이자 증거다.**
--            이것은 migration 체계가 아니라 손으로 적용하는 .sql 이다.
--            선례가 `2026-09-17-w2-indexes.sql`(W2)이고 같은 모양을 그대로 쓴다.
--
-- 무엇을 위한 인덱스인가: **후행 역방향 조회** (SPEC-007 §4 · WORK-007 Phase B-1).
--            `task_predecessors` 는 `ix_task_predecessors_task_id`(「내 선행」)만 갖고 있어서
--            `WHERE predecessor_task_id IN (...)`(「나를 선행으로 삼는 업무」)가 seq scan 이 된다.
--            `uq_task_predecessors_active` 의 선두 열도 `task_id` 라 그 방향을 받지 못한다.
--
-- 정의의 SoT 는 모델 metadata 다 — `platform/persistence.py` 의
--            `Index("ix_task_predecessors_predecessor_task_id", "predecessor_task_id")`.
--            새로 만드는 DB 는 `create_all` 이 그것으로 만든다.
--
-- 재적용 가능: `IF NOT EXISTS` 라 여러 번 돌려도 실패하지 않는다.
-- 되돌리기: `DROP INDEX ix_task_predecessors_predecessor_task_id;` — 데이터에 영향이 없다.

CREATE INDEX IF NOT EXISTS ix_task_predecessors_predecessor_task_id
    ON task_predecessors (predecessor_task_id);
