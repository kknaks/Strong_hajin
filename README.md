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

The browser authenticates with a server-side login session (`POST /api/auth/login` → HttpOnly `scax_session` cookie, `GET /api/auth/me`, `POST /api/auth/logout`). Signing in proves who someone is and carries no privilege of its own: the session resolves to a member and the Organization & Access ledger decides everything else. The local provider is an ordinary email and password — `PBKDF2-SHA256`, salted per member, and every refused attempt answers identically so that trying addresses reveals nothing. `make reset-demo` installs the capability catalog, the recommended roles (외부 참여자 · 구성원 · 팀장 · 인사 담당자 · 대표), the demo organization (유나 대표 · 지호 팀장 · 민아 구성원 · 현우 인사 · 소라 외부 법무 자문 · 민석 재무), and one credential per member (`<member>@scax.example`, password `scax-demo-1234`). Installing again adds nothing, and a role the organization has changed (`roles.customized_at`) is never rewritten by a later install; `reset_demo` refuses anything but a local demo database, On a developer machine `GET /api/auth/providers` also hands the sign-in page those demo accounts and their shared password, and pressing one fills the form and posts the same credentials to the same login route — a way to skip typing, not a way to skip signing in, and becoming someone else still means signing out first. Only credentials at the demo domain are listed, so a real account added to a local database is not enumerated. The production profile registers no local login route at all and offers no accounts. Google OIDC will plug into the same session store as a second provider. The `X-Demo-Persona` header remains a development/test seam that only names which member a script is acting as — the ledger still decides whether that member exists and is active, it never overrides a live session, and production omits those routes entirely.

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

For a manual walkthrough, use `make local-stack` as the default run path: it waits for PostgreSQL, starts all five required processes together (API on 8001, conversation worker, material worker, meeting worker, frontend on 5176), fails if any of them exits during startup, and stops them together on Ctrl+C, so no worker can be forgotten. It never resets the database. An AX turn stays `pending` forever when the conversation worker is not running, uploaded materials stay `queued` without the material worker, and uploaded recordings await finalization without the meeting worker. All workers survive transient database failures (for example `make reset-demo` recreating the schema they poll) by logging and retrying with backoff instead of exiting. `make acceptance-e2e` starts with a reset, so it runs in its own database (`ACCEPTANCE_DATABASE_URL`, `ax_test_acceptance` by default) and never touches the organization and materials you loaded into `DATABASE_URL`.

`api-e2e` listens on `127.0.0.1:8001`, while `frontend-e2e` configures Vite's `/api` proxy with `VITE_API_TARGET=http://127.0.0.1:8001` and listens on `127.0.0.1:5176`. This avoids accidentally validating the unrelated default API port 8000; `e2e-work-request` uses the UI itself to create a request, switch to the assignee, then accept it.

The individual `e2e-*` targets do not reset data: they expect a running API, worker, and frontend on the documented E2E ports. In particular, `e2e-daily-report` requires the seeded Mina report date to be unsubmitted, so it is not intended to be run repeatedly against an already-submitted demo database. For repeatable clean-bootstrap acceptance evidence, use the composite command below. It starts the pinned vanilla PostgreSQL 16 container if needed, performs exactly one explicit `reset-demo`, starts an isolated API/conversation-worker/material-worker/meeting-worker/frontend stack on ports `18111`/`15186`, runs every browser journey against that one seed — Task, WorkRequest, ActionItem, Conversation, DailyReport, 자료 검색, 이력, 참고, 캘린더, 결과 확인, 하위 업무, 채팅 복구·체크리스트, 관계 탐색, 역할 범위 — and stops only the processes it started. The one journey outside it is `e2e-meeting-live-transcript`, which needs a real Soniox credential and a macOS fake microphone; run that against `make local-stack`. It fails before reset if either isolated port is occupied; override both ports when necessary.

```sh
make acceptance-e2e
```

Run `DATABASE_URL=postgresql+psycopg://ax:ax@localhost:54329/ax_demo make conversation-worker` alongside the two E2E servers before the conversation browser journeys. `make e2e-conversation` proves the production worker's Codex CLI path calls the persona-bound `task_list` MCP tool, keeps the composer usable while a follow-up is visibly queued in the same conversation, and switches between two independently executing conversations without leaking timeline state; the screenshot is written to `frontend/test-results/conversation-e2e.png`. `make e2e-conversation-action` proves a real Codex MCP `work_request_create` call ends as a pending ActionItem, then approves that exact Action from the general 판단 surface and verifies its shared resource id/audit plus Jiho's Task-free decision inbox projection; it writes `frontend/test-results/conversation-action-e2e.png`. `make e2e-daily-report` is the browser report journey: it creates and starts a real Task, calls the actual report-generation runtime through the Reports page, edits with the returned draft version, submits with the edited version, and re-enters the page to restore the immutable submission history; it writes `frontend/test-results/daily-report-e2e.png` and prints only resource/provenance identifiers. `make e2e-access-roles` is the scope journey: 구성원 · 팀장 · 대표 each sign in with their own address and see exactly what their roles and scoped grants allow — the 대표 reads the organization's work read-only and a private meeting they were not part of, the 팀장 does neither, and nobody else's work reaches 할일 or 내 업무. It also drives the access admin surface on the 조직 page: the 대표 widens a member's authority at a named scope with a reason, then takes it back in the same place. `make live-report-smoke` remains an opt-in DB-level real-Codex report proof: it creates a seeded Task activity, calls `daily_report.generate_draft`, and asserts the persisted WorkflowRun, four NodeRuns, and completed ProviderCall provenance without printing the generated body or prompt.

## 실제 조직·업무 자료 (dataset)

전달받은 자료와 거기서 만든 데이터는 이 저장소가 소유하지 않는다. 저장소는 계약(schema)과 도구만 갖고, 데이터는 Git 밖 폴더에 둔다.

```sh
make dataset-inspect SOURCE=~/Downloads/thesc DATASET_ARGS="--hide-names"   # 열지 않고 분류만
make dataset-init TARGET=~/scax-datasets/actual DATASET_ARGS="--name 조직 --as-of 2026-09-02"
make dataset-validate TARGET=~/scax-datasets/actual
make dataset-import TARGET=~/scax-datasets/actual DATASET_ARGS=--dry-run    # 넣어 본 뒤 되돌린다
SCAX_DATASET_PASSWORD=... make dataset-import TARGET=~/scax-datasets/actual
```

`inspect`는 파일을 열지 않는다. 경로가 말하는 것만으로 `deny`(계정·비밀번호 자료) · `metadata-only`(읽을 수 없는 형식이나 너무 큰 파일) · `manual-review`(기본) · `import`(사람이 `--allow`로 올린 것)을 정하고, 목록을 원본 폴더 옆에 쓴다. 허용 목록이 deny를 이기지 못하며, macOS가 분해해서 저장한 한글 파일명(NFD)도 같은 이름으로 취급한다.

`init`은 빈 CSV header와 manifest를, `validate`는 키 중복·없는 참조·허용되지 않은 값·날짜 형식·순환을 확인한다. 검증 결과는 어느 파일 몇 번째 줄 어느 열인지만 말하고 셀 값은 출력하지 않는다. 두 명령 모두 저장소 안을 가리키면 거절한다.

`import`는 검증을 통과한 dataset을 제품의 원장에 넣는다. 조직을 만드는 두 번째 길이 아니라 제품이 이미 쓰는 행을 사람이 정한 key로 다시 찾아 쓰는 adapter이므로, 두 번 넣어도 한 번 넣은 것과 같다. 한 transaction이라 적용할 수 없는 dataset은 아무것도 남기지 않는다. 넣을 수 있는 대상은 `reset-demo`가 지울 수 있는 로컬 demo DB뿐이다.

실제 조직을 들여올 때는 `make reset-catalog`으로 시작한다. 제품 자신의 것(조직 단위 종류 · 기능 권한 · 권장 역할 · workflow definition)만 설치하고 예시 회사는 만들지 않으므로, 실제 조직이 예시 회사 옆에 나란히 서지 않는다. `make reset-demo`는 예시 회사까지 함께 만드는 기존 동작 그대로이며 모든 browser journey가 그것을 쓴다.

권한은 조직이 정한다. 그 사람의 역할(`members.role_key`)과 보직에 따라오는 역할(`positions.role_key`)이 APPOINTMENT와 ACCESS_GRANT를 만들고, `logins`는 들어오는 문만 만든다. 두 역할은 서로를 덮어쓰지 않는다 — 인사총무팀장은 팀장이면서 인사 담당자다. 보직이 만든 grant는 그 보직과 함께 끝나고 소속이 만든 grant는 소속이 있는 동안 남으며, 어느 쪽인지는 그 grant를 만든 STANDARD_GRANT_RULE이 말한다. 비밀번호는 dataset에 두지 않고 `SCAX_DATASET_PASSWORD`로 준다. 주지 않으면 로그인을 절반만 만들지 않고 "하지 않은 것"으로 보고한다.

조직 전체가 원장에 있어도 로그인은 일부만 갖는다. 답할 수 없는 사람은 업무 요청과 배정의 수행 후보에서 빠지고 그렇게 만들려는 시도는 원장이 거절하므로, 판단이 영영 기다리는 항목이 생기지 않는다. 명부·과거 업무·회의 참석자·graph node로는 그대로 보인다.

`employment_type`은 원문이 사람별로 말할 때만 채운다. 조직도가 팀 단위 인원수로만 말하는 경우에는 비워 두고 추정하지 않는다.

`projects`·`project_assignments`는 부서를 가로질러 묶이는 일과 그 사람들을 나른다. 담당 기간은 없을 수 있고, 배정은 조직 보직과 같은 STANDARD_GRANT_RULE 경로로 그 프로젝트 범위의 grant를 만든다.

프로젝트에는 소유 조직이 없다. 부서를 가로지르려고 있는 것이라 어느 한 부서의 것이라고 적는 순간 그 부서가 열쇠가 되고, `붙어야 보인다`는 규칙에 뒷문이 생긴다 — 배정되지 않은 팀원이 팀의 모든 프로젝트를 읽게 된다. 프로젝트가 열리는 길은 배정 하나뿐이며 조직 전체를 읽는 자격에도 예외가 없다.

만드는 것은 관리하는 일이라 `project.manage`가 있어야 하고, 만든 사람은 담당자로 함께 기록된다. 그러지 않으면 만든 사람조차 자기 프로젝트를 찾지 못해 아무도 붙일 수 없다. 담당자는 사람을 붙이고 뗀다.

## 예제 업무 (scenario)

조직은 정본이고 그 위의 업무는 아니다. `dataset import`가 조직을 넣은 뒤 같은 폴더의 계획으로 같은 성격의 예제 업무를 만든다 — 날짜 단위 실무, 고객사 프로젝트의 계층과 기한, 고객사와의 월간 미팅. 모든 행이 제품의 정식 command를 그 사람으로서 지나가므로 actor·회차·활동 이력·권한이 전부 진짜이고, 두 번 돌려도 한 번 돌린 것과 같다.

계획은 조직 dataset과 같은 폴더의 CSV다. 실제 구성원의 key와 고객사 이름을 가리키므로 저장소 밖에 두고, 저장소는 그 표를 읽는 계약만 갖는다. 저장소 안을 가리키면 명령이 거절한다. `dataset init`이 조직 표와 함께 빈 계획 표도 만든다.

한 폴더가 한 명령이다 — `dataset import`가 조직을 넣고 그 위에 예제를 올린다. 예제 표가 없으면 조직만 들어간다.

```sh
make reset-catalog
SCAX_DATASET_PASSWORD=... make dataset-import TARGET=~/scax-datasets/thesc
```

두 층은 들어가는 길이 다르다. 조직은 원장에 한 transaction으로 들어가고, 예제는 제품의 정식 command를 그 사람으로서 지나간다 — actor·이력·권한이 진짜여야 하기 때문이다. 그래서 예제는 되돌릴 수 없고, `--dry-run`은 조직까지만 보여 준다.

| 표 | 무엇을 담나 |
|---|---|
| `scenario_people.csv` | 사람 key에 붙이는 이름표. 아래 표들은 이 이름만 쓴다 |
| `scenario_work.csv` | 업무 한 줄씩. `project`가 있으면 그 프로젝트의 일, `parent`가 있으면 그 업무의 하위 |
| `scenario_requests.csv` · `scenario_request_cc.csv` | 요청과 그 참조자 |
| `scenario_assignments.csv` | 배정 |
| `scenario_meetings.csv` · `scenario_attendees.csv` | 회의와 참석자 |
| `scenario_checklists.csv` | 업무·요청·배정의 체크리스트 (`owner_kind`로 구분) |

스스로 든 일, 프로젝트의 일, 하위 업무를 세 가지 모양으로 두지 않는다 — 셋의 차이는 `project`와 `parent` 두 칸뿐이다. 쓰지 않는 표는 없어도 되고, 없는 표는 빈 표로 읽는다.

The local stdio MCP server is a development-only delegated binding. It resolves the active Organization & Access principal for every canonical operation, but a long-lived external MCP process must reconnect after its developer persona's employment or grants change; the conversation worker also revalidates that owner and typed context immediately before execution.

## 자료 검색

자료 검색은 제목·파일명이 아니라 추출된 본문을 찾는다. 한국어는 조사가 낱말에 붙어 있어 글자 그대로 비교하면 `견적서를`이 `견적서`를 만나지 못하므로, 문서와 질문에 같은 형태소 분석(Kiwi)을 적용한다. 같은 낱말이 자리에 따라 다르게 갈리는 경우(`납기일은` → `납·기일`, `납기일` → `납기·일`)를 위해 어절에서 조사를 뗀 형태도 함께 색인하고, 갈리면 다른 것이 되는 제품 코드·문서 번호는 원문 그대로도 남긴다. 낱말은 통째로만 맞는다 — `일`은 `일정`이 아니다.

조건·순위·개수는 데이터베이스 안에서 끝난다. PostgreSQL에서는 `tsvector` + GIN 색인이, 그 밖에서는 같은 열을 훑는 방식이 답하며 application으로는 답만 온다. 2,400개 구간에 하나만 있는 낱말을 찾는 통합 test가 실행 계획으로 색인 사용과 순차 스캔 부재를 확인한다.

분석 규칙은 version을 갖는다(`kiwi-<lib>-r<rules>`). 규칙이 바뀌면 그 규칙으로 만든 색인은 질문과 만나지 못하므로 `make reindex-search`로 다시 만든다. 원문은 건드리지 않고 찾기 위한 형태만 바뀌며, 여러 번 돌려도 한 번 돌린 것과 같다.

시작점을 대지 않으면 읽을 수 있는 업무 전부에서 찾는다. 시작점이 넓어져도 권한은 넓어지지 않는다.

`make verify` deliberately excludes PostgreSQL integration tests; its success is not PostgreSQL coverage. For an explicit, reproducible disposable-PostgreSQL proof, start the documented container and run:

```sh
make postgres-up
make test-postgres
make reset-demo
```

`postgres-up` provisions a separate local `ax_test` database beside the app's `ax_demo` database. `test-postgres` resets only `POSTGRES_TEST_URL` (default: `ax_test`) and refuses to run when it equals `DATABASE_URL`. To use a different disposable local port, pass it consistently, for example `make test-postgres POSTGRES_TEST_URL=postgresql+psycopg://localhost:55432/ax_test` and `make reset-demo DATABASE_URL=postgresql+psycopg://localhost:55432/ax_demo`. Reset rejects remote, production-named, and non-demo/test URLs before connecting.
