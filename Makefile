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

.PHONY: install test test-unit test-contract test-scale test-release test-postgres frontend-test frontend-build frontend-assets verify protected-build protected-inspect postgres-up postgres-down reset-demo reset-catalog sync-demo-schema dataset-import dataset-inspect api conversation-worker material-worker meeting-worker mcp frontend-install frontend storybook storybook-build api-e2e frontend-e2e e2e-task-lifecycle e2e-task-checklist e2e-task-history e2e-task-reference e2e-calendar-tasks e2e-task-delivery e2e-chat-checklist e2e-task-detail-layout e2e-task-origin e2e-work-request e2e-work-relations e2e-action-item e2e-conversation e2e-conversation-action e2e-chat-lifecycle e2e-chat-approval e2e-ax-editable-task e2e-ax-editable-meeting e2e-ax-meeting-draft e2e-assistant-character e2e-assistant-preference e2e-follow-up-continuation e2e-ax-action-draft e2e-ax-action-materials e2e-conversation-report-edit-action e2e-daily-report e2e-material-search e2e-meeting-live-transcript e2e-access-roles e2e-project-participation-history e2e-graph-question local-stack acceptance-e2e live-report-smoke soniox-smoke

install:
	cd backend && uv sync --all-groups

test:
	cd backend && uv run pytest -n auto --dist worksteal

test-unit:
	cd backend && uv run pytest tests/unit tests/architecture

test-contract:
	cd backend && uv run pytest tests/contract -n auto --dist worksteal

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
		if ! docker compose exec -T postgres psql -U ax -d "$$(printf '%s' "$(DATABASE_URL)" | sed -E 's#.*/([^/?]+)(\?.*)?$$#\1#')" -tAc "SELECT to_regclass('durable_jobs'), to_regclass('daily_report_generations'), to_regclass('task_checklist_items'), to_regclass('meeting_transcripts'), to_regclass('assistant_character_preferences'), to_regclass('action_material_drafts'), to_regclass('notifications'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_turns' AND column_name = 'progress_state'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_turns' AND column_name = 'follow_up_candidates'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_messages' AND column_name = 'follow_up_candidate_id'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_messages' AND column_name = 'answer_document')" 2>/dev/null | grep -q 'durable_jobs|daily_report_generations|task_checklist_items|meeting_transcripts|assistant_character_preferences|action_material_drafts|notifications|progress_state|follow_up_candidates|follow_up_candidate_id|answer_document' \
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
