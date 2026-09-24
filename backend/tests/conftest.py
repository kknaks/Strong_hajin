"""Make shared synthetic fixture builders importable from each test layer."""

from pathlib import Path
import sys


TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))


#: W1 이후 생성 명령은 `Idempotency-Key` 를 **필수**로 받는다 (SPEC-001 §5 · WORK-001 Phase 2).
#: 키는 인증 헤더처럼 모든 호출자가 싣는 전송 계약이라, 그것을 시험 대상으로 삼지 않는 테스트까지
#: 줄마다 키를 적게 하지 않는다 — 여기서 **호출마다 새 키**를 채운다. 새 키이므로 두 호출은 언제나
#: 별개 생성이고, 이 채움이 중복을 감춰 주지 않는다.
#:
#: 키 자체를 시험하는 테스트는 헤더를 **직접 실어** 이 채움을 밀어낸다 — 같은 키를 두 번 보내
#: 영수증을 보거나, 빈 문자열을 실어 누락을 재현한다.
import uuid as _uuid

import pytest as _pytest
from starlette.testclient import TestClient as _TestClient


@_pytest.fixture(autouse=True)
def _fresh_idempotency_key_per_request(request, monkeypatch: "_pytest.MonkeyPatch") -> None:
    # `@pytest.mark.no_auto_idempotency_key` 를 단 테스트는 **헤더가 아예 없는** 호출을 그대로 보낸다 —
    # 키 누락 자체를 시험하는 자리다.
    skip = request.node.get_closest_marker("no_auto_idempotency_key") is not None
    original = _TestClient.request

    def request(self, method, url, *args, **kwargs):
        if str(method).upper() == "POST" and not skip:
            headers = dict(kwargs.get("headers") or {})
            if not any(name.lower() == "idempotency-key" for name in headers):
                headers["Idempotency-Key"] = f"test-{_uuid.uuid4()}"
                kwargs["headers"] = headers
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(_TestClient, "request", request)


#: ── 진짜 동시성을 만들고 초 단위 창을 재는 테스트는 병렬 패스에서 돌지 않는다 ──────────────
#: **기준 한 문장**: 이 저장소에서 흔들리는 계약은 「**한 테스트가 자기 안에서 진짜 동시성을
#: 만들고**(자식 «프로세스» — 자료·보고서 워커의 `IsolatedWork` spawn · MCP `stdio_client` ·
#: 직접 부른 `subprocess` — «또는» 자기가 직접 띄운 «스레드») **그 진행을 초 단위 실시간 창으로
#: 재는**」 부류다. `-n auto`(= 코어 수) 아래서는 「워커 수 × 각자가 만든 동시성」이 그 창보다 큰
#: 스케줄 지터를 만든다(근거·버린 가설: orchestration/work/strong-hajin-projects/
#: phase0-report.md · phase0-followup-report.md · phase0-fix3-report.md).
#:
#: **프로세스냐 스레드냐는 원인의 본질이 아니다** — `test_meeting_rooms` 의
#: `test_expired_replay_waits_for_the_live_room_sync_before_recovery` 는 `Event`·`Lock` 으로
#: 스레드 둘을 돌리고 `release.wait(5)` 로 5초 창을 재는데, 기준이 「프로세스」였을 때 마커가
#: 안 붙어 `make verify` 두 회차에서 두 번 다 깨졌다(단독으로 돌리면 통과한다).
#:
#: 그래서 그 부류는 `@pytest.mark.serial` 을 달고 `Makefile` 이 `-m` 으로 갈라 돈다
#: (병렬 패스 `-m "... and not serial"` → 직렬 패스 `test-serial` 이 `-m serial -n0`).
#: 이 걸개는 **목록이 조용히 새지 않게** 한다: 마커 없는 테스트가 병렬 패스(xdist 워커) 안에서
#: 동시성을 만들려 하면, 흔들리는 대신 **즉시·결정적으로** 실패하고 무엇을 달아야 하는지 말한다.
#: 파일 이름을 하나씩 더하는 대신 «동시성을 만드는가» 라는 기준 자체가 목록을 정의한다.
#:
#: ⚠ **스레드 문은 «테스트 코드가 직접 띄운 것»만 본다.** 스레드는 프로세스와 달리 라이브러리
#: 내부가 늘 쓴다 — 병렬 패스 전체(1447건)를 실측하니 스레드 기동 11,788회 중 11,405회가
#: anyio(TestClient 의 blocking portal·worker thread), 128회가 asyncio 의 기본 executor,
#: 185회가 제품의 회의 배치 백그라운드(`meetings/batch_service.py`)였고, **테스트 코드가 직접
#: 띄운 것은 5회(3건)뿐**이었다. 그래서 판정은 「**`Thread.start()`(또는 `Executor.submit()`)를
#: 부른 «바로 그» 프레임이 `backend/tests/` 안인가, 아니면 그 스레드가 실행할 함수가
#: `backend/tests/` 안에 정의돼 있는가**」 하나다 — anyio·asyncio 내부는 둘 다 아니므로 조용하고,
#: 제품이 스스로 띄우는 백그라운드 스레드(테스트가 기다리지 않는다)도 잡지 않는다.
import concurrent.futures.thread as _cf_thread
import multiprocessing.process as _mp_process
import os as _os
import subprocess as _subprocess
import sys as _sys
import threading as _threading
from pathlib import Path as _Path

#: 이 파일이 있는 곳이 곧 테스트 코드의 경계다.
_TESTS_ROOT = str(_Path(__file__).resolve().parent) + _os.sep


def _defined_in_test_code(function) -> bool:
    """이 스레드가 «실행할» 함수가 테스트 코드인가 — 라이브러리 내부 worker 는 아니다."""
    code = getattr(function, "__code__", None) or getattr(getattr(function, "__func__", None), "__code__", None)
    return code is not None and code.co_filename.startswith(_TESTS_ROOT)


def _called_from_test_code(depth: int) -> bool:
    """`start()`/`submit()` 을 부른 «바로 그» 프레임이 테스트 코드인가.

    `depth` 는 **부르는 쪽 기준**이다(1 = 나를 부른 문 함수의 호출자). 이 함수 자신의 프레임
    한 칸을 여기서 더한다 — 그러지 않으면 이 conftest 가 `backend/tests/` 안이라서 늘 참이 된다.
    """
    frame = _sys._getframe(depth + 1)
    return frame.f_code.co_filename.startswith(_TESTS_ROOT)


@_pytest.fixture(autouse=True)
def _tests_that_make_their_own_concurrency_stay_out_of_the_parallel_pass(request, monkeypatch: "_pytest.MonkeyPatch") -> None:
    # xdist 워커 안이 아니면(= `-n0`·직접 실행·직렬 패스) 아무것도 막지 않는다 — 그 자리에서는
    # 동시성을 만드는 것이 문제가 아니다.
    if getattr(request.config, "workerinput", None) is None:
        return
    if request.node.get_closest_marker("serial") is not None:
        return

    def _refuse(how: str, what: str) -> None:
        _pytest.fail(
            f"{how} 로 **{what}를 띄우는** 테스트가 병렬 패스에서 돌았다. 이 부류는 자기가 만든 동시성의"
            " 진행을 초 단위 실시간 창으로 재기 때문에 워커 수가 코어 수에 닿으면 흔들린다 —"
            " `@pytest.mark.serial` 을 달아 직렬 패스(`make test-serial`)로 보내라."
            " 기준과 근거는 tests/conftest.py 위 주석과 AGENTS.md 「테스트」.",
            pytrace=False,
        )

    started = _mp_process.BaseProcess.start
    opened = _subprocess.Popen.__init__
    thread_started = _threading.Thread.start
    submitted = _cf_thread.ThreadPoolExecutor.submit

    def start(self):
        _refuse(f"multiprocessing.{type(self).__name__}", "자식 프로세스")
        return started(self)

    def popen(self, *args, **kwargs):
        _refuse("subprocess.Popen", "자식 프로세스")
        return opened(self, *args, **kwargs)

    def thread_start(self):
        # 부른 프레임(깊이 1)이 테스트 코드이거나, 스레드가 돌릴 함수가 테스트 코드일 때만 문다.
        if _called_from_test_code(1) or _defined_in_test_code(getattr(self, "_target", None)) or _defined_in_test_code(type(self).run):
            _refuse(f"threading.{type(self).__name__}", "스레드")
        return thread_started(self)

    def submit(self, fn, /, *args, **kwargs):
        if _called_from_test_code(1) or _defined_in_test_code(fn):
            _refuse("concurrent.futures.ThreadPoolExecutor.submit", "스레드")
        return submitted(self, fn, *args, **kwargs)

    monkeypatch.setattr(_mp_process.BaseProcess, "start", start)
    monkeypatch.setattr(_subprocess.Popen, "__init__", popen)
    monkeypatch.setattr(_threading.Thread, "start", thread_start)
    monkeypatch.setattr(_cf_thread.ThreadPoolExecutor, "submit", submit)
