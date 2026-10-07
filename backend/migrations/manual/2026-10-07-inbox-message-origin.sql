-- WORK-012 Phase WP4-BE — 메시지함 → AX: 업무 출처 메시지 · 참고 자료 한 줄 · **칸 판(운영 적용용)**
--            (SPEC-008 §4.8 ③④ · §2.9 ③-2 · WP4 계약 고정 1)
--
-- 운영 적용 순서 — 셋 다 사람이 한다:
--   ① 이 파일(칸 셋)            psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f 2026-10-07-inbox-message-origin.sql
--   ② 새 이미지 배포             (이 칸들을 읽고 쓴다 — ① 없이 올리면 업무 상세·대화 상세·메시지함 방 메시지가 500 이다)
--   ③ 인덱스(쓰기를 막지 않음)    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 2026-10-07-inbox-message-origin.concurrent.sql
--      ③ 은 ② 뒤 언제든 된다 — 인덱스는 「업무 만듦」 셈을 빠르게 할 뿐이고, 없어도 기능은 돈다.
--   `2026-10-07-inbox-message-origin-index.sql` 은 **격리 검증용**이다 — 운영에는 쓰지 않는다.
--
-- 무엇: nullable 칸 셋(additive · 기존 행은 NULL = 「메시지에서 오지 않았다」 / 「조합 전」)
--            ① `tasks.source_inbox_message_id`  ② `work_requests.source_inbox_message_id`
--            ③ `conversation_context_references.label`
-- 트랜잭션 안전: 기본값 없는 nullable `ADD COLUMN` 은 메타데이터만 바꾼다(테이블 재작성 · 긴 잠금 없음). `-1` 로 통째로.
--            **인덱스는 이 파일에 두지 않는다** — `tasks` 에 트랜잭션 안의 `CREATE INDEX` 는 쓰기를 막는다(검수 W-2).
-- 로컬 demo DB 는 `make sync-demo-schema`(schema_sync, additive)가 같은 칸 셋을 만든다(인덱스는 기존 표라 만들지 않는다).
-- 외래 키를 걸지 않는다: 메시지는 메시지함 모듈의 것이고 나중에 소프트 딜리트돼도 출처 사실은 남는다(S8 §4.8 ③).
-- 정의의 SoT 는 모델 metadata 다 — `platform/persistence.py` 의 `TaskRecord` · `WorkRequestRecord` · `ContextReferenceRecord`.
-- 재적용 가능: 전부 `IF NOT EXISTS`.
-- 되돌리기: 이미지 롤백만 한다 — 칸은 additive 라 남겨 둔다. 정말 걷어야 하면 사람이(인덱스를 먼저)
--            `DROP INDEX CONCURRENTLY ix_tasks_source_inbox_message_id;` 뒤
--            `ALTER TABLE tasks DROP COLUMN source_inbox_message_id; ALTER TABLE work_requests DROP COLUMN source_inbox_message_id;
--             ALTER TABLE conversation_context_references DROP COLUMN label;`.

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_inbox_message_id UUID;
ALTER TABLE work_requests ADD COLUMN IF NOT EXISTS source_inbox_message_id UUID;
ALTER TABLE conversation_context_references ADD COLUMN IF NOT EXISTS label VARCHAR(300);
