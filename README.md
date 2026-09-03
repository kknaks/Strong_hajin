# ax-workspace

SCAX의 독립 Workflow catalog 제품 저장소입니다. `mediness-app`과 코드, 데이터베이스, API 또는 인증을 공유하지 않습니다.

현재 실행 계약은 다음 Work Brief가 소유합니다.

- `/Users/dante/git/Main/00_Inbox/Work Briefs/2026-09-03 - SCAX Workflow catalog demo.md`

## Local backend bootstrap

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```sh
make install
make web-install
make postgres-up
make reset-demo
make api
```

In a second terminal, run `make web`; the browser UI starts at `http://127.0.0.1:5173` and proxies `/api` to FastAPI on port 8000. Run `AX_MCP_PERSONA=mina make mcp` in a third terminal to expose Mina’s dynamically filtered stdio MCP Tool set; this binding is required, so an unbound MCP server never lets a client select `demo-admin`. Run `make verify` in a fourth terminal for the non-integration backend tests plus the production Vite build. The API itself starts at `http://127.0.0.1:8000`; Swagger is at `/docs`.

`make reset-demo` is the only command that creates or drops the demo tables. Normal API startup never mutates the schema. `X-Demo-Persona` accepts only a seeded persona (`mina`, `jiho`, `sora`, `minseok`, `demo-admin`) while `AX_PROFILE` is `development` or `test`; production omits those routes entirely.

## Current API slice

The catalog and run endpoints use one `WorkflowRunStarter`, PostgreSQL repository, and unit of work. Starts are version-pinned to a seeded `WorkflowDefinitionVersion`; normal progression writes node execution, tool-result, human-decision, and append-only audit records.

- `GET /api/catalog` shows workflow definitions allowed for `X-Demo-Persona`.
- `POST /api/runs/{workflow_id}` starts a run with `{ "input": { ... } }`.
- `GET /api/inbox`, `POST /api/runs/{run_id}/decisions/{node_id}`, and `GET /api/my-work` demonstrate the human decision flow.

The meeting-followup flow creates a `pending_acceptance` assignment after the requester chooses an assignee. It becomes visible in My Work only after that exact assignee accepts; rejection leaves it out of My Work. The daily-report confirmation and contract legal/finance `all` join use the same runtime, with local demo adapters standing in for external effects.

The six non-golden definitions complete through the same local demo adapter and persisted runtime; real external adapters, production authentication, and the final timed demo rehearsal remain outside this slice.

## Demo rehearsal

After `make reset-demo`, run `make demo-rehearse`. It executes the daily-report confirmation, meeting-assignment acceptance, and contract legal/finance `all` join against the same PostgreSQL runtime and writes a reproducible audit summary to `artifacts/demo-rehearsal.json`.

`make mcp-probe` launches a local stdio MCP client/server pair and records tool discovery, structured-result validation, a daily-report golden flow, and per-call latency to `artifacts/mcp-probe.json`. Its recorded `gpt-5.6-terra` / priority / low-tool / medium-authoring profile is a local protocol baseline—not a cloud Codex request—because cloud model traffic is excluded from this demo scope.

`make verify` deliberately excludes PostgreSQL integration tests; its success is not PostgreSQL coverage. For an explicit, reproducible disposable-PostgreSQL proof, start the documented container and run:

```sh
make postgres-up
make test-postgres
make reset-demo
make demo-rehearse
make mcp-probe
```

`postgres-up` provisions a separate local `ax_test` database beside the app's `ax_demo` database. `test-postgres` resets only `POSTGRES_TEST_URL` (default: `ax_test`), refuses to run when it equals `DATABASE_URL`, and verifies the golden flows plus competing human-decision serialization. To use a different disposable local port, pass it consistently, for example `make test-postgres POSTGRES_TEST_URL=postgresql+psycopg://localhost:55432/ax_test` and `make reset-demo DATABASE_URL=postgresql+psycopg://localhost:55432/ax_demo`. Reset rejects remote, production-named, and non-demo/test URLs before connecting.
