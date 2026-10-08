-- WORK-013 Phase WP1-BE — 알림 표 `notifications` 의 **사건 순번** · 고친 시각 (SPEC-011 §4.1-3 · WORK-013 Domain/Schema)
--
-- 언제 쓰나: 운영 DB 에 **이미지보다 먼저** 적용한다 — 새 이미지는 알림을 쓸 때 `nextval('notification_seq')` 를 부르고
--            이어 받기가 `seq` · `updated_at` 을 읽는다. 시퀀스·칸이 없는 DB 에 새 이미지를 올리면 업무 요청 발송이 500 이다.
--            트랜잭션 안에서 통째로 돈다: `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f 2026-10-08-notifications-v2.sql`
-- 운영 적용 순서: ① **이 파일**(표 · 칸 · 시퀀스 · 옛 행 채우기) → ② 새 이미지(back + 워커 다섯) →
--                ③ `2026-10-08-notifications-v2.concurrent.sql`(인덱스 — 트랜잭션 밖 · 사람이).
--            **② 이미지 반영 뒤 이 파일을 한 번 더 돌린다** — ①과 ② 사이에 옛 이미지가 쓴 알림 행은 `seq` · `updated_at` 이
--            비어 있다(옛 코드는 그 칸을 모른다). 비어 있으면 이어 받기 · SSE 밖에 남으므로 다시 돌려 채운다. 채우기는
--            빈 행만 건드리고 나머지는 `IF NOT EXISTS` 라 **다시 돌려도 안전**하다.
--            격리 PostgreSQL · demo DB 는 ③ 대신 `2026-10-08-notifications-v2-index.sql`(같은 정의 · 트랜잭션 안).
-- 왜 표 만들기가 있나: `notifications` 는 지금까지 **마이그레이션 파일이 없던 표**다(ORM `create_all` 로만 섰다 — BE 조사 A-1).
--            운영에 이미 있으면 `IF NOT EXISTS` 가 건너뛰고, 없으면 지금 모델 그대로 선다. 정의의 SoT 는 모델 metadata
--            (`platform/persistence.py` `NotificationRecord` · `NOTIFICATION_SEQ`)이고, 이 파일은 그것을 postgresql dialect 로
--            컴파일한 글자다. 로컬 demo DB 는 `make sync-demo-schema`(schema_sync — 칸 · 시퀀스를 더한다)가 같은 일을 한다.
-- 옛 행: `seq` 를 `created_at` 순으로 채우고 `updated_at` 은 `created_at` 으로 — 이어 받기가 옛 행도 기준 줄로 쓸 수 있다.
-- 재적용 가능: 전부 `IF NOT EXISTS` · 채우기는 비어 있는 행만.
-- 되돌리기: 이미지 롤백만 한다 — 칸 · 시퀀스는 additive 라 남긴다(옛 이미지는 모른 채 돈다 · WORK-013 Rollback).
-- WP2-BE(SPEC-011 §4.2 · §4.4 · §4.5 · SPEC-008 v0.7.0 §4.4) 가 같은 파일에 덧붙였다 — 알림 칸 여덟 · 설정 표 ·
--            `external_messages.from_me` · 옛 종류 두 개 바꾸기. 운영 적용 전 한 파일이다.
--            `from_me` 의 기존 줄 채우기는 이 파일이 아니라 `2026-10-08-notification-from-me-backfill.sql`(두 걸음 · 인자) 다.

CREATE TABLE IF NOT EXISTS notifications (
	id UUID NOT NULL,
	recipient_member_id VARCHAR(100) NOT NULL,
	source_kind VARCHAR(60) NOT NULL,
	source_id VARCHAR(100) NOT NULL,
	kind VARCHAR(80) NOT NULL,
	resource_type VARCHAR(40) NOT NULL,
	resource_id VARCHAR(100) NOT NULL,
	resource_version INTEGER,
	resource_title VARCHAR(300) NOT NULL,
	actor_member_id VARCHAR(100) NOT NULL,
	safe_summary VARCHAR(300) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	read_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_recipient_source UNIQUE (recipient_member_id, source_kind, source_id),
	FOREIGN KEY(recipient_member_id) REFERENCES members (id),
	FOREIGN KEY(actor_member_id) REFERENCES members (id)
);
CREATE INDEX IF NOT EXISTS ix_notifications_recipient_created ON notifications (recipient_member_id, created_at);

CREATE SEQUENCE IF NOT EXISTS notification_seq;

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS seq BIGINT;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE;

-- 옛 행 채우기 — 만든 순서대로 순번을 받는다(같은 시각이면 id 순).
UPDATE notifications AS n
SET seq = numbered.next_seq
FROM (
	SELECT id, nextval('notification_seq') AS next_seq
	FROM (SELECT id FROM notifications WHERE seq IS NULL ORDER BY created_at, id) AS ordered
) AS numbered
WHERE n.id = numbered.id;
UPDATE notifications SET updated_at = created_at WHERE updated_at IS NULL;

-- ── WP2-BE: 알림 칸 · 설정 · from_me ───────────────────────────────────────────────────

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS theme VARCHAR(20);
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS item VARCHAR(30);
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS relation VARCHAR(30);
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS failure BOOLEAN;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS actor JSON;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS data JSON;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS target JSON;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS coalesce_key VARCHAR(200);

CREATE TABLE IF NOT EXISTS notification_settings (
	member_id VARCHAR(100) NOT NULL,
	settings JSON NOT NULL,
	version INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (member_id),
	FOREIGN KEY(member_id) REFERENCES members (id)
);

ALTER TABLE external_messages ADD COLUMN IF NOT EXISTS from_me BOOLEAN;

-- 옛 종류 두 개(D-30 흡수) — 새 종류 · 테마 · 항목 · 꼬리표 · 값 · 대상 · 행위자. 테마가 빈 행만(다시 돌려도 안전).
UPDATE notifications AS n
SET kind = 'work.request_received', theme = 'work', item = 'request', relation = 'assignee', failure = false,
	data = '{"resubmitted": false}'::json,
	target = json_build_object('surface', 'work', 'work_request_id', n.resource_id),
	actor = json_build_object('member_id', n.actor_member_id, 'display_name', COALESCE(m.display_name, n.actor_member_id))
FROM members AS m
WHERE n.kind = 'work_request.received' AND n.theme IS NULL AND m.id = n.actor_member_id;
UPDATE notifications AS n
SET kind = 'work.request_answered', theme = 'work', item = 'answer', relation = 'requester', failure = false,
	data = '{"answer": "accepted"}'::json,
	target = json_build_object('surface', 'work', 'work_request_id', n.resource_id),
	actor = json_build_object('member_id', n.actor_member_id, 'display_name', COALESCE(m.display_name, n.actor_member_id))
FROM members AS m
WHERE n.kind = 'work_request.accepted' AND n.theme IS NULL AND m.id = n.actor_member_id;

