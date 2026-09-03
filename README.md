# ax-workspace

SCAX 상용 시스템의 독립 modular monolith 저장소입니다. 조직·업무·요청·판단·보고와 내장 AX 대화가 하나의 PostgreSQL 원장과 application operation 위에서 동작하며, 개인 일일보고 생성만 내부 동적 Workflow를 사용합니다. 로컬 실행은 별도 demo mode가 아니라 같은 production 경로를 `DeveloperAuthAdapter`와 seed로 검증하는 방식입니다.

장기 설계와 진행 상태는 Obsidian vault의 `SCAX 상용 시스템 구축` Project Note와 `SCAX 상용 시스템 설계` 문서가 소유합니다. 첫 vertical slice의 실행 기록은 `02_PARA/04_Archives/Work Briefs/2026-09-03 - SCAX Workflow catalog demo.md`에 보관되어 있습니다. 디자인 시스템 참조본은 `docs/design/`에, 도메인 모델과 SCAX ERD의 대조표는 `docs/domain-model.md`에 있습니다.

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

`make reset-demo` is the only command that creates or drops the demo tables. Normal API startup never mutates the schema. After pulling a persistence schema change, stop the local API/worker and run `make reset-demo` before local journeys; Alembic revisions are intentionally not part of this milestone.

## Task fields and materials

A Task carries `description`, `start_date`, and `due_date` beside its state; the owner edits them with `PATCH /api/tasks/{id}` (`task.update`, no approval gate, `expected_version` required, start ≤ due). A WorkRequest carries an optional `due_date` and `description` that flow into the Task created on acceptance. Reference documents (`kind=input`) and deliverables (`kind=output`) are uploaded with `POST /api/tasks/{id}/materials` (multipart, 25MB), listed, downloaded from `/content`, and detached (the record and bytes stay for lineage). Bytes live behind the `MaterialStorage` port; the local adapter writes under `AX_MATERIALS_DIR` (default `backend/.scax/materials`, git-ignored) and an Azure Blob container adapter will implement the same port. Completed tasks can be reopened (`resume`); cancellation stays terminal.

## Login and sessions

The browser authenticates with a server-side login session (`POST /api/auth/login` → HttpOnly `scax_session` cookie, `GET /api/auth/me`, `POST /api/auth/logout`). The session is the production credential boundary; authorization always resolves the session to an active Organization & Access principal. Only the `developer` credential provider is wired today: while `AX_PROFILE` is `development` or `test`, the login page offers a "누구로 로그인" account picker over the seeded personas (`mina`, `jiho`, `sora`, `minseok`). Google OIDC will plug into the same login route and session store as a second provider. The `X-Demo-Persona` header remains a development/test seam for API scripts and MCP tests only; it never overrides an active session, and production omits the developer routes entirely.

## Current API slice

The product surface exposes direct Task and Reports operations. `POST /api/daily-reports/generate-draft` runs the persisted `daily-report-generation@1` metadata internally, then Reports owns `edit`, `submit`, and `history`; the browser and persona-bound MCP server use those same operations rather than a generic run console.

The only production/development LLM adapter is `CodexCliProviderAdapter`. It invokes `codex exec` with an isolated runtime home, user config/rules/skills/plugins disabled, a read-only sandbox, `gpt-5.6-terra`, Fast tier, low reasoning, and a structured output schema. A missing CLI binary or authentication fails explicitly; deterministic providers are injected only by tests.

For the browser WorkRequest journey, run the backend on port 8001, then start Vite with the matching proxy and execute the Playwright script:

```sh
DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make api-e2e
DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make frontend-e2e
make e2e-task-lifecycle
make e2e-work-request
make e2e-conversation
make e2e-conversation-action
make e2e-conversation-report-edit-action
make e2e-daily-report
make live-report-smoke
```

`api-e2e` listens on `127.0.0.1:8001`, while `frontend-e2e` configures Vite's `/api` proxy with `VITE_API_TARGET=http://127.0.0.1:8001` and listens on `127.0.0.1:5176`. This avoids accidentally validating the unrelated default API port 8000; `e2e-work-request` uses the UI itself to create a request, switch to the assignee, then accept it.

The individual `e2e-*` targets do not reset data: they expect a running API, worker, and frontend on the documented E2E ports. In particular, `e2e-daily-report` requires the seeded Mina report date to be unsubmitted, so it is not intended to be run repeatedly against an already-submitted demo database. For repeatable clean-bootstrap acceptance evidence, use the composite command below. It starts PGMQ PostgreSQL if needed, performs exactly one explicit `reset-demo`, starts an isolated API/worker/frontend stack on ports `18111`/`15186`, runs the Task → WorkRequest → Conversation → Action → DailyReport browser journeys against that one seed, and stops only the processes it started. It fails before reset if either isolated port is occupied; override both ports when necessary.

```sh
make acceptance-e2e
```

Run `DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make conversation-worker` alongside the two E2E servers before the conversation browser journeys. `make e2e-conversation` proves the production worker's Codex CLI path calls the persona-bound `task_list` MCP tool, keeps the composer usable while a follow-up is visibly queued in the same conversation, and switches between two independently executing conversations without leaking timeline state; the screenshot is written to `frontend/test-results/conversation-e2e.png`. `make e2e-conversation-action` proves a real Codex MCP `work_request_create` call ends as a pending ActionItem, then approves that exact Action from the general 판단 surface and verifies its shared resource id/audit plus Jiho's Task-free decision inbox projection; it writes `frontend/test-results/conversation-action-e2e.png`. `make e2e-daily-report` is the browser report journey: it creates and starts a real Task, calls the actual report-generation runtime through the Reports page, edits with the returned draft version, submits with the edited version, and re-enters the page to restore the immutable submission history; it writes `frontend/test-results/daily-report-e2e.png` and prints only resource/provenance identifiers. `make live-report-smoke` remains an opt-in DB-level real-Codex report proof: it creates a seeded Task activity, calls `daily_report.generate_draft`, and asserts the persisted WorkflowRun, four NodeRuns, and completed ProviderCall provenance without printing the generated body or prompt.

The local stdio MCP server is a development-only delegated binding. It resolves the active Organization & Access principal for every canonical operation, but a long-lived external MCP process must reconnect after its developer persona's employment or grants change; the conversation worker also revalidates that owner and typed context immediately before execution.

`make verify` deliberately excludes PostgreSQL integration tests; its success is not PostgreSQL coverage. For an explicit, reproducible disposable-PostgreSQL proof, start the documented container and run:

```sh
make postgres-up
make test-postgres
make reset-demo
```

`postgres-up` provisions a separate local `ax_test` database beside the app's `ax_demo` database. `test-postgres` resets only `POSTGRES_TEST_URL` (default: `ax_test`) and refuses to run when it equals `DATABASE_URL`. To use a different disposable local port, pass it consistently, for example `make test-postgres POSTGRES_TEST_URL=postgresql+psycopg://localhost:55432/ax_test` and `make reset-demo DATABASE_URL=postgresql+psycopg://localhost:55432/ax_demo`. Reset rejects remote, production-named, and non-demo/test URLs before connecting.
