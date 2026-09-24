DATABASE_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_demo
SONIOX_ENV_FILE ?= $(HOME)/.config/soniox/env
# Load the Soniox key from the same file the vault's transcribe skill uses, without ever printing it. Absent file =
# the feature reports itself unavailable; it never falls back to a stub that pretends to transcribe.
SONIOX_ENV = set -a; [ -f "$(SONIOX_ENV_FILE)" ] && . "$(SONIOX_ENV_FILE)"; set +a;
THECONNECT_ENV_FILE ?= $(HOME)/.config/theconnect/env
# 사옥 회의실 예약 시스템(THE CONNECT) 계정 — Soniox 와 같은 결로, 값은 어디에도 찍지 않는다.
# 파일이 없으면 예약 기능이 스스로 없다고 말한다: 회의는 그대로 서고 회의실만 잡히지 않는다.
THECONNECT_ENV = set -a; [ -f "$(THECONNECT_ENV_FILE)" ] && . "$(THECONNECT_ENV_FILE)"; set +a;
POSTGRES_TEST_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_test
# Acceptance는 자기 데이터베이스에서 돈다. reset으로 시작하는 suite가 사람이 쓰던 DATABASE_URL의
# 조직·자료를 지우지 않게 한다.
ACCEPTANCE_DATABASE_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_test_acceptance
# Use 0 when ACCEPTANCE_DATABASE_URL points at an already managed PostgreSQL server.
ACCEPTANCE_MANAGE_POSTGRES ?= 1
E2E_API_PORT ?= 8001
E2E_FRONTEND_PORT ?= 5176
# 기본은 이 기기에서만 연다. 내부망 다른 기기에서 접근하려면 0.0.0.0으로 띄운다 — 그만큼 이 기기의
# 방화벽/네트워크가 허용하는 누구나 닿을 수 있다는 뜻이므로 신뢰된 내부망에서만 그렇게 연다.
E2E_API_HOST ?= 127.0.0.1
E2E_FRONTEND_HOST ?= 127.0.0.1
ACCEPTANCE_API_PORT ?= 18111
ACCEPTANCE_FRONTEND_PORT ?= 15186
PROTECTED_IMAGE ?= scax-protected:test
PROTECTED_PLATFORM ?= linux/amd64
PROTECTED_PYTHON_VERSION ?= 3.13.15
PROTECTED_PYTHON_BASE ?= python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
PROTECTED_CODEX_BASE ?= node:22.18.0-bookworm-slim@sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e
PROTECTED_RUNTIME_BASE ?= debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171
PROTECTED_EXPECT_CONSTANTS ?= visible

.PHONY: install test test-unit test-contract test-contract-serial test-serial test-scale test-release test-postgres frontend-test frontend-build frontend-assets shell-verify shell-verify-strict shell-build-fixture shell-final-preflight shell-release-preflight tauri-local verify protected-build protected-inspect postgres-up postgres-down reset-demo reset-catalog sync-demo-schema dataset-import dataset-inspect api conversation-worker material-worker meeting-worker mcp frontend-install frontend storybook storybook-build api-e2e frontend-e2e e2e-task-lifecycle e2e-task-checklist e2e-task-history e2e-task-reference e2e-calendar-tasks e2e-task-delivery e2e-chat-checklist e2e-task-detail-layout e2e-task-origin e2e-work-request e2e-work-relations e2e-action-item e2e-conversation e2e-conversation-action e2e-chat-lifecycle e2e-chat-approval e2e-ax-editable-task e2e-ax-editable-meeting e2e-ax-meeting-draft e2e-assistant-character e2e-assistant-preference e2e-follow-up-continuation e2e-ax-action-draft e2e-ax-action-materials e2e-conversation-report-edit-action e2e-daily-report e2e-material-search e2e-meeting-live-transcript e2e-meeting-three-tracks e2e-access-roles e2e-project-participation-history e2e-graph-question local-stack acceptance-e2e live-report-smoke soniox-smoke

install:
	cd backend && uv sync --all-groups

# ── 자기 안에서 동시성을 만드는 테스트는 직렬 패스에서 돈다 ─────────────────────────────
# **기준 한 문장**: 흔들리는 부류는 「**한 테스트가 자기 안에서 진짜 동시성을 만들고**
# (자식 **프로세스** — 자료·보고서 워커의 `IsolatedWork` spawn · MCP `stdio_client` · 직접 부른
# `subprocess` — **또는** 자기가 직접 띄운 **스레드**) **그 진행을 초 단위 실시간 창으로 재는**」 테스트다.
# `-n auto`(= 코어 수 11) 가 되면 「11 xdist 워커 × 각자가 만든 동시성」이 그 창보다 큰 스케줄 지터를
# 만든다 — 계약이 틀린 것이 아니라 **잰 창을 놓친 것**이다(단독·`-n0` 로는 통과한다).
# **프로세스냐 스레드냐는 원인의 본질이 아니다**: 기준이 「프로세스」로만 적혀 있던 동안 `Event` 로
# 5초 창을 재던 회의실 계약이 마커 없이 새서 `make verify` 두 회차를 다 깼다.
# 근거와 버린 가설: orchestration/work/strong-hajin-projects/phase0-report.md ·
# phase0-followup-report.md · phase0-fix3-report.md.
#
# **그래서 파일 이름을 세지 않는다.** 그 부류는 `@pytest.mark.serial` 을 달고 여기서 `-m` 으로 가른다:
# 새 테스트는 마커만 달면 자동으로 옳은 쪽에 서고, 마커 없이 자식 프로세스를 띄우거나 테스트 코드가
# 직접 스레드를 띄우면 `tests/conftest.py` 의 걸개가 병렬 패스에서 **즉시·결정적으로** 실패시킨다.
# `-m` 은 pyproject 의 `addopts` 를 덮으므로 기본 제외(integration·release·scale)를 여기서 다시 적는다.
DEFAULT_DESELECT = not integration and not release and not scale
PARALLEL_MARKERS = $(DEFAULT_DESELECT) and not serial
SERIAL_MARKERS = serial and $(DEFAULT_DESELECT)

test:
	cd backend && uv run pytest -n auto --dist worksteal -m "$(PARALLEL_MARKERS)"
	$(MAKE) test-serial

test-unit:
	cd backend && uv run pytest tests/unit tests/architecture

test-contract:
	cd backend && uv run pytest tests/contract -n auto --dist worksteal -m "$(PARALLEL_MARKERS)"
	$(MAKE) test-serial SERIAL_PATHS=tests/contract

# 마커가 달린 것만 직렬로. `test`·`test-contract` 가 이어서 부르므로 따로 부를 일은 재측정뿐이다.
# `PYTEST_ADDOPTS='-k "..."' make test-contract`(docs/demo-work-seed.md) 처럼 필터를 걸면 이 패스가
# **한 건도 못 고를 수** 있다. pytest 는 그때 5 로 끝나는데, 그것은 실패가 아니라 «고를 것이 없었다» 다 —
# 필터 하나가 초록을 빨갛게 만들지 않게 5 만 삼킨다. 마커가 실제로 갈라지는지는
# `tests/architecture/test_serial_test_targets.py` 가 따로 지킨다.
SERIAL_PATHS ?=
test-serial:
	cd backend && uv run pytest $(SERIAL_PATHS) -m "$(SERIAL_MARKERS)" -n0 || { status=$$?; [ $$status -eq 5 ] || exit $$status; \
		echo "test-serial: 필터가 이 부류를 모두 걸렀다 — 이 패스는 건너뛴다" >&2; }

# 고른 파일만 **직렬로** 다시 돌리는 자리. `-n0` 하나가 실효 스위치다 — `-n auto` 를 끈다.
# **격리 수단이 아니다**: 어떤 부류를 갈라 도는 타겟이 아니라 재실행 편의다 (가르는 것은 `test-serial`).
# `FILES` 로 받은 것만 돈다. 예: make test-contract-serial FILES="tests/contract/test_projects.py"
test-contract-serial:
	cd backend && uv run pytest $(FILES) -n0

test-scale:
	cd backend && uv run pytest -m scale

test-release:
	cd backend && uv run pytest -m release

test-postgres:
	@test "$(POSTGRES_TEST_URL)" != "$(DATABASE_URL)" || (echo "POSTGRES_TEST_URL must differ from DATABASE_URL" >&2; exit 2)
	cd backend && AX_POSTGRES_TEST_URL="$(POSTGRES_TEST_URL)" uv run pytest -m integration

frontend-build:
	cd frontend && npm run build

frontend-test:
	cd frontend && npm test

frontend-assets:
	cd frontend && npm run verify:assistant-assets

# 데스크톱 셸의 **빌드 구성 정적 검증**(WORK-006 Phase 7).
# 설치파일을 굽지 않고 기기에 아무것도 설치하지 않는다 — 읽고 대조만 하므로 몇 번이든 안전하다.
# 재는 것: 판 번호 단일 출처(Cargo.toml → tauri.conf → shell_info) · 양 플랫폼 번들 타깃 ·
#          아이콘 실재 · 원격 문서 계약(shell-noop) · 권한 경계(local:false · remote.urls 하나).
# SHELL_TAG 를 주면 코드 태그까지 같은 판인지 함께 본다: make shell-verify SHELL_TAG=v0.0.1
#
# ⚠ **「문제 0건」은 「빌드 가능」이 아니다.** 못 재는 것을 **두 종류로 갈라** 센다:
#   · **구성 미비** — 채우면 사라지는 것(예: .icns 부재). strict 가 **실패로 승격**한다
#   · **호스트 한계** — 이 기기가 다른 플랫폼을 못 굽는다는 사실. **어느 모드에서도 정보**다
#     (승격하면 strict 가 영원히 붉어 경보로서 죽는다. 대신 «검증됐다»고 쓰지 않는 금지는 그대로다)
#   SHELL_STRICT=1 make shell-verify     (또는 make shell-verify-strict)
#
# 태그와 strict 를 **함께** 걸면 「이 판 번호로, 구성이 갖춰진 채」를 한 번에 본다 —
# 발행 직전 관문으로 쓰는 조합이다:
#   SHELL_TAG=v0.0.1 SHELL_STRICT=1 make shell-verify
shell-verify:
	cd frontend && node scripts/verify-shell-build.mjs $(if $(SHELL_TAG),--tag $(SHELL_TAG),) $(if $(SHELL_STRICT),--strict,)

# 위와 같되 **검증 불가를 실패로 본다.** fixture/운영 판을 굽기 직전의 관문으로 쓴다.
shell-verify-strict:
	$(MAKE) shell-verify SHELL_STRICT=1

# fixture origin 판을 굽기 «전» 점검 + 명령 안내. **기본은 dry-run 이라 굽지 않는다.**
# 주소를 정하는 두 곳(shell.config.json · capabilities/product-shell.json)이 서로, 그리고
# 요청한 origin 과 같은지 보고 어긋나면 무엇을 어디에 쓸지 알려 주고 멈춘다 — 주소를 지어내지 않는다.
#   make shell-build-fixture SHELL_FIXTURE_ORIGIN=https://<fixture-host>
#   make shell-build-fixture SHELL_FIXTURE_ORIGIN=https://<fixture-host> SHELL_BUILD=1   # 실제 빌드(호스트 플랫폼만)
shell-build-fixture:
	@test -n "$(SHELL_FIXTURE_ORIGIN)" || { echo "SHELL_FIXTURE_ORIGIN 을 주세요 — 예: make shell-build-fixture SHELL_FIXTURE_ORIGIN=https://<fixture-host>"; exit 2; }
	cd frontend && node scripts/build-shell-fixture.mjs --origin "$(SHELL_FIXTURE_ORIGIN)" $(if $(SHELL_BUILD),--run,)

# 운영 origin **최종 빌드 관문**(WORK-006 Phase 8). **굽지 않고, 설치하지 않고, 설정 파일을 쓰지 않는다.**
# shell-verify 와 범위가 다르다 — 저쪽은 「구성이 일관한가」를 보고 자리표시여도 초록이다.
# 이쪽은 「**그 자리표시가 그대로 구워지고 있지 않은가**」를 본다.
#   make shell-final-preflight                                             # 현재 상태 점검(지금은 막힌다)
#   make shell-final-preflight SHELL_OPERATING_ORIGIN=https://<host>
#   make shell-final-preflight SHELL_OPERATING_ORIGIN=https://<host> SHELL_FINAL_EVIDENCE=<path>
#
# 막는 것: .invalid·와일드카드·경로 포함·http·빈 값 · shell.config 미정(null) ·
#          capability 와 불일치 · **D-4 / 운영 서버 실재 / M-1 / M-5 미결**.
# 마지막 넷은 정적 검사로 **잴 수 없다** — 증거 파일에 기록되기 전에는 막고, 기록돼도
# manifest 에 «확인됨»이 아니라 «기록된 주장(attested)»으로 싣는다. **없음을 통과로 바꾸지 않는다.**
#
# ⚠ **자동화·CI 는 이 타깃을 쓰지 말 것 — 종료 코드가 뭉개진다.**
# 스크립트는 원인을 코드로 구분한다: **0** 통과(진짜 src-tauri 에서만) · **1** 관문이 막았다 ·
# **2** 돌릴 수 없다(경로·입력) · **3** 연습(`--shell-root`/`SHELL_ROOT`).
# 그런데 **GNU make 는 레시피의 nonzero 를 모두 자기 코드 2 로 감싼다**(실측: 1·2·3 → 전부 2).
# 그래서 make 로는 「막혔다」와 「돌리지 못했다」와 「연습이었다」가 **구분되지 않는다.**
# 코드를 읽어야 하는 쪽은 **스크립트를 직접 부른다**:
#
#   cd frontend && node scripts/verify-final-build.mjs [--origin <https://host>] [--evidence <path>]
#
# **이 타깃은 사람이 보는 편의 진입점이다** — 출력을 눈으로 읽을 때 쓰고,
# 판정을 기계가 집계할 때는 위의 직접 호출을 쓴다.
shell-final-preflight:
	cd frontend && node scripts/verify-final-build.mjs \
	  $(if $(SHELL_OPERATING_ORIGIN),--origin "$(SHELL_OPERATING_ORIGIN)",) \
	  $(if $(SHELL_FINAL_EVIDENCE),--evidence "$(SHELL_FINAL_EVIDENCE)",)

# **Release 자산 동일성 관문**(WORK-006 Phase 9). **읽기 전용이다** — 굽지 않고, 태그를 달지 않고,
# Release 를 만들지 않고, 서명·공증하지 않고, 네트워크를 건드리지 않는다.
# 묻는 것 하나: 「**올리려는 이 파일이 Phase 8 이 본 바로 그 파일인가**」 — 바이트 해시로 좁힌다.
#   make shell-release-preflight SHELL_RELEASE_MANIFEST=<phase8.json> SHELL_RELEASE_CANDIDATE=<dir>
#
# manifest 가 없거나 그 안에 아티팩트가 없으면 **실패한다** — 자리표시 해시를 만들지 않는다.
# 연습(rehearsal) 기록이나 origin 이 갈린 기록 위에는 Release 를 세우지 않는다.
#
# ⚠ **자동화·CI 는 이 타깃을 쓰지 말 것.** 스크립트는 원인을 코드로 구분한다
# (0 동일 · 2 돌릴 수 없음 · 3 미검증 manifest · 4 아티팩트 없음 · 5 집합 불일치 ·
#  6 해시 불일치 · 7 조합 불일치 · 8 대조 불가 자산 — candidate 에 있는데 기록에 해시가
#  없다(디렉터리 번들 `.app`). 「해시가 없다」를 「같다」로 바꾸지 않는다) 인데,
#  **GNU make 가 nonzero 를 모두 2 로 감싼다.**
# 코드를 읽어야 하는 쪽은 직접 부른다:
#   cd frontend && node scripts/verify-release-artifact.mjs --manifest <path> --candidate <dir>
shell-release-preflight:
	cd frontend && node scripts/verify-release-artifact.mjs \
	  $(if $(SHELL_RELEASE_MANIFEST),--manifest "$(SHELL_RELEASE_MANIFEST)",) \
	  $(if $(SHELL_RELEASE_CANDIDATE),--candidate "$(SHELL_RELEASE_CANDIDATE)",)

# 로컬 Tauri 셸 실행. 운영 shell.config/capability를 고치지 않고 임시 Rust
# 프로젝트 복사본에만 loopback origin을 주입한다. 기본값은 local-stack의
# 프론트 주소다. 이미 `make local-stack`을 띄운 뒤 실행한다.
#   make tauri-local
#   TAURI_LOCAL_ORIGIN=http://127.0.0.1:5176 make tauri-local
tauri-local:
	cd frontend && node scripts/run-tauri-local.mjs

verify: test test-scale test-release frontend-test frontend-assets frontend-build

protected-build:
	docker buildx build --platform "$(PROTECTED_PLATFORM)" --load --tag "$(PROTECTED_IMAGE)" \
		--build-arg "PYTHON_VERSION=$(PROTECTED_PYTHON_VERSION)" \
		--build-arg "PYTHON_BASE=$(PROTECTED_PYTHON_BASE)" \
		--build-arg "CODEX_BASE=$(PROTECTED_CODEX_BASE)" \
		--build-arg "RUNTIME_BASE=$(PROTECTED_RUNTIME_BASE)" \
		--file delivery/Dockerfile .

# Community Nuitka compiles Python but leaves constants visible. A licensed data-hiding build must run this with
# `--expect-constants hidden` instead and must not reuse the community image label.
protected-inspect:
	python3 delivery/inspect_image.py --image "$(PROTECTED_IMAGE)" --expect-constants "$(PROTECTED_EXPECT_CONSTANTS)"

postgres-up:
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U ax -d ax_demo >/dev/null 2>&1; do sleep 1; done
	@docker compose exec -T postgres sh -ec 'psql -U ax -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '\''ax_test'\''" | grep -q 1 || psql -U ax -d postgres -c "CREATE DATABASE ax_test"'
	@docker compose exec -T postgres sh -ec 'psql -U ax -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '\''ax_test_acceptance'\''" | grep -q 1 || psql -U ax -d postgres -c "CREATE DATABASE ax_test_acceptance"'

postgres-down:
	docker compose down

# dataset 명령들. 데이터는 저장소 밖에 있고, 저장소는 계약과 도구만 갖는다.
# 전달받은 자료를 열지 않고 살펴본다. SOURCE 는 저장소 밖 폴더여야 하고, 목록도 그 옆에 쓴다.
# 한 폴더가 한 명령이다: 조직과 그 위의 예제까지. 검증을 통과한 dataset만, 그리고 이 저장소가 reset할 수
# 있는 로컬 demo DB에만 들어간다. DATASET_ARGS=--dry-run 은 조직까지만 넣어 보고 되돌린다.
dataset-import:
	@test -n "$(TARGET)" || (echo "TARGET=<dataset 경로> 를 지정하세요" >&2; exit 2)
	@test -n "$(SCAX_DATASET_PASSWORD)" || echo "SCAX_DATASET_PASSWORD가 없으면 로그인은 만들지 않고 넘어갑니다" >&2
	cd backend && DATABASE_URL="$(DATABASE_URL)" SCAX_DATASET_PASSWORD="$(SCAX_DATASET_PASSWORD)" uv run python -m ax_workspace.entrypoints.dataset import "$(TARGET)" $(DATASET_ARGS)

dataset-inspect:
	@test -n "$(SOURCE)" || (echo "SOURCE=<전달받은 폴더 경로> 를 지정하세요" >&2; exit 2)
	cd backend && uv run python -m ax_workspace.entrypoints.dataset inspect "$(SOURCE)" $(DATASET_ARGS)

reset-demo:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.reset_demo $(RESET_ARGS)

# 실제 조직을 dataset으로 들여올 때: 제품 catalog만 두고 예시 회사는 만들지 않는다.
# 지우지 않고 모델에 맞춘다. 잃을 수 있는 것은 하지 않고 사람이 정하도록 출력한다.
sync-demo-schema:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.reset_demo --sync

reset-catalog:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.reset_demo --catalog-only

api:
	@$(SONIOX_ENV) $(THECONNECT_ENV) cd backend && DATABASE_URL="$(DATABASE_URL)" uv run uvicorn ax_workspace.entrypoints.http:app --reload

conversation-worker:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.conversation_worker

material-worker:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.material_worker

meeting-worker:
	@$(SONIOX_ENV) cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.meeting_worker

report-worker:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.report_worker

mcp:
	@test -n "$$AX_MCP_PERSONA" || (echo "Set AX_MCP_PERSONA to a seeded demo persona"; exit 2)
	cd backend && DATABASE_URL="$(DATABASE_URL)" AX_MCP_PERSONA="$$AX_MCP_PERSONA" uv run python -m ax_workspace.entrypoints.mcp

frontend-install:
	cd frontend && npm install

frontend:
	cd frontend && npm run dev

storybook:
	cd frontend && npm run storybook

storybook-build:
	cd frontend && npm run build-storybook

api-e2e:
	@$(SONIOX_ENV) $(THECONNECT_ENV) cd backend && DATABASE_URL="$(DATABASE_URL)" uv run uvicorn ax_workspace.entrypoints.http:app --host "$(E2E_API_HOST)" --port "$(E2E_API_PORT)"

frontend-e2e:
	cd frontend && VITE_API_TARGET="http://127.0.0.1:$(E2E_API_PORT)" npm run dev -- --host "$(E2E_FRONTEND_HOST)" --port "$(E2E_FRONTEND_PORT)"

# `make acceptance-e2e` runs every browser journey except `e2e-meeting-live-transcript`, which needs a real Soniox
# credential and a macOS fake-microphone recording; run that one on its own against `make local-stack`.
# The complete local stack for a manual walkthrough or the individual e2e-* targets: waits for PostgreSQL, refuses to
# start on an uninitialized schema (run reset-demo first; this target never resets), then starts the API, conversation
# worker, material worker, meeting worker, report worker, and frontend together, supervises them (if any one exits, the rest are
# stopped and the target fails), and stops them all on Ctrl+C. acceptance-e2e uses the same processes on isolated ports.
local-stack:
	@set -eu; \
		pids=""; \
		stop_process_tree() { \
			for child in $$(pgrep -P "$$1" 2>/dev/null || true); do stop_process_tree "$$child"; done; \
			kill -TERM "$$1" 2>/dev/null || true; \
		}; \
		cleanup() { for pid in $$pids; do stop_process_tree "$$pid"; done; for pid in $$pids; do wait "$$pid" 2>/dev/null || true; done; }; \
		trap cleanup EXIT INT TERM; \
		$(MAKE) postgres-up; \
		if ! docker compose exec -T postgres psql -U ax -d "$$(printf '%s' "$(DATABASE_URL)" | sed -E 's#.*/([^/?]+)(\?.*)?$$#\1#')" -tAc "SELECT to_regclass('durable_jobs'), to_regclass('daily_report_generations'), to_regclass('task_checklist_items'), to_regclass('task_schedules'), to_regclass('meeting_transcripts'), to_regclass('assistant_character_preferences'), to_regclass('action_material_drafts'), to_regclass('notifications'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_turns' AND column_name = 'progress_state'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_turns' AND column_name = 'follow_up_candidates'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_messages' AND column_name = 'follow_up_candidate_id'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_messages' AND column_name = 'answer_document')" 2>/dev/null | grep -q 'durable_jobs|daily_report_generations|task_checklist_items|task_schedules|meeting_transcripts|assistant_character_preferences|action_material_drafts|notifications|progress_state|follow_up_candidates|follow_up_candidate_id|answer_document' \
			|| ! docker compose exec -T postgres psql -U ax -d "$$(printf '%s' "$(DATABASE_URL)" | sed -E 's#.*/([^/?]+)(\?.*)?$$#\1#')" -tAc "SELECT to_regclass('notifications')" 2>/dev/null | grep -qx 'notifications'; then \
			echo "SCAX schema is not initialized or is behind the current code in $(DATABASE_URL). Run 'make sync-demo-schema' to add safe missing tables or columns; use 'make reset-demo' only for a disposable fresh demo DB. Then run 'make local-stack' again." >&2; \
			exit 2; \
		fi; \
		names=""; \
		$(MAKE) api-e2e & pids="$$pids $$!"; names="$$names api"; \
		$(MAKE) conversation-worker & pids="$$pids $$!"; names="$$names conversation-worker"; \
		$(MAKE) material-worker & pids="$$pids $$!"; names="$$names material-worker"; \
		$(MAKE) meeting-worker & pids="$$pids $$!"; names="$$names meeting-worker"; \
		$(MAKE) report-worker & pids="$$pids $$!"; names="$$names report-worker"; \
		$(MAKE) frontend-e2e & pids="$$pids $$!"; names="$$names frontend"; \
		for attempt in $$(seq 1 60); do curl -fsS "http://127.0.0.1:$(E2E_API_PORT)/api/auth/providers" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$(E2E_FRONTEND_PORT)" >/dev/null 2>&1 && break; sleep 1; done; \
		curl -fsS "http://127.0.0.1:$(E2E_API_PORT)/api/auth/providers" >/dev/null; \
		curl -fsS "http://127.0.0.1:$(E2E_FRONTEND_PORT)" >/dev/null; \
		check_alive() { i=0; for pid in $$pids; do i=$$((i+1)); if ! kill -0 "$$pid" 2>/dev/null; then echo "SCAX local stack: required process #$$i ($$(printf '%s' "$$names" | awk -v n=$$i '{print $$n}')) exited; stopping the rest" >&2; return 1; fi; done; }; \
		check_alive || exit 1; \
		echo "SCAX local stack ready: API http://127.0.0.1:$(E2E_API_PORT) · frontend http://127.0.0.1:$(E2E_FRONTEND_PORT) · conversation worker · material worker · meeting worker · report worker (Ctrl+C stops all)"; \
		while check_alive; do sleep 2; done; \
		exit 1

e2e-task-lifecycle:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-lifecycle

e2e-task-checklist:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-checklist

e2e-chat-checklist:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:chat-checklist

e2e-chat-recovery:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:chat-recovery

e2e-relation-graph:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:relation-graph

e2e-subtask:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:subtask

e2e-task-delivery:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-delivery

e2e-calendar-tasks:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:calendar-tasks

e2e-task-reference:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-reference

e2e-task-history:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-history

e2e-task-detail-layout:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-detail-layout

e2e-task-origin:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-origin

e2e-work-request:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:work-request

e2e-work-relations:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:work-relations

e2e-action-item:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:action-item

e2e-conversation:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:conversation

e2e-conversation-action:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:conversation-action

e2e-chat-lifecycle:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:chat-lifecycle

e2e-chat-approval:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:chat-approval

e2e-ax-editable-task:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:ax-editable-task

e2e-ax-task-request:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:ax-task-request

e2e-task-progress-batch:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-progress-batch

e2e-ax-editable-meeting:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:ax-editable-meeting

e2e-ax-meeting-draft:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:ax-meeting-draft

e2e-assistant-character:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:assistant-character

e2e-assistant-preference:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:assistant-preference

e2e-follow-up-continuation:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:follow-up-continuation

e2e-ax-action-draft:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:ax-action-draft

e2e-ax-action-materials:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:ax-action-materials

e2e-conversation-report-edit-action:
	cd frontend && SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" node scripts/conversation-report-edit-action-e2e.mjs

e2e-daily-report:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:daily-report

e2e-material-search:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:material-search

# Real microphone path: Chrome plays a wav into getUserMedia and the audio travels browser → our server → Soniox.
# 실시간 전사가 원문 정본이다 — 종료 후 파일을 다시 읽는 경로는 없다 (SCAX-SPEC-004 §5.4-3).
e2e-meeting-live-transcript:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:meeting-live-transcript

# 회의록 세 벌을 **실물 음성**으로 끝까지 밟는다 (SPEC-004 v0.5.1 §4.0 · §8).
# `SCAX_E2E_WAV` 에 16bit PCM wav 경로를 준다 — 음원은 리포에 없다(실제 회의 녹음이라 커밋하지 않는다).
# 배치가 최소 한 번 돌아야 AI 벌이 서므로 기본 180초를 흘린다 (`SCAX_E2E_STREAM_SECONDS` 로 조절).
e2e-meeting-three-tracks:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:meeting-three-tracks

# 같은 원장, 다른 범위: 대표·팀장·구성원이 각자 볼 수 있는 것만 본다.
e2e-access-roles:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:access-roles

e2e-project-participation-history:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:project-participation-history

# 관계 질문 두 turn을 실제 Codex CLI·MCP로: graph 먼저 걷고, 이어지는 질문은 이 대화가 읽은 id에서 출발한다.
e2e-graph-question:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:graph-question

acceptance-e2e:
	@set -eu; \
		acceptance_dir="$$(mktemp -d)"; \
		api_pid=""; worker_pid=""; material_pid=""; meeting_pid=""; report_pid=""; frontend_pid=""; \
		stop_process_tree() { \
			for child in $$(pgrep -P "$$1" 2>/dev/null || true); do stop_process_tree "$$child"; done; \
			kill -TERM "$$1" 2>/dev/null || true; \
		}; \
		cleanup() { \
			outcome=$$?; \
			for pid in "$$api_pid" "$$worker_pid" "$$material_pid" "$$meeting_pid" "$$report_pid" "$$frontend_pid"; do [ -z "$$pid" ] || stop_process_tree "$$pid"; done; \
			for pid in "$$api_pid" "$$worker_pid" "$$material_pid" "$$meeting_pid" "$$report_pid" "$$frontend_pid"; do [ -z "$$pid" ] || wait "$$pid" 2>/dev/null || true; done; \
			if [ "$$outcome" -ne 0 ]; then cat "$$acceptance_dir"/*.log >&2 2>/dev/null || true; fi; \
			rm -rf "$$acceptance_dir"; \
			trap - EXIT; exit "$$outcome"; \
		}; \
		trap cleanup EXIT; \
		for port in "$(ACCEPTANCE_API_PORT)" "$(ACCEPTANCE_FRONTEND_PORT)"; do \
			if lsof -nP -iTCP:"$$port" -sTCP:LISTEN >/dev/null 2>&1; then \
				echo "Acceptance port $$port is already in use; choose free ACCEPTANCE_API_PORT/ACCEPTANCE_FRONTEND_PORT values." >&2; \
				exit 2; \
			fi; \
		done; \
		if [ "$(ACCEPTANCE_MANAGE_POSTGRES)" = "1" ]; then $(MAKE) postgres-up; fi; \
		$(MAKE) DATABASE_URL="$(ACCEPTANCE_DATABASE_URL)" reset-demo; \
		$(MAKE) DATABASE_URL="$(ACCEPTANCE_DATABASE_URL)" E2E_API_PORT="$(ACCEPTANCE_API_PORT)" api-e2e >"$$acceptance_dir/api.log" 2>&1 & api_pid=$$!; \
		$(MAKE) DATABASE_URL="$(ACCEPTANCE_DATABASE_URL)" conversation-worker >"$$acceptance_dir/worker.log" 2>&1 & worker_pid=$$!; \
		$(MAKE) DATABASE_URL="$(ACCEPTANCE_DATABASE_URL)" material-worker >"$$acceptance_dir/material-worker.log" 2>&1 & material_pid=$$!; \
		$(MAKE) DATABASE_URL="$(ACCEPTANCE_DATABASE_URL)" meeting-worker >"$$acceptance_dir/meeting-worker.log" 2>&1 & meeting_pid=$$!; \
		$(MAKE) DATABASE_URL="$(ACCEPTANCE_DATABASE_URL)" report-worker >"$$acceptance_dir/report-worker.log" 2>&1 & report_pid=$$!; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" frontend-e2e >"$$acceptance_dir/frontend.log" 2>&1 & frontend_pid=$$!; \
		for attempt in $$(seq 1 60); do curl -fsS "http://127.0.0.1:$(ACCEPTANCE_API_PORT)/api/auth/providers" >/dev/null && curl -fsS "http://127.0.0.1:$(ACCEPTANCE_FRONTEND_PORT)" >/dev/null && break; sleep 1; done; \
		curl -fsS "http://127.0.0.1:$(ACCEPTANCE_API_PORT)/api/auth/providers" >/dev/null; \
		curl -fsS "http://127.0.0.1:$(ACCEPTANCE_FRONTEND_PORT)" >/dev/null; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-lifecycle; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-checklist; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-detail-layout; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-origin; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-work-request; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-work-relations; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-action-item; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-conversation; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-conversation-action; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-chat-lifecycle; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-chat-approval; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-ax-editable-task; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-ax-task-request; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-progress-batch; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-ax-editable-meeting; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-ax-meeting-draft; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-assistant-character; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-assistant-preference; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-follow-up-continuation; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-ax-action-draft; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-ax-action-materials; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-conversation-report-edit-action; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-daily-report; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-material-search; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-history; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-reference; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-calendar-tasks; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-delivery; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-subtask; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-chat-recovery; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-chat-checklist; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-relation-graph; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-access-roles; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-project-participation-history; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-graph-question

# Opt-in: needs a real SONIOX_API_KEY. Prints provider request ids and durations only.
soniox-smoke:
	@cd backend && $(SONIOX_ENV) uv run python scripts/soniox_smoke.py

live-report-smoke:
	cd backend && DATABASE_URL="$(DATABASE_URL)" SCAX_API_URL="http://127.0.0.1:$(E2E_API_PORT)" uv run python scripts/live_report_smoke.py
