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
