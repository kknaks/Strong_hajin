"""PRODUCTION 에서도 API·WS 라우트가 «등록»된다 — 막는 것은 등록이 아니라 권한이다.

WORK-006(데스크톱 래퍼)이 여는 것은 **배포된 운영 웹 그 자체**다. 그런데 이 앱은 그동안
`AX_PROFILE=production` 이면 라우트를 **하나도** 등록하지 않았다 — 한 `if` 가 「라우트의 존재」와
「누가 부를 수 있나」를 겸했기 때문이다. 그 상태의 운영 서버는 `/health` 만 답하고 제품은 아무것도
못 한다.

그래서 둘을 갈랐다. **등록은 프로파일과 무관**하고, **권한은 종전 그대로** 세션이 정한다.
이 파일이 재는 것은 그 분리가 «양쪽 다» 지켜지는가다 —
① 대표 REST·WS 경로가 PRODUCTION 에 **있다** ② 그런데 세션 없이는 **하나도 통과하지 못한다**
③ 개발·시험 전용 표면(로컬 로그인·데모 계정·페르소나)은 **여전히 닫혀 있다**
④ `Secure` 쿠키 조건은 **바뀌지 않았다**
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.http_auth import cookie_secure
from ax_workspace.modules.meetings.stream import CLOSE_UNAUTHORIZED

# 실제로 붙지 않는 주소다. 등록은 연결 없이도 서야 한다 — 그 사실 자체가 이 파일의 전제다.
UNUSED_DATABASE = "postgresql+psycopg://unused"
PERSONA = {"X-Demo-Persona": "yuna"}


def _production_app():
    return create_app(Settings(RuntimeProfile.PRODUCTION, UNUSED_DATABASE))


def _paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_production_registers_the_rest_surface_the_wrapper_opens() -> None:
    paths = _paths(_production_app())
    # 래퍼가 여는 화면이 실제로 쓰는 대표 경로들. 하나라도 빠지면 그 화면이 죽는다.
    for path in ("/api/tasks", "/api/work-requests", "/api/my-work", "/api/meetings", "/api/organization/members"):
        assert path in paths, f"{path} 가 PRODUCTION 에 등록되지 않았다: {sorted(paths)[:20]}"


def test_production_registers_the_meeting_stream_websocket() -> None:
    """녹음이 오르는 자리다. 이것이 없으면 래퍼의 존재 이유(회의 녹음)가 통째로 사라진다."""
    assert "/api/meetings/{meeting_id}/stream" in _paths(_production_app())


def test_the_meeting_stream_refuses_an_unauthenticated_handshake() -> None:
    """WS 도 «등록되어 있을 뿐» 통과하지 못한다 — 그리고 **닫는 방식까지** 계약이다.

    REST 는 401 로 답하지만 WS 는 HTTP 상태코드를 쓸 수 없다. 그래서 이 통로는 핸드셰이크를
    받아들인 «뒤» `CLOSE_UNAUTHORIZED`(4401) 로 닫는다(SCAX-SPEC-004 §5.3). 여기서 «아무 예외나»
    가 아니라 **그 코드**를 재는 이유는, 연결이 다른 사유(4404 참석 · 4409 상태)로 닫혀도
    시험이 초록이 되어 버리면 「인증이 막았다」를 증명하지 못하기 때문이다.
    """
    client = TestClient(_production_app())
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/api/meetings/{uuid4()}/stream") as socket:
            socket.receive_json()
    assert closed.value.code == CLOSE_UNAUTHORIZED

    # 개발 이음새가 없으므로 페르소나 헤더를 실어도 같은 답이다.
    with pytest.raises(WebSocketDisconnect) as with_persona:
        with client.websocket_connect(f"/api/meetings/{uuid4()}/stream", headers=PERSONA) as socket:
            socket.receive_json()
    assert with_persona.value.code == CLOSE_UNAUTHORIZED


def test_health_stays_outside_the_profile_gate() -> None:
    """probe 가 쓰는 경로. 예전에도 게이트 밖이었고 지금도 그대로다."""
    assert TestClient(_production_app()).get("/health").json()["status"] == "ok"


def test_registered_does_not_mean_reachable_without_a_session() -> None:
    """등록과 권한을 갈랐다는 말의 «나머지 절반». 세션이 없으면 401 이다.

    404 가 아니라 401 이어야 한다 — 404 는 「그런 것 없다」이고 401 은 「당신이 누군지 모른다」다.
    이 앱이 PRODUCTION 에서 세션을 발급하는 route 를 갖지 않으므로(아래 시험), 결과적으로
    **지금은 아무도 통과하지 못한다.**
    """
    client = TestClient(_production_app())
    for path in ("/api/tasks", "/api/work-requests", "/api/my-work", "/api/organization/members"):
        assert client.get(path).status_code == 401, path


def test_the_development_persona_header_proves_nothing_in_production() -> None:
    """개발 이음새는 PRODUCTION 에 «붙지 않는다». 헤더를 실어도 사람이 되지 않는다."""
    app = _production_app()
    assert not hasattr(app.state, "developer_auth")
    client = TestClient(app)
    for path in ("/api/tasks", "/api/work-requests", "/api/my-work"):
        assert client.get(path, headers=PERSONA).status_code == 401, path


def test_local_login_and_demo_shortcuts_stay_closed_in_production() -> None:
    """로그인 방식은 이번 작업이 건드리지 않는다 — 로컬 로그인은 PRODUCTION 에서 계속 닫힌다."""
    app = _production_app()
    assert "/api/auth/login" not in _paths(app)
    answer = TestClient(app).get("/api/auth/providers").json()
    assert answer == {"local": False, "oidc": False}


def test_the_secure_cookie_condition_is_unchanged() -> None:
    """`Secure` 는 PRODUCTION 에서만 붙는다 — 이번 분리가 이 계약을 건드리지 않았다."""
    assert cookie_secure(Settings(RuntimeProfile.PRODUCTION, UNUSED_DATABASE)) is True
    assert cookie_secure(Settings(RuntimeProfile.DEVELOPMENT, UNUSED_DATABASE)) is False
    assert cookie_secure(Settings(RuntimeProfile.TEST, UNUSED_DATABASE)) is False


def test_development_keeps_both_the_routes_and_its_own_seam() -> None:
    """회귀 방지 — 분리가 개발 쪽에서 무언가를 빼앗지 않았다."""
    app = create_app(Settings(RuntimeProfile.DEVELOPMENT, UNUSED_DATABASE))
    paths = _paths(app)
    assert "/api/tasks" in paths and "/api/meetings/{meeting_id}/stream" in paths
    assert "/api/auth/login" in paths          # 로컬 로그인은 개발에서 열려 있다
    assert hasattr(app.state, "developer_auth")  # 페르소나 이음새도 그대로다
