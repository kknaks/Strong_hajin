-- WORK-011 Phase BE-1 — 외부 채널 연동의 새 표 아홉 (SPEC-008 §4.1 · DEC-008 D-24~D-27·D-46)
--
-- 언제 쓰나: 운영 DB 에 **이미지보다 먼저** 적용한다(4차 검수 ★4 — 새 표를 모르는 DB 에 새 이미지를 올리면
--            연동 라우트가 500 이다). 로컬 demo DB 는 `make sync-demo-schema`(schema_sync, additive)가 같은 정의로
--            만든다 — 이 파일은 그 정의를 PostgreSQL 글자로 고정한 것이다.
-- 왜 `.concurrent.sql` 이 없나: 전부 **새 표**다. 빈 표에 거는 인덱스는 막을 쓰기가 없으므로 같은 트랜잭션에서
--            만든다. `psql -1 -f` 로 통째로 돌린다. 기존 표에는 아무것도 더하지 않는다.
-- 정의의 SoT 는 모델 metadata 다 — `platform/persistence.py` 의 `External*Record`·`ProfileImageRecord`.
--            이 파일은 `CreateTable`/`CreateIndex` 를 postgresql dialect 로 컴파일한 결과다.
-- 재적용 가능: 전부 `IF NOT EXISTS`.
-- **개정(2026-10-08 · WORK-013 WP2-BE)**: `external_messages.from_me`(nullable bool · 「내가 보낸 줄」 · SPEC-011 §4.3-6)를
--            이 정의에 더했다 — 이 파일은 **처음 까는 DB 의 모양**이고 모델 metadata 와 같아야 한다
--            (`tests/architecture/test_external_channel_schema.py`). **이미 이 표가 있는 운영 DB 에는 이 파일이 아무것도 더하지
--            않는다**(`CREATE TABLE IF NOT EXISTS` 가 건너뛴다) — 그 칸은 `2026-10-08-notifications-v2.sql` 의
--            `ALTER TABLE external_messages ADD COLUMN IF NOT EXISTS from_me BOOLEAN` 이 더한다. 두 파일을 어느 순서로 돌려도 같다.
-- 되돌리기: 이미지 롤백만 한다 — 표는 additive 라 남겨 둔다(보존·파기 없음, OQ-805). 정말 걷어야 하면
--            `DROP TABLE` 을 FK 역순(profile_images … external_integrations)으로 사람이 한다.

CREATE TABLE IF NOT EXISTS external_integrations (
	id UUID NOT NULL,
	member_id VARCHAR(100) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	status VARCHAR(20) NOT NULL,
	account_key VARCHAR(320) NOT NULL,
	display_name VARCHAR(320) NOT NULL,
	account_meta JSON NOT NULL,
	access_token_encrypted TEXT,
	refresh_token_encrypted TEXT,
	token_expires_at TIMESTAMP WITH TIME ZONE,
	scopes VARCHAR(1000) NOT NULL,
	sync_cursor VARCHAR(200),
	watch_expires_at TIMESTAMP WITH TIME ZONE,
	backfill_count INTEGER DEFAULT 0 NOT NULL,
	backfill_cursor VARCHAR(500),
	backfill_done_at TIMESTAMP WITH TIME ZONE,
	synced_count INTEGER DEFAULT 0 NOT NULL,
	last_synced_at TIMESTAMP WITH TIME ZONE,
	disconnected_reason VARCHAR(40),
	disconnected_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR(500),
	selected_rooms_version INTEGER DEFAULT 0 NOT NULL,
	account_reset_at TIMESTAMP WITH TIME ZONE,
	collector_status JSON,
	collector_reported_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	removed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_integrations_member_account UNIQUE (member_id, kind, account_key),
	CONSTRAINT ck_external_integrations_kind CHECK (kind IN ('mail', 'slack', 'kakao')),
	CONSTRAINT ck_external_integrations_status CHECK (status IN ('connected', 'backfilling', 'disconnected', 'removed')),
	FOREIGN KEY(member_id) REFERENCES members (id)
);

CREATE INDEX IF NOT EXISTS ix_external_integrations_kind_account_key ON external_integrations (kind, account_key);

CREATE INDEX IF NOT EXISTS ix_external_integrations_member_id ON external_integrations (member_id);

CREATE TABLE IF NOT EXISTS external_rooms (
	id UUID NOT NULL,
	integration_id UUID NOT NULL,
	external_id VARCHAR(100) NOT NULL,
	room_type VARCHAR(20) NOT NULL,
	name VARCHAR(300) NOT NULL,
	member_count INTEGER,
	room_meta JSON NOT NULL,
	status VARCHAR(20) NOT NULL,
	last_message_key VARCHAR(64),
	backfill_cursor VARCHAR(500),
	backfill_count INTEGER DEFAULT 0 NOT NULL,
	backfill_done_at TIMESTAMP WITH TIME ZONE,
	synced_count INTEGER DEFAULT 0 NOT NULL,
	last_synced_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	removed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_rooms_integration_external UNIQUE (integration_id, external_id),
	CONSTRAINT ck_external_rooms_type CHECK (room_type IN ('channel', 'private', 'dm', 'group_dm', 'direct', 'group')),
	CONSTRAINT ck_external_rooms_status CHECK (status IN ('backfilling', 'live', 'paused')),
	FOREIGN KEY(integration_id) REFERENCES external_integrations (id)
);

CREATE INDEX IF NOT EXISTS ix_external_rooms_external_id ON external_rooms (external_id);

CREATE INDEX IF NOT EXISTS ix_external_rooms_integration_id ON external_rooms (integration_id);

CREATE TABLE IF NOT EXISTS external_messages (
	id UUID NOT NULL,
	integration_id UUID NOT NULL,
	room_id UUID,
	source_kind VARCHAR(10) NOT NULL,
	container_key VARCHAR(100) NOT NULL,
	external_key VARCHAR(200) NOT NULL,
	thread_key VARCHAR(200),
	sent_at TIMESTAMP WITH TIME ZONE NOT NULL,
	subject VARCHAR(1000),
	author VARCHAR(500),
	preview TEXT,
	raw JSON NOT NULL,
	from_me BOOLEAN,
	safe_html TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_messages_dedup UNIQUE (integration_id, container_key, external_key),
	CONSTRAINT ck_external_messages_source_kind CHECK (source_kind IN ('mail', 'slack', 'kakao')),
	FOREIGN KEY(integration_id) REFERENCES external_integrations (id),
	FOREIGN KEY(room_id) REFERENCES external_rooms (id)
);

CREATE INDEX IF NOT EXISTS ix_external_messages_integration_sent_at ON external_messages (integration_id, sent_at);

CREATE INDEX IF NOT EXISTS ix_external_messages_room_sent_at ON external_messages (room_id, sent_at);

CREATE INDEX IF NOT EXISTS ix_external_messages_room_thread ON external_messages (room_id, thread_key);

CREATE TABLE IF NOT EXISTS external_attachments (
	id UUID NOT NULL,
	message_id UUID NOT NULL,
	aid VARCHAR(128) NOT NULL,
	seq INTEGER NOT NULL,
	kind VARCHAR(10) NOT NULL,
	state VARCHAR(20) NOT NULL,
	name VARCHAR(500) NOT NULL,
	size BIGINT,
	mime VARCHAR(200),
	external_ref TEXT,
	storage_key VARCHAR(300),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	stored_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_attachments_message_aid UNIQUE (message_id, aid),
	CONSTRAINT ck_external_attachments_kind CHECK (kind IN ('image', 'album', 'file', 'video', 'audio', 'sticker')),
	CONSTRAINT ck_external_attachments_state CHECK (state IN ('reference', 'pending', 'stored', 'expired', 'too_large', 'not_stored')),
	FOREIGN KEY(message_id) REFERENCES external_messages (id)
);

CREATE INDEX IF NOT EXISTS ix_external_attachments_aid ON external_attachments (aid);

CREATE TABLE IF NOT EXISTS external_read_states (
	id UUID NOT NULL,
	member_id VARCHAR(100) NOT NULL,
	room_id UUID,
	message_id UUID,
	read_up_to_key VARCHAR(64),
	read_up_to_at TIMESTAMP WITH TIME ZONE,
	read_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_read_states_member_room UNIQUE (member_id, room_id),
	CONSTRAINT uq_external_read_states_member_message UNIQUE (member_id, message_id),
	CONSTRAINT ck_external_read_states_one_target CHECK ((room_id IS NOT NULL AND message_id IS NULL) OR (room_id IS NULL AND message_id IS NOT NULL)),
	FOREIGN KEY(member_id) REFERENCES members (id),
	FOREIGN KEY(room_id) REFERENCES external_rooms (id),
	FOREIGN KEY(message_id) REFERENCES external_messages (id)
);

CREATE TABLE IF NOT EXISTS external_sent_replies (
	id UUID NOT NULL,
	member_id VARCHAR(100) NOT NULL,
	integration_id UUID NOT NULL,
	room_id UUID,
	message_id UUID,
	source_kind VARCHAR(10) NOT NULL,
	idempotency_key VARCHAR(200),
	status VARCHAR(20) NOT NULL,
	payload JSON NOT NULL,
	external_ref VARCHAR(200),
	error VARCHAR(500),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	sent_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_sent_replies_member_key UNIQUE (member_id, idempotency_key),
	CONSTRAINT ck_external_sent_replies_status CHECK (status IN ('sending', 'sent', 'failed')),
	FOREIGN KEY(member_id) REFERENCES members (id),
	FOREIGN KEY(integration_id) REFERENCES external_integrations (id),
	FOREIGN KEY(room_id) REFERENCES external_rooms (id),
	FOREIGN KEY(message_id) REFERENCES external_messages (id)
);

CREATE INDEX IF NOT EXISTS ix_external_sent_replies_integration_id ON external_sent_replies (integration_id);

CREATE INDEX IF NOT EXISTS ix_external_sent_replies_message_id ON external_sent_replies (message_id);

CREATE TABLE IF NOT EXISTS external_device_tokens (
	id UUID NOT NULL,
	member_id VARCHAR(100) NOT NULL,
	device_name VARCHAR(200) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	revoked_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_device_tokens_hash UNIQUE (token_hash),
	FOREIGN KEY(member_id) REFERENCES members (id)
);

CREATE INDEX IF NOT EXISTS ix_external_device_tokens_member_id ON external_device_tokens (member_id);

CREATE TABLE IF NOT EXISTS external_oauth_states (
	id UUID NOT NULL,
	state_hash VARCHAR(64) NOT NULL,
	member_id VARCHAR(100) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	integration_id UUID,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_external_oauth_states_hash UNIQUE (state_hash),
	CONSTRAINT ck_external_oauth_states_kind CHECK (kind IN ('mail', 'slack')),
	FOREIGN KEY(member_id) REFERENCES members (id),
	FOREIGN KEY(integration_id) REFERENCES external_integrations (id)
);

CREATE TABLE IF NOT EXISTS profile_images (
	member_id VARCHAR(100) NOT NULL,
	storage_key VARCHAR(300) NOT NULL,
	content_type VARCHAR(100) NOT NULL,
	size INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (member_id),
	FOREIGN KEY(member_id) REFERENCES members (id)
);
