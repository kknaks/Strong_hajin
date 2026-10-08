-- WORK-013 Phase WP2-BE — 기존 메시지 줄의 「내가 보낸 줄」(`external_messages.from_me`) 채우기 · **일회성 수동** (SPEC-011 §4.7 · D-35)
--
-- 언제 쓰나: `2026-10-08-notifications-v2.sql`(칸 추가) 뒤. 카톡 줄은 **새 수집기 dmg 를 먼저 깔고** 돌린다(그 사이 옛 수집기가
--            올린 줄도 한 번에 채운다). 뒤에 표지 없는 줄이 또 남으면 같은 파일을 다시 — `from_me IS NULL` 줄만 바꾼다.
-- 인자(둘 다 **실행 인자**로만 준다 — 이름을 이 파일 · 코드 · 문서 · 커밋에 쓰지 않는다 · P-10):
--            -v integration_id=<카톡 연동 id>   -v self_name=<사용자 본인의 카톡 표시 이름>   -v apply=0|1(기본 0)
-- **두 걸음** — ① 셈(`apply=0`, 기본): 아무것도 바꾸지 않고 출력만. 코디가 보고 맞다고 판단하면 ② 적용(`apply=1`):
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v integration_id=… -v self_name='…' -f 2026-10-08-notification-from-me-backfill.sql
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v integration_id=… -v self_name='…' -v apply=1 -f 2026-10-08-notification-from-me-backfill.sql
--
--            참고값(2026-10-08 운영 · prod-check-1.md): 본인 14건 · 방 3개 · 전체 116건 — **늘어 있는 것이 정상**이다.
-- 무엇을 채우나(전부 `from_me IS NULL` 인 줄만 · 한 트랜잭션):
--   · 카톡 — 그 연동의 줄: `author` = 인자 이름 → true · 다른 이름 → false · 작성자 없음(시스템 줄) → false
--   · 슬랙 — 모든 슬랙 연동: `raw.user` == 그 연동 `account_meta.user_id` → true / 아니면 false (이름 인자 없이 계산된다)
--   · 메일 — 모든 메일 연동: raw 헤더 From 의 주소 == 그 연동 계정 주소(`account_key` · 대소문자 무시) → true / 아니면 false ·
--     From 을 못 찾으면 null 그대로(= 아님 — 받은편지함만 받아 드물다 · I-5)
-- 되돌리기: 칸을 비우면 「내 것 아님」(null)으로 돌아간다 — `UPDATE external_messages SET from_me = NULL WHERE …` (사람이 판단).

\if :{?apply}
\else
\set apply 0
\endif
\if :{?integration_id}
\else
\echo 'integration_id 인자가 필요합니다 (-v integration_id=…)'
\quit
\endif
\if :{?self_name}
\else
\echo 'self_name 인자가 필요합니다 (-v self_name=…)'
\quit
\endif

SELECT (:apply)::int = 1 AS do_apply \gset

\echo '── 카톡 (그 연동) ──'
SELECT
	count(*) FILTER (WHERE m.from_me IS NULL AND m.author = :'self_name') AS becomes_true,
	count(DISTINCT m.room_id) FILTER (WHERE m.from_me IS NULL AND m.author = :'self_name') AS becomes_true_rooms,
	count(*) AS kakao_lines,
	count(*) FILTER (WHERE m.from_me IS NULL) AS unflagged_lines,
	count(DISTINCT m.author) AS author_names,
	-- 같은 이름을 쓰는 «다른 사람» 이 있는가 — 수집기가 작성자 id 를 실었으면 그 종류 수(1 이어야 한다 · 2 이상이면 멈춘다).
	count(DISTINCT m.raw ->> 'authorId') FILTER (WHERE m.author = :'self_name') AS self_author_ids
FROM external_messages AS m
JOIN external_integrations AS i ON i.id = m.integration_id
WHERE i.id = :'integration_id'::uuid AND i.kind = 'kakao';

\echo '── 슬랙 (모든 연동 · raw.user) ──'
SELECT
	count(*) FILTER (WHERE m.from_me IS NULL AND m.raw ->> 'user' = i.account_meta ->> 'user_id') AS becomes_true,
	count(*) FILTER (WHERE m.from_me IS NULL) AS unflagged_lines
FROM external_messages AS m
JOIN external_integrations AS i ON i.id = m.integration_id
WHERE i.kind = 'slack';

\echo '── 메일 (모든 연동 · From) ──'
WITH sender AS (
	SELECT m.id, lower(i.account_key) AS account,
		lower(trim(COALESCE(substring(h ->> 'value' FROM '<([^>]+)>'), h ->> 'value'))) AS address
	FROM external_messages AS m
	JOIN external_integrations AS i ON i.id = m.integration_id
	CROSS JOIN LATERAL json_array_elements(COALESCE(m.raw -> 'payload' -> 'headers', '[]'::json)) AS h
	WHERE i.kind = 'mail' AND m.from_me IS NULL AND lower(h ->> 'name') = 'from'
)
SELECT count(*) FILTER (WHERE address = account) AS becomes_true, count(*) AS with_from FROM sender;

\if :do_apply
BEGIN;
UPDATE external_messages AS m
SET from_me = (m.author IS NOT NULL AND m.author = :'self_name')
FROM external_integrations AS i
WHERE i.id = m.integration_id AND i.id = :'integration_id'::uuid AND i.kind = 'kakao' AND m.from_me IS NULL;

UPDATE external_messages AS m
SET from_me = (m.raw ->> 'user' IS NOT NULL AND m.raw ->> 'user' = i.account_meta ->> 'user_id')
FROM external_integrations AS i
WHERE i.id = m.integration_id AND i.kind = 'slack' AND m.from_me IS NULL;

WITH sender AS (
	SELECT m.id, lower(i.account_key) AS account,
		lower(trim(COALESCE(substring(h ->> 'value' FROM '<([^>]+)>'), h ->> 'value'))) AS address
	FROM external_messages AS m
	JOIN external_integrations AS i ON i.id = m.integration_id
	CROSS JOIN LATERAL json_array_elements(COALESCE(m.raw -> 'payload' -> 'headers', '[]'::json)) AS h
	WHERE i.kind = 'mail' AND m.from_me IS NULL AND lower(h ->> 'name') = 'from'
)
UPDATE external_messages AS m SET from_me = (sender.address = sender.account)
FROM sender WHERE sender.id = m.id;
COMMIT;
\echo '적용했습니다 — from_me 가 빈 줄만 바꿨습니다. 다시 돌려도 안전합니다.'
\else
\echo '셈만 했습니다(apply=0) — 바꾸지 않았습니다. 맞으면 -v apply=1 로 다시 돌리세요.'
\endif
