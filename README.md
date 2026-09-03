# ax-workspace

SCAX의 모듈형 업무 제품 데모 저장소입니다. 개인 일일보고만 내부 동적 Workflow를 사용하며, 조직·업무·요청·판단은 각각의 고유 application operation으로 발전합니다.

현재 실행 계약은 다음 Work Brief가 소유합니다.

- `/Users/dante/git/Main/00_Inbox/Work Briefs/2026-09-03 - SCAX Workflow catalog demo.md`

## Local backend bootstrap

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```sh
make install
make frontend-install
make postgres-up
make reset-demo
make api
```

In a second terminal, run `make conversation-worker`; it is the separate PGMQ consumer and is the only process that invokes Codex for queued AX turns. In a third terminal, run `make frontend`; the browser UI starts at `http://127.0.0.1:5173` and proxies `/api` to FastAPI on port 8000. Run `AX_MCP_PERSONA=mina make mcp` in a fourth terminal to expose Mina’s dynamically filtered stdio MCP Tool set; this binding is required, so an unbound MCP server never lets a client select `demo-admin`. Run `make verify` in another terminal for non-integration backend tests, frontend behavior tests, and the production Vite build. The API itself starts at `http://127.0.0.1:8000`; Swagger is at `/docs`.

`make reset-demo` is the only command that creates or drops the demo tables. Normal API startup never mutates the schema. `X-Demo-Persona` accepts only a seeded persona (`mina`, `jiho`, `sora`, `minseok`, `demo-admin`) while `AX_PROFILE` is `development` or `test`; production omits those routes entirely.

## Current API slice

The product surface exposes direct Task and Reports operations. `POST /api/daily-reports/generate-draft` runs the persisted `daily-report-generation@1` metadata internally, then Reports owns `edit`, `submit`, and `history`; the browser and persona-bound MCP server use those same operations rather than a generic run console.

The only production/development LLM adapter is `CodexCliProviderAdapter`. It invokes `codex exec` with an isolated runtime home, user config/rules/skills/plugins disabled, a read-only sandbox, `gpt-5.6-terra`, Fast tier, low reasoning, and a structured output schema. A missing CLI binary or authentication fails explicitly; deterministic providers are injected only by tests.

For the browser WorkRequest journey, run the backend on port 8001, then start Vite with the matching proxy and execute the Playwright script:

```sh
DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make api-e2e
DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make frontend-e2e
make e2e-work-request
make e2e-conversation
make live-report-smoke
```

`api-e2e` listens on `127.0.0.1:8001`, while `frontend-e2e` configures Vite's `/api` proxy with `VITE_API_TARGET=http://127.0.0.1:8001` and listens on `127.0.0.1:5176`. This avoids accidentally validating the unrelated default API port 8000; `e2e-work-request` uses the UI itself to create a request, switch to the assignee, then accept it.

Run `DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make conversation-worker` alongside the two E2E servers before `make e2e-conversation`. It proves the production worker's Codex CLI path calls the persona-bound `task_list` MCP tool, keeps the composer usable while a follow-up is visibly queued in the same conversation, and switches between two independently executing conversations without leaking timeline state; the screenshot is written to `frontend/test-results/conversation-e2e.png`. `make live-report-smoke` is an opt-in real-Codex report proof: it creates a seeded Task activity, calls `daily_report.generate_draft`, and asserts the persisted WorkflowRun, four NodeRuns, and completed ProviderCall provenance without printing the generated body or prompt.

`make verify` deliberately excludes PostgreSQL integration tests; its success is not PostgreSQL coverage. For an explicit, reproducible disposable-PostgreSQL proof, start the documented container and run:

```sh
make postgres-up
make test-postgres
make reset-demo
```

`postgres-up` provisions a separate local `ax_test` database beside the app's `ax_demo` database. `test-postgres` resets only `POSTGRES_TEST_URL` (default: `ax_test`) and refuses to run when it equals `DATABASE_URL`. To use a different disposable local port, pass it consistently, for example `make test-postgres POSTGRES_TEST_URL=postgresql+psycopg://localhost:55432/ax_test` and `make reset-demo DATABASE_URL=postgresql+psycopg://localhost:55432/ax_demo`. Reset rejects remote, production-named, and non-demo/test URLs before connecting.
