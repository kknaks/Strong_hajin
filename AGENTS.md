# AGENTS.md

Codex, Claude Code 등 이 저장소에서 작업하는 agent 세션을 위한 공통 규칙이다. Claude Code는 `CLAUDE.md`(이 파일을 `@AGENTS.md`로 import하는 thin wrapper)를 통해 같은 내용을 읽는다.

## 테스트

- 백엔드 테스트는 항상 Makefile 타겟으로 실행한다. `cd backend && uv run pytest ...`처럼 직접 호출하지 않는다 — 병렬 실행(`-n auto --dist worksteal`)이 빠지면 전체 스위트가 훨씬 오래 걸린다.
  - `make test` — 전체 백엔드 스위트(병렬)
  - `make test-unit` — `tests/unit` + `tests/architecture`
  - `make test-contract` — `tests/contract`(병렬)
  - `make test-scale` / `make test-release` — `@pytest.mark.scale` / `@pytest.mark.release`
  - `make test-postgres` — 실제 PostgreSQL 대상 통합 테스트(`POSTGRES_TEST_URL` 필요)
- 프론트엔드 테스트는 `make frontend-test`(`cd frontend && npm test`)로 실행한다.
- `make verify`는 `test test-scale test-release frontend-test frontend-assets frontend-build`를 순서대로 돌리는 종합 검증이다.
- MCP 도구·HTTP 시그니처를 바꿨다면 `tests/architecture/test_operation_inventory.py`가 `docs/unified-operations-inventory.json`과의 drift를 잡아낸다 — 실패하면 실제/캡처된 스키마를 비교해 diff난 항목만 패치한다(전체 재작성 금지).

## 로컬 스택

- `make local-stack`이 postgres 대기 → API·conversation/material/meeting/report worker·frontend를 함께 띄우고 감독한다. 스키마가 최신이 아니면 시작하지 않는다 — `make sync-demo-schema`(안전한 추가만) 또는 `make reset-demo`(디스포저블 초기화)를 먼저 실행한다.
- 기본은 `127.0.0.1`에만 바인딩된다. LAN/Tailscale 등 내부망에서 접근하려면 `E2E_API_HOST=0.0.0.0 E2E_FRONTEND_HOST=0.0.0.0`을 넘긴다 — 그만큼 이 기기의 방화벽이 허용하는 누구에게나 열리므로 신뢰된 내부망에서만 그렇게 한다.
