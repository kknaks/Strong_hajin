# ax-workspace

SCAX의 독립 Workflow catalog 제품 저장소입니다. `mediness-app`과 코드, 데이터베이스, API 또는 인증을 공유하지 않습니다.

현재 실행 계약은 다음 Work Brief가 소유합니다.

- `/Users/dante/git/Main/00_Inbox/Work Briefs/2026-09-03 - SCAX Workflow catalog demo.md`

## Local backend bootstrap

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```sh
make install
make postgres-up
make reset-demo
make api
```

In a second terminal, run `make test`. The API starts at `http://127.0.0.1:8000`; Swagger is at `/docs`.

`make reset-demo` is the only command that creates or drops the demo tables. Normal API startup never mutates the schema. `X-Demo-Persona` accepts only a seeded persona (`mina`, `jiho`, `sora`, `minseok`, `demo-admin`) while `AX_PROFILE` is `development` or `test`; production omits those routes entirely.

## Current API slice

The catalog and run endpoints use one `WorkflowRunStarter`, PostgreSQL repository, and unit of work. Starts are version-pinned to a seeded `WorkflowDefinitionVersion`; normal progression writes node execution, tool-result, human-decision, and append-only audit records.

- `GET /api/catalog` shows workflow definitions allowed for `X-Demo-Persona`.
- `POST /api/runs/{workflow_id}` starts a run with `{ "input": { ... } }`.
- `GET /api/inbox`, `POST /api/runs/{run_id}/decisions/{node_id}`, and `GET /api/my-work` demonstrate the human decision flow.

The meeting-followup flow creates a `pending_acceptance` assignment after the requester chooses an assignee. It becomes visible in My Work only after that exact assignee accepts; rejection leaves it out of My Work. The daily-report confirmation and contract legal/finance `all` join use the same runtime, with local demo adapters standing in for external effects.

MCP, React UI, the remaining six end-to-end demo paths, restart-recovery rehearsal, and the complete demo script remain to be implemented.
