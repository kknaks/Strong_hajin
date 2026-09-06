DATABASE_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_demo
SONIOX_ENV_FILE ?= $(HOME)/.config/soniox/env
# Load the Soniox key from the same file the vault's transcribe skill uses, without ever printing it. Absent file =
# the feature reports itself unavailable; it never falls back to a stub that pretends to transcribe.
SONIOX_ENV = set -a; [ -f "$(SONIOX_ENV_FILE)" ] && . "$(SONIOX_ENV_FILE)"; set +a;
POSTGRES_TEST_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_test
E2E_API_PORT ?= 8001
E2E_FRONTEND_PORT ?= 5176
ACCEPTANCE_API_PORT ?= 18111
ACCEPTANCE_FRONTEND_PORT ?= 15186

.PHONY: install test test-postgres frontend-test frontend-build verify postgres-up postgres-down reset-demo sync-demo-schema api conversation-worker material-worker meeting-worker mcp frontend-install frontend api-e2e frontend-e2e e2e-task-lifecycle e2e-task-checklist e2e-task-history e2e-task-reference e2e-calendar-tasks e2e-task-delivery e2e-chat-checklist e2e-task-detail-layout e2e-task-origin e2e-work-request e2e-work-relations e2e-action-item e2e-conversation e2e-conversation-action e2e-chat-lifecycle e2e-chat-approval e2e-conversation-report-edit-action e2e-daily-report e2e-material-search e2e-meeting-live-transcript e2e-access-roles e2e-graph-question local-stack acceptance-e2e live-report-smoke soniox-smoke

install:
	cd backend && uv sync --all-groups

test:
	cd backend && uv run pytest -m 'not integration'

test-postgres:
	@test "$(POSTGRES_TEST_URL)" != "$(DATABASE_URL)" || (echo "POSTGRES_TEST_URL must differ from DATABASE_URL" >&2; exit 2)
	cd backend && AX_POSTGRES_TEST_URL="$(POSTGRES_TEST_URL)" uv run pytest -m integration

frontend-build:
	cd frontend && npm run build

frontend-test:
	cd frontend && npm test

verify: test frontend-test frontend-build

postgres-up:
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U ax -d ax_demo >/dev/null 2>&1; do sleep 1; done
	@docker compose exec -T postgres sh -ec 'psql -U ax -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '\''ax_test'\''" | grep -q 1 || psql -U ax -d postgres -c "CREATE DATABASE ax_test"'

postgres-down:
	docker compose down

sync-demo-schema:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.sync_demo_schema

reset-demo:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.reset_demo

api:
	@$(SONIOX_ENV) cd backend && DATABASE_URL="$(DATABASE_URL)" uv run uvicorn ax_workspace.entrypoints.http:app --reload

conversation-worker:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.conversation_worker

material-worker:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.material_worker

meeting-worker:
	@$(SONIOX_ENV) cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.meeting_worker

mcp:
	@test -n "$$AX_MCP_PERSONA" || (echo "Set AX_MCP_PERSONA to a seeded demo persona"; exit 2)
	cd backend && DATABASE_URL="$(DATABASE_URL)" AX_MCP_PERSONA="$$AX_MCP_PERSONA" uv run python -m ax_workspace.entrypoints.mcp

frontend-install:
	cd frontend && npm install

frontend:
	cd frontend && npm run dev

api-e2e:
	@$(SONIOX_ENV) cd backend && DATABASE_URL="$(DATABASE_URL)" uv run uvicorn ax_workspace.entrypoints.http:app --host 127.0.0.1 --port "$(E2E_API_PORT)"

frontend-e2e:
	cd frontend && VITE_API_TARGET="http://127.0.0.1:$(E2E_API_PORT)" npm run dev -- --host 127.0.0.1 --port "$(E2E_FRONTEND_PORT)"

# `make acceptance-e2e` runs every browser journey except `e2e-meeting-live-transcript`, which needs a real Soniox
# credential and a macOS fake-microphone recording; run that one on its own against `make local-stack`.
# The complete local stack for a manual walkthrough or the individual e2e-* targets: waits for PostgreSQL, refuses to
# start on an uninitialized schema (run reset-demo first; this target never resets), then starts the API, conversation
# worker, material worker, meeting worker, and frontend together, supervises them (if any one exits, the rest are stopped and the
# target fails), and stops them all on Ctrl+C. acceptance-e2e uses the same four processes on isolated ports.
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
		if ! docker compose exec -T postgres psql -U ax -d "$$(printf '%s' "$(DATABASE_URL)" | sed -E 's#.*/([^/?]+)(\?.*)?$$#\1#')" -tAc "SELECT to_regclass('durable_jobs'), to_regclass('task_checklist_items'), to_regclass('meeting_recordings'), (SELECT column_name FROM information_schema.columns WHERE table_name = 'conversation_turns' AND column_name = 'progress_state')" 2>/dev/null | grep -q 'durable_jobs|task_checklist_items|meeting_recordings|progress_state'; then \
			echo "SCAX schema is not initialized or is behind the current code in $(DATABASE_URL). Run 'make reset-demo' once (it is the only command that creates or drops tables), then 'make local-stack' again." >&2; \
			exit 2; \
		fi; \
		names=""; \
		$(MAKE) api-e2e & pids="$$pids $$!"; names="$$names api"; \
		$(MAKE) conversation-worker & pids="$$pids $$!"; names="$$names conversation-worker"; \
		$(MAKE) material-worker & pids="$$pids $$!"; names="$$names material-worker"; \
		$(MAKE) meeting-worker & pids="$$pids $$!"; names="$$names meeting-worker"; \
		$(MAKE) frontend-e2e & pids="$$pids $$!"; names="$$names frontend"; \
		for attempt in $$(seq 1 60); do curl -fsS "http://127.0.0.1:$(E2E_API_PORT)/api/auth/providers" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$(E2E_FRONTEND_PORT)" >/dev/null 2>&1 && break; sleep 1; done; \
		curl -fsS "http://127.0.0.1:$(E2E_API_PORT)/api/auth/providers" >/dev/null; \
		curl -fsS "http://127.0.0.1:$(E2E_FRONTEND_PORT)" >/dev/null; \
		check_alive() { i=0; for pid in $$pids; do i=$$((i+1)); if ! kill -0 "$$pid" 2>/dev/null; then echo "SCAX local stack: required process #$$i ($$(printf '%s' "$$names" | awk -v n=$$i '{print $$n}')) exited; stopping the rest" >&2; return 1; fi; done; }; \
		check_alive || exit 1; \
		echo "SCAX local stack ready: API http://127.0.0.1:$(E2E_API_PORT) · frontend http://127.0.0.1:$(E2E_FRONTEND_PORT) · conversation worker · material worker · meeting worker (Ctrl+C stops all)"; \
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

e2e-conversation-report-edit-action:
	cd frontend && SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" node scripts/conversation-report-edit-action-e2e.mjs

e2e-daily-report:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:daily-report

e2e-material-search:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:material-search

# Real microphone path: Chrome plays a wav into getUserMedia, Soniox transcribes it live, the file's reading replaces it.
e2e-meeting-live-transcript:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:meeting-live-transcript

# 같은 원장, 다른 범위: 대표·팀장·구성원이 각자 볼 수 있는 것만 본다.
e2e-access-roles:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:access-roles

# 관계 질문 두 turn을 실제 Codex CLI·MCP로: graph 먼저 걷고, 이어지는 질문은 이 대화가 읽은 id에서 출발한다.
e2e-graph-question:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:graph-question

acceptance-e2e:
	@set -eu; \
		acceptance_dir="$$(mktemp -d)"; \
		api_pid=""; worker_pid=""; material_pid=""; frontend_pid=""; \
		stop_process_tree() { \
			for child in $$(pgrep -P "$$1" 2>/dev/null || true); do stop_process_tree "$$child"; done; \
			kill -TERM "$$1" 2>/dev/null || true; \
		}; \
		cleanup() { \
			outcome=$$?; \
			for pid in "$$api_pid" "$$worker_pid" "$$material_pid" "$$meeting_pid" "$$frontend_pid"; do [ -z "$$pid" ] || stop_process_tree "$$pid"; done; \
			for pid in "$$api_pid" "$$worker_pid" "$$material_pid" "$$meeting_pid" "$$frontend_pid"; do [ -z "$$pid" ] || wait "$$pid" 2>/dev/null || true; done; \
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
		$(MAKE) postgres-up; \
		$(MAKE) reset-demo; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" api-e2e >"$$acceptance_dir/api.log" 2>&1 & api_pid=$$!; \
		$(MAKE) conversation-worker >"$$acceptance_dir/worker.log" 2>&1 & worker_pid=$$!; \
		$(MAKE) material-worker >"$$acceptance_dir/material-worker.log" 2>&1 & material_pid=$$!; \
		$(MAKE) meeting-worker >"$$acceptance_dir/meeting-worker.log" 2>&1 & meeting_pid=$$!; \
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
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-graph-question

# Opt-in: needs a real SONIOX_API_KEY. Prints provider request ids and durations only.
soniox-smoke:
	@cd backend && $(SONIOX_ENV) uv run python scripts/soniox_smoke.py

live-report-smoke:
	cd backend && DATABASE_URL="$(DATABASE_URL)" SCAX_API_URL="http://127.0.0.1:$(E2E_API_PORT)" uv run python scripts/live_report_smoke.py
