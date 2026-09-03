.PHONY: install test web-build verify postgres-up postgres-down reset-demo api mcp web-install web demo-rehearse mcp-probe

install:
	cd backend && uv sync --all-groups

test:
	cd backend && uv run pytest

web-build:
	cd web && npm run build

verify: test web-build

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down

reset-demo:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run python -m ax.reset_demo

api:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run uvicorn ax.api:app --reload

mcp:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run python -m ax.mcp_server

web-install:
	cd web && npm install

web:
	cd web && npm run dev

demo-rehearse:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run python -m ax.demo_rehearsal

mcp-probe:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run python -m ax.mcp_probe
