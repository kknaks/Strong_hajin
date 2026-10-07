-- WORK-012 Phase WP2-BE — 회의록 용어 보정 표 (SPEC-010 §4.7 · DEC-009 D-16·D-17·D-18 · 코디 판정 N-1)
--
-- 언제 쓰나: 운영 DB 에 **이미지보다 먼저** 적용한다 — 새 이미지는 최종 합성 적재와 회의 상세 응답에서 이 표와
--            `meetings.term_corrected_at` 을 읽고 쓴다(모르는 DB 에 올리면 회의 상세가 500 이다). 로컬 demo DB 는
--            `make sync-demo-schema`(schema_sync, additive)가 같은 정의로 만든다 — 이 파일은 그 정의를 PostgreSQL 글자로 고정한 것이다.
-- 무엇: ① 새 표 `meeting_term_corrections` + 인덱스 `(meeting_id, order_index)` ② 기존 표 `meetings` 에 nullable 칸
--            `term_corrected_at` 하나(additive — 기존 회의는 NULL = 「정정이 돌지 않았다」 → 응답 `term_corrections: null`).
-- 왜 `.concurrent.sql` 이 없나: 인덱스는 **새 표**에만 건다(빈 표라 막을 쓰기가 없다). `meetings` 에는 nullable 칸 하나라
--            기본값 없는 `ADD COLUMN` 이 메타데이터만 바꾼다(테이블 재작성 없음). `psql -1 -f` 로 통째로 돌린다.
-- 정의의 SoT 는 모델 metadata 다 — `platform/persistence.py` 의 `MeetingTermCorrectionRecord` · `MeetingRecord.term_corrected_at`.
-- 재적용 가능: 전부 `IF NOT EXISTS`.
-- 되돌리기: 이미지 롤백만 한다 — 표·칸은 additive 라 남겨 둔다. 정말 걷어야 하면 사람이
--            `DROP TABLE meeting_term_corrections; ALTER TABLE meetings DROP COLUMN term_corrected_at;`.

CREATE TABLE IF NOT EXISTS meeting_term_corrections (
	id UUID NOT NULL,
	meeting_id UUID NOT NULL,
	order_index INTEGER NOT NULL,
	heard VARCHAR(100) NOT NULL,
	corrected VARCHAR(100) NOT NULL,
	grade VARCHAR(10) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(meeting_id) REFERENCES meetings (id)
);
CREATE INDEX IF NOT EXISTS ix_meeting_term_corrections_meeting_order ON meeting_term_corrections (meeting_id, order_index);

ALTER TABLE meetings ADD COLUMN IF NOT EXISTS term_corrected_at TIMESTAMP WITH TIME ZONE;
