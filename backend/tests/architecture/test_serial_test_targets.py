"""마커로 갈라 둔 두 패스가 다시 하나로 섞이거나 한쪽이 사라지지 않게 지킨다.

**기준 한 문장**: 흔들리는 부류는 「한 테스트가 자기 안에서 **진짜 동시성을 만들고**
(자식 **프로세스** — 자료·보고서 워커의 `IsolatedWork` spawn · MCP `stdio_client` · 직접 부른
`subprocess` — **또는** 자기가 직접 띄운 **스레드**) **그 진행을 초 단위 실시간 창으로 재는**」
테스트다. `-n auto`(= 코어 수) 아래서는 「워커 수 × 각자가 만든 동시성」이 그 창보다 큰 스케줄
지터를 만든다. **프로세스냐 스레드냐는 원인의 본질이 아니다** — 기준이 「프로세스」였을 때
`test_meeting_rooms` 의 스레드 5초 창이 마커 없이 새서 `make verify` 두 회차를 다 깼다.

그래서 **파일 이름을 세지 않는다**: 그 부류는 `@pytest.mark.serial` 을 달고 `Makefile` 이 `-m` 으로
가른다. 이 파일은 그 가르기가 성립하는지를 지킨다 — 병렬 패스가 마커를 빼는가, 직렬 패스가 마커만
`-n0` 로 드는가, 두 패스가 **한 명령 안에서 이어 도는가**, 그리고 `-m` 이 `addopts` 를 덮으면서
기본 제외(integration·release·scale)를 잃지 않는가. 부류에 드는지 자체는 `tests/conftest.py` 의
걸개가 실행 시점에 지킨다(마커 없이 동시성을 만들면 병렬 패스에서 즉시 실패한다).
"""
from pathlib import Path
import re

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = BACKEND_ROOT.parent / "Makefile"
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
CONFTEST = BACKEND_ROOT / "tests" / "conftest.py"
PARALLEL_TARGETS = ("test", "test-contract")
SERIAL_TARGET = "test-serial"


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _recipe(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\n((?:\t.*\n?)+)", _makefile(), re.M)
    assert match, f"Makefile target {name} is missing"
    return match.group(1)


def _variable(name: str) -> str:
    match = re.search(rf"^{re.escape(name)} = (.+)$", _makefile(), re.M)
    assert match, f"Makefile no longer defines {name}"
    value = match.group(1)
    # 한 겹 펼쳐서 실제로 pytest 에 가는 문장을 본다.
    while "$(" in value:
        expanded = re.sub(r"\$\(([A-Z_]+)\)", lambda m: _variable(m.group(1)), value)
        if expanded == value:
            break
        value = expanded
    return value


def test_the_serial_marker_is_registered_with_the_widened_reason() -> None:
    """등록되지 않은 마커는 오타 하나로 조용히 아무것도 고르지 않는다.

    그리고 기준은 **프로세스와 스레드를 모두** 말해야 한다 — 「프로세스」로만 적혀 있던 동안
    스레드로 5초 창을 재던 계약이 마커 없이 병렬 패스에 남아 두 회차를 다 깼다.
    """
    entry = re.search(r'^\s*"serial: (.+)",$', PYPROJECT.read_text(encoding="utf-8"), re.M)
    assert entry, "pyproject 의 markers 에 `serial` 과 그 기준이 적혀 있어야 한다"
    reason = entry.group(1)
    for word in ("child process", "thread", "wall clock"):
        assert word in reason, f"serial 마커의 기준에 «{word}» 가 빠졌다: {reason}"


def test_parallel_pass_deselects_the_serial_marker() -> None:
    for target in PARALLEL_TARGETS:
        recipe = _recipe(target)
        assert "-n auto" in recipe, f"{target} 가 더 이상 병렬이 아니다"
        assert '-m "$(PARALLEL_MARKERS)"' in recipe, f"{target} 가 마커로 가르지 않는다"
    assert "not serial" in _variable("PARALLEL_MARKERS"), (
        "병렬 패스가 자식을 띄우는 부류를 다시 집어 든다"
    )


def test_serial_pass_takes_exactly_the_marked_tests_without_xdist() -> None:
    recipe = _recipe(SERIAL_TARGET)
    assert '-m "$(SERIAL_MARKERS)"' in recipe, "직렬 타겟이 마커로 고르지 않는다"
    # `-n0` 하나가 실효 스위치다. `-n auto` 가 남아 있으면 갈라 둔 뜻이 없다.
    assert "-n0" in recipe and "-n auto" not in recipe
    assert _variable("SERIAL_MARKERS").startswith("serial and"), (
        "직렬 패스가 마커 밖의 것까지 든다"
    )


def test_both_marker_expressions_keep_the_default_deselect() -> None:
    """`-m` 은 pyproject 의 `addopts` 를 **덮는다** — 기본 제외를 다시 적지 않으면 조용히 넓어진다."""
    addopts = re.search(r'^addopts = "(.+)"$', PYPROJECT.read_text(encoding="utf-8"), re.M)
    assert addopts, "pyproject 가 더 이상 addopts 를 갖지 않는다"
    excluded = set(re.findall(r"not (\w+)", addopts.group(1)))
    assert excluded, "addopts 가 아무것도 제외하지 않는다 — 이 테스트의 전제를 다시 봐라"
    for name in ("PARALLEL_MARKERS", "SERIAL_MARKERS"):
        expression = _variable(name)
        missing = [marker for marker in excluded if f"not {marker}" not in expression]
        assert not missing, f"{name} 가 기본 제외 {missing} 를 잃었다 — `-m` 이 addopts 를 덮는다"


def test_parallel_targets_run_the_serial_pass_next() -> None:
    """빼기만 하고 끝나면 그 계약은 아무 데서도 돌지 않는다."""
    for target in PARALLEL_TARGETS:
        assert re.search(rf"\$\(MAKE\)[^\n;]*\b{re.escape(SERIAL_TARGET)}\b", _recipe(target)), (
            f"{target} 가 직렬 패스를 이어 부르지 않는다 — 마커 붙은 테스트가 한 번도 돌지 않는다"
        )


def test_the_split_names_no_test_file() -> None:
    """부류를 **기준**으로 정의한다는 것이 이 가르기의 요점이다 — 이름 목록으로 돌아가면 또 샌다."""
    for target in PARALLEL_TARGETS + (SERIAL_TARGET,):
        named = re.findall(r"tests/\S+\.py", _recipe(target))
        assert not named, f"{target} 가 파일 이름을 세고 있다: {named}"


def test_the_runtime_guard_watches_every_door_into_real_concurrency() -> None:
    """마커를 빠뜨린 새 테스트는 흔들리는 대신 병렬 패스에서 즉시 실패해야 한다.

    문은 넷이다 — 프로세스 둘(`multiprocessing` · `subprocess`)과 스레드 둘
    (`threading.Thread.start` · `ThreadPoolExecutor.submit`).
    """
    guard = CONFTEST.read_text(encoding="utf-8")
    assert 'get_closest_marker("serial")' in guard, "걸개가 더 이상 마커를 보지 않는다"
    assert "workerinput" in guard, "걸개가 병렬 패스인지(xdist 워커인지)를 보지 않는다"
    for door in (
        'setattr(_mp_process.BaseProcess, "start"',
        'setattr(_subprocess.Popen, "__init__"',
        'setattr(_threading.Thread, "start"',
        'setattr(_cf_thread.ThreadPoolExecutor, "submit"',
    ):
        assert door in guard, f"걸개가 동시성으로 가는 문 하나를 놓쳤다: {door}"


def test_the_thread_door_only_bites_what_the_test_itself_started() -> None:
    """스레드는 프로세스와 달리 **라이브러리 내부가 늘 쓴다**.

    anyio 의 blocking portal·worker thread(TestClient 가 쓴다) · asyncio 의 기본 executor 까지
    물면 멀쩡한 테스트가 다 빨개진다. 그래서 스레드 문의 판정은 하나다 —
    **부른 프레임이 `backend/tests/` 안인가, 아니면 그 스레드가 돌릴 함수가 거기 정의됐는가.**
    """
    guard = CONFTEST.read_text(encoding="utf-8")
    assert "_TESTS_ROOT" in guard, "스레드 문이 테스트 코드의 경계를 갖고 있지 않다"
    for judgement in ("_called_from_test_code", "_defined_in_test_code"):
        assert f"{judgement}(" in guard, f"스레드 문이 판정 «{judgement}» 를 잃었다 — 라이브러리 내부까지 문다"
    thread_door = guard[guard.index("def thread_start(self):"):]
    assert "_called_from_test_code(1)" in thread_door.split("def submit(")[0], (
        "스레드 문이 «바로 그» 프레임이 아니라 스택 전체를 본다 — TestClient 를 쓰는 모든 테스트가 걸린다"
    )


def test_verify_still_goes_through_the_split_target() -> None:
    """`make verify` 의 초록은 `test` 한 줄에 달려 있다 — 갈라 둔 두 패스가 모두 그 안에 있어야 한다."""
    match = re.search(r"^verify: (.+)$", _makefile(), re.M)
    assert match, "Makefile no longer defines verify"
    assert "test" in match.group(1).split()
