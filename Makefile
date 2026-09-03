DATABASE_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_demo
POSTGRES_TEST_URL ?= postgresql+psycopg://ax:ax@localhost:54329/ax_test

.PHONY: install test test-postgres web-build verify postgres-up postgres-down reset-demo api mcp web-install web demo-rehearse mcp-probe

install:
	cd backend && uv sync --all-groups

test:
	cd backend && uv run pytest -m 'not integration'

test-postgres:
	@test "$(POSTGRES_TEST_URL)" != "$(DATABASE_URL)" || (echo "POSTGRES_TEST_URL must differ from DATABASE_URL" >&2; exit 2)
	cd backend && AX_POSTGRES_TEST_URL="$(POSTGRES_TEST_URL)" uv run pytest -m integration

web-build:
	cd web && npm run build

verify: test web-build

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

mcp:
	@test -n "$$AX_MCP_PERSONA" || (echo "Set AX_MCP_PERSONA to a seeded demo persona"; exit 2)
	cd backend && DATABASE_URL="$(DATABASE_URL)" AX_MCP_PERSONA="$$AX_MCP_PERSONA" uv run python -m ax_workspace.entrypoints.mcp

web-install:
	cd web && npm install

web:
	cd web && npm run dev

demo-rehearse:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.demo_rehearsal

mcp-probe:
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run python -m ax_workspace.entrypoints.mcp_probe
