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

This first slice establishes the catalog and security boundary. Workflow-run persistence, tool dispatch, MCP, React UI, and the three domain-complete flows remain to be implemented.
