# AGENTS.md

Codex, Claude Code 등 이 저장소에서 작업하는 agent 세션을 위한 공통 규칙이다. Claude Code는 `CLAUDE.md`(이 파일을 `@AGENTS.md`로 import하는 thin wrapper)를 통해 같은 내용을 읽는다.

## 테스트

- 백엔드 테스트는 항상 Makefile 타겟으로 실행한다. `cd backend && uv run pytest ...`처럼 직접 호출하지 않는다 — 병렬 실행(`-n auto --dist worksteal`)이 빠지면 전체 스위트가 훨씬 오래 걸린다.
  - `make test` — 전체 백엔드 스위트(병렬)
  - `make test-unit` — `tests/unit` + `tests/architecture`
  - `make test-contract` — `tests/contract`(병렬)
  - `make test` 와 `make test-contract` 는 **두 패스**다 — 병렬 패스가 `-m "... and not serial"` 로
    돌고, `test-serial` 이 `@pytest.mark.serial` 달린 것만 `-n0` 로 이어 돈다.
    **그 마커의 기준은 한 문장이다**: 「한 테스트가 자기 안에서 **진짜 동시성을 만들고**
    (자식 **프로세스** — 자료·보고서 워커의 `IsolatedWork` spawn · MCP `stdio_client` · 직접 부른
    `subprocess` — **또는** 자기가 직접 띄운 **스레드**) **그 진행을 초 단위 실시간 창으로 잰다」.**
    워커 수가 코어 수에 닿으면 그 창이 스케줄 지터에 먹힌다 — **프로세스냐 스레드냐는 그 원인의
    본질이 아니다**(기준이 「프로세스」였을 때 `Event` 로 5초 창을 재던 회의실 계약이 마커 없이
    새서 `make verify` 두 회차를 다 깼다).
    **새 테스트에 마커를 달 일이 있는지 스스로 판단하지 않아도 된다** — 마커 없이 자식 프로세스를
    띄우거나 **테스트 코드가 직접 스레드를 띄우면** `tests/conftest.py` 의 걸개가 병렬 패스에서 즉시
    실패시키며 무엇을 달아야 하는지 말한다. 스레드 문은 **테스트 코드가 직접 띄운 것만** 본다 —
    anyio(TestClient) · asyncio 기본 executor 처럼 라이브러리 내부가 쓰는 스레드는 건드리지 않는다.
    가르기 자체(마커가 병렬 패스에서 빠지는가·직렬 패스가 이어 도는가)는
    `tests/architecture/test_serial_test_targets.py` 가 지킨다.
  - `make test-scale` / `make test-release` — `@pytest.mark.scale` / `@pytest.mark.release`
  - `make test-postgres` — 실제 PostgreSQL 대상 통합 테스트(`POSTGRES_TEST_URL` 필요)
- 프론트엔드 테스트는 `make frontend-test`(`cd frontend && npm test`)로 실행한다.
- `make verify`는 `test test-scale test-release frontend-test frontend-assets frontend-build`를 순서대로 돌리는 종합 검증이다.
- MCP 도구·HTTP 시그니처를 바꿨다면 `tests/architecture/test_operation_inventory.py`가 `docs/unified-operations-inventory.json`과의 drift를 잡아낸다 — 실패하면 실제/캡처된 스키마를 비교해 diff난 항목만 패치한다(전체 재작성 금지).

## 로컬 스택

- `make local-stack`이 postgres 대기 → API·conversation/material/meeting/report worker·frontend를 함께 띄우고 감독한다. 스키마가 최신이 아니면 시작하지 않는다 — `make sync-demo-schema`(안전한 추가만) 또는 `make reset-demo`(디스포저블 초기화)를 먼저 실행한다.
- 기본은 `127.0.0.1`에만 바인딩된다. LAN/Tailscale 등 내부망에서 접근하려면 `E2E_API_HOST=0.0.0.0 E2E_FRONTEND_HOST=0.0.0.0`을 넘긴다 — 그만큼 이 기기의 방화벽이 허용하는 누구에게나 열리므로 신뢰된 내부망에서만 그렇게 한다.
