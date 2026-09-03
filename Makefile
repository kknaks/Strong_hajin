DATABASE_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_demo
POSTGRES_TEST_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_test
E2E_API_PORT ?= 8001
E2E_FRONTEND_PORT ?= 5176
ACCEPTANCE_API_PORT ?= 18111
ACCEPTANCE_FRONTEND_PORT ?= 15186

.PHONY: install test test-postgres frontend-test frontend-build verify postgres-up postgres-down reset-demo api conversation-worker mcp frontend-install frontend api-e2e frontend-e2e e2e-task-lifecycle e2e-work-request e2e-conversation e2e-conversation-action e2e-conversation-report-edit-action e2e-daily-report acceptance-e2e live-report-smoke

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

reset-demo:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.reset_demo

api:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run uvicorn ax_workspace.entrypoints.http:app --reload

conversation-worker:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.conversation_worker

mcp:
	@test -n "$$AX_MCP_PERSONA" || (echo "Set AX_MCP_PERSONA to a seeded demo persona"; exit 2)
	cd backend && DATABASE_URL="$(DATABASE_URL)" AX_MCP_PERSONA="$$AX_MCP_PERSONA" uv run python -m ax_workspace.entrypoints.mcp

frontend-install:
	cd frontend && npm install

frontend:
	cd frontend && npm run dev

api-e2e:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run uvicorn ax_workspace.entrypoints.http:app --host 127.0.0.1 --port "$(E2E_API_PORT)"

frontend-e2e:
	cd frontend && VITE_API_TARGET="http://127.0.0.1:$(E2E_API_PORT)" npm run dev -- --host 127.0.0.1 --port "$(E2E_FRONTEND_PORT)"

e2e-task-lifecycle:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:task-lifecycle

e2e-work-request:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:work-request

e2e-conversation:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:conversation

e2e-conversation-action:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:conversation-action

e2e-conversation-report-edit-action:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" node frontend/scripts/conversation-report-edit-action-e2e.mjs

e2e-daily-report:
	SCAX_E2E_URL="http://127.0.0.1:$(E2E_FRONTEND_PORT)" npm --prefix frontend run e2e:daily-report

acceptance-e2e:
	@set -eu; \
		acceptance_dir="$$(mktemp -d)"; \
		api_pid=""; worker_pid=""; frontend_pid=""; \
		stop_process_tree() { \
			for child in $$(pgrep -P "$$1" 2>/dev/null || true); do stop_process_tree "$$child"; done; \
			kill -TERM "$$1" 2>/dev/null || true; \
		}; \
		cleanup() { \
			outcome=$$?; \
			for pid in "$$api_pid" "$$worker_pid" "$$frontend_pid"; do [ -z "$$pid" ] || stop_process_tree "$$pid"; done; \
			for pid in "$$api_pid" "$$worker_pid" "$$frontend_pid"; do [ -z "$$pid" ] || wait "$$pid" 2>/dev/null || true; done; \
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
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" frontend-e2e >"$$acceptance_dir/frontend.log" 2>&1 & frontend_pid=$$!; \
		for attempt in $$(seq 1 60); do curl -fsS "http://127.0.0.1:$(ACCEPTANCE_API_PORT)/api/developer/personas" >/dev/null && curl -fsS "http://127.0.0.1:$(ACCEPTANCE_FRONTEND_PORT)" >/dev/null && break; sleep 1; done; \
		curl -fsS "http://127.0.0.1:$(ACCEPTANCE_API_PORT)/api/developer/personas" >/dev/null; \
		curl -fsS "http://127.0.0.1:$(ACCEPTANCE_FRONTEND_PORT)" >/dev/null; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-task-lifecycle; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-work-request; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-conversation; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-conversation-action; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-conversation-report-edit-action; \
		$(MAKE) E2E_API_PORT="$(ACCEPTANCE_API_PORT)" E2E_FRONTEND_PORT="$(ACCEPTANCE_FRONTEND_PORT)" e2e-daily-report

live-report-smoke:
	cd backend && DATABASE_URL="$(DATABASE_URL)" SCAX_API_URL="http://127.0.0.1:$(E2E_API_PORT)" uv run python scripts/live_report_smoke.py
