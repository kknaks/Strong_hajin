# ax-workspace

SCAX 상용 시스템의 독립 modular monolith 저장소입니다. 조직·업무·요청·판단·보고와 내장 AX 대화가 하나의 PostgreSQL 원장과 application operation 위에서 동작하며, 개인 일일보고 생성만 내부 동적 Workflow를 사용합니다. 로컬 실행은 별도 demo mode가 아니라 같은 production 경로를 `DeveloperAuthAdapter`와 seed로 검증하는 방식입니다.

장기 설계와 진행 상태는 Obsidian vault의 `SCAX 상용 시스템 구축` Project Note와 `SCAX 상용 시스템 설계` 문서가 소유합니다. 첫 vertical slice의 실행 기록은 `02_PARA/04_Archives/Work Briefs/2026-09-03 - SCAX Workflow catalog demo.md`에 보관되어 있습니다. 디자인 시스템 참조본은 `docs/design/`에, 도메인 모델과 SCAX ERD의 대조표는 `docs/domain-model.md`에 있습니다.

## Local backend bootstrap

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```sh
make install
make frontend-install
make postgres-up
make reset-demo      # once, and again after any persistence schema change
make local-stack     # API 8001 + conversation/material/meeting workers + frontend 5176, Ctrl+C stops all
```

`make local-stack` is the default local run path: it waits for PostgreSQL, refuses to start when the schema has not been initialized (run `make reset-demo` first; the stack itself never resets), starts the five required processes together, supervises them (if any one exits at any time the others are stopped and the target fails), and stops them all on Ctrl+C. The individual targets remain for running one process at a time: `make api` (port 8000, autoreload), `make conversation-worker`, `make material-worker`, `make meeting-worker`, `make frontend` (port 5173). The conversation worker is the separate consumer of the durable job table and the only process that invokes Codex for queued AX turns; without it every AX turn stays `pending`. The material worker extracts and indexes uploaded Task materials; without it uploads stay `queued`. The meeting worker finalizes uploaded audio through raw STT → refinement → final summary, and resumes from the first missing immutable artifact after a fenced crash. All workers and the API share one extension-free PostgreSQL job transport (`durable_jobs`: `FOR UPDATE SKIP LOCKED` claims, fencing lease tokens, at-least-once delivery with idempotent handlers). It uses only standard PostgreSQL features so that it can run on Azure Database for PostgreSQL Flexible Server, which does not offer the PGMQ extension; that compatibility is intended by design and has not yet been probed against an Azure runtime. `AX_JOB_QUEUE_BACKEND` selects `postgres` (default) or `memory` (in-process tests); the removed PGMQ transport and its `AX_CONVERSATION_QUEUE_BACKEND` variable fail fast with an actionable error. Run `AX_MCP_PERSONA=mina make mcp` in another terminal to expose Mina’s dynamically filtered stdio MCP Tool set; this binding is required, so an unbound MCP server never lets a client select `demo-admin`. Run `make verify` in another terminal for non-integration backend tests, frontend behavior tests, and the production Vite build. The API itself starts at `http://127.0.0.1:8000`; Swagger is at `/docs`.

`make reset-demo` is the only command that drops the demo tables, and normal API startup never mutates the schema. After pulling a persistence schema change you have two choices: `make reset-demo` (destructive — a clean seed, losing local demo data) or `make sync-demo-schema`, which adds the tables and columns the model has and the database does not, and refuses to do anything that could lose data (a column the model no longer has, or a NOT NULL column on a table with rows) — it prints those for a person to decide. `sync-demo-schema` is a development convenience and runs only in development/test profiles; it is not a migration tool. Production schema change is a separate, gated piece of work: see the migration baseline Work Brief. Alembic revisions are intentionally not part of this milestone.

## Assignments, cc, and evidence

- Every task holds a `task_assignments` row (self, request_effect, or direct). My Work lists only tasks with an **active** assignment.
- A member with `task.assign` (지호 팀장, 데모 관리자) can assign a task to someone in their own units via 새 업무 추가 → 담당자. It shows up in the assignee's 판단이 필요한 업무 panel until they accept or decline (reason required); the assigner tracks it under 보낸 업무 → 배정한 업무.
- A work request can carry 참조자(cc). cc members read the request, its timeline and comments, and can attach files to their own comments, but never decide.
- Requester and assignee can adopt files as evidence for the current submission (근거 자료). Evidence is pinned to the submission by sha256 and shown per 회차 in the request drawer.
- Capabilities come from `access_grants` only: role grants apply the role's capability mapping at the pinned `role_capability_version`; grants created by a standard rule end with the appointment that produced them. 조직 → 내 권한 shows the grants.

## Material content search and AX evidence

Uploading a Task material records an extraction job in the same transaction as the attachment (`extraction.status` starts as `queued` in the upload response). The separate `make material-worker` process extracts UTF-8 text/Markdown and text-based PDF into bounded chunks; every outcome is an explicit state (`completed`, `failed` with a reason such as `encrypted_pdf`, `corrupt_pdf`, `empty_content`, `not_utf8_text`, or `unsupported`) shown in the Task drawer. `GET /api/tasks/{id}/materials/search?q=` and the MCP tool `task_material_search` run the same `material.search` query: they re-check the caller's active assignment and the live binding first, then return bounded excerpts with file name, page, integrity hash, and the origin URL; detached materials and other people's tasks never appear, not even as counts. When Codex calls the tool inside a delegated turn, the excerpts it read are recorded as that turn's `material_evidence` and rendered as 근거 자료 cards under the answer, each with 원본 열기. File text is never copied into tool timeline summaries or audit rows. `make e2e-material-search` is the real Codex journey for this path.

## Office document parser (not yet persisted)

`modules/work/document_parsing.py` defines a persistence-free `DocumentParser` port whose result is a bounded, ordered `ParsedDocument` (blocks with source locators: DOCX 문단/표/머리글·바닥글, XLSX 시트+셀 범위, PPTX 슬라이드+발표자 노트, PDF 페이지) and an explainable status (`ok`, `empty`, `needs_ocr`, `unsupported`, `encrypted`, `corrupt`, `budget_exceeded`). `platform/document_parsers.py` implements it with python-docx, openpyxl (read-only, formulas kept as text, external links never followed), python-pptx, and pypdf behind an OOXML ZIP preflight (member count, per-part and total uncompressed size, compression ratio, encryption flags). It is deliberately **not** connected to the material extraction tables: where Office output is stored and at what granularity is the user gate described in `docs/material-data-management-design.md`. `uv run python backend/scripts/probe_documents.py <dir>` probes a local directory read-only and prints names, sizes, and statuses only.

## Task fields and materials

A Task carries `description`, `start_date`, and `due_date` beside its state; the owner edits them with `PATCH /api/tasks/{id}` (`task.update`, no approval gate, `expected_version` required, start ≤ due). A WorkRequest carries an optional `due_date` and `description` that flow into the Task created on acceptance. Reference documents (`kind=input`) and deliverables (`kind=output`) are uploaded with `POST /api/tasks/{id}/materials` (multipart, 25MB), listed, downloaded from `/content`, and detached (the record and bytes stay for lineage). Bytes live behind the `MaterialStorage` port; the local adapter writes under `AX_MATERIALS_DIR` (default `backend/.scax/materials`, git-ignored) and an Azure Blob container adapter will implement the same port. Completed tasks can be reopened (`resume`); cancellation stays terminal.

## Login and sessions

The browser authenticates with a server-side login session (`POST /api/auth/login` → HttpOnly `scax_session` cookie, `GET /api/auth/me`, `POST /api/auth/logout`). Signing in proves who someone is and carries no privilege of its own: the session resolves to a member and the Organization & Access ledger decides everything else. The local provider is an ordinary email and password — `PBKDF2-SHA256`, salted per member, and every refused attempt answers identically so that trying addresses reveals nothing. `make reset-demo` installs the capability catalog, the recommended roles (외부 참여자 · 구성원 · 팀장 · 인사 담당자 · 대표), the demo organization (유나 대표 · 지호 팀장 · 민아 구성원 · 현우 인사 · 소라 외부 법무 자문 · 민석 재무), and one credential per member (`<member>@scax.example`, password `scax-demo-1234`). Installing again adds nothing, and a role the organization has changed (`roles.customized_at`) is never rewritten by a later install; `reset_demo` refuses anything but a local demo database, `GET /api/auth/providers` never lists who has an account, and the production profile registers no local login route at all. Google OIDC will plug into the same session store as a second provider. The `X-Demo-Persona` header remains a development/test seam that only names which member a script is acting as — the ledger still decides whether that member exists and is active, it never overrides a live session, and production omits those routes entirely.

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

For a manual walkthrough, use `make local-stack` as the default run path: it waits for PostgreSQL, starts all five required processes together (API on 8001, conversation worker, material worker, meeting worker, frontend on 5176), fails if any of them exits during startup, and stops them together on Ctrl+C, so no worker can be forgotten. It never resets the database. An AX turn stays `pending` forever when the conversation worker is not running, uploaded materials stay `queued` without the material worker, and uploaded recordings await finalization without the meeting worker. All workers survive transient database failures (for example `make reset-demo` or `make acceptance-e2e` recreating the schema they poll) by logging and retrying with backoff instead of exiting; note that `acceptance-e2e` resets the same `DATABASE_URL`, so run it only when you can lose the local demo data.

`api-e2e` listens on `127.0.0.1:8001`, while `frontend-e2e` configures Vite's `/api` proxy with `VITE_API_TARGET=http://127.0.0.1:8001` and listens on `127.0.0.1:5176`. This avoids accidentally validating the unrelated default API port 8000; `e2e-work-request` uses the UI itself to create a request, switch to the assignee, then accept it.

The individual `e2e-*` targets do not reset data: they expect a running API, worker, and frontend on the documented E2E ports. In particular, `e2e-daily-report` requires the seeded Mina report date to be unsubmitted, so it is not intended to be run repeatedly against an already-submitted demo database. For repeatable clean-bootstrap acceptance evidence, use the composite command below. It starts the pinned vanilla PostgreSQL 16 container if needed, performs exactly one explicit `reset-demo`, starts an isolated API/conversation-worker/material-worker/meeting-worker/frontend stack on ports `18111`/`15186`, runs the Task → WorkRequest → Conversation → Action → DailyReport browser journeys against that one seed, and stops only the processes it started. It fails before reset if either isolated port is occupied; override both ports when necessary.

```sh
make acceptance-e2e
```

Run `DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make conversation-worker` alongside the two E2E servers before the conversation browser journeys. `make e2e-conversation` proves the production worker's Codex CLI path calls the persona-bound `task_list` MCP tool, keeps the composer usable while a follow-up is visibly queued in the same conversation, and switches between two independently executing conversations without leaking timeline state; the screenshot is written to `frontend/test-results/conversation-e2e.png`. `make e2e-conversation-action` proves a real Codex MCP `work_request_create` call ends as a pending ActionItem, then approves that exact Action from the general 판단 surface and verifies its shared resource id/audit plus Jiho's Task-free decision inbox projection; it writes `frontend/test-results/conversation-action-e2e.png`. `make e2e-daily-report` is the browser report journey: it creates and starts a real Task, calls the actual report-generation runtime through the Reports page, edits with the returned draft version, submits with the edited version, and re-enters the page to restore the immutable submission history; it writes `frontend/test-results/daily-report-e2e.png` and prints only resource/provenance identifiers. `make e2e-access-roles` is the scope journey: 구성원 · 팀장 · 대표 each sign in with their own address and see exactly what their roles and scoped grants allow — the 대표 reads the organization's work read-only and a private meeting they were not part of, the 팀장 does neither, and nobody else's work reaches 할일 or 내 업무. It also drives the access admin surface on the 조직 page: the 대표 widens a member's authority at a named scope with a reason, then takes it back in the same place. `make live-report-smoke` remains an opt-in DB-level real-Codex report proof: it creates a seeded Task activity, calls `daily_report.generate_draft`, and asserts the persisted WorkflowRun, four NodeRuns, and completed ProviderCall provenance without printing the generated body or prompt.

The local stdio MCP server is a development-only delegated binding. It resolves the active Organization & Access principal for every canonical operation, but a long-lived external MCP process must reconnect after its developer persona's employment or grants change; the conversation worker also revalidates that owner and typed context immediately before execution.

`make verify` deliberately excludes PostgreSQL integration tests; its success is not PostgreSQL coverage. For an explicit, reproducible disposable-PostgreSQL proof, start the documented container and run:

```sh
make postgres-up
make test-postgres
make reset-demo
```

`postgres-up` provisions a separate local `ax_test` database beside the app's `ax_demo` database. `test-postgres` resets only `POSTGRES_TEST_URL` (default: `ax_test`) and refuses to run when it equals `DATABASE_URL`. To use a different disposable local port, pass it consistently, for example `make test-postgres POSTGRES_TEST_URL=postgresql+psycopg://localhost:55432/ax_test` and `make reset-demo DATABASE_URL=postgresql+psycopg://localhost:55432/ax_demo`. Reset rejects remote, production-named, and non-demo/test URLs before connecting.
