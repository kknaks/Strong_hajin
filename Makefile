.PHONY: install test postgres-up postgres-down reset-demo api

install:
	cd backend && uv sync --all-groups

test:
	cd backend && uv run pytest

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down

reset-demo:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run python -m ax.reset_demo

api:
	cd backend && DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo uv run uvicorn ax.api:app --reload

