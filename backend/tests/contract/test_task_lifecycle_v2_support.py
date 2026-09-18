"""WORK-002 v2 계약 테스트가 함께 쓰는 도우미 — **실제 HTTP 입구만** 부른다.

이 모듈은 테스트를 담지 않는다. 이름이 `test_` 로 시작하는 것은 같은 폴더의 수집 규칙을 따르기
위해서이고, 여기 있는 것은 stack 을 세우는 자리와 실제 명령을 부르는 **얇은 감싸개**뿐이다.

**감싸개는 계약을 대신하지 않는다.** 어느 함수도 application·facade 의 private helper 를 부르지
않는다 — 전부 `TestClient` 를 지나는 REST 호출이고, 상태·권한·투영·이력은 **호출한 테스트가**
단언한다. 여기서 하는 단언은 「그 앞 준비가 실패하면 그 자리에서 멈춘다」는 것뿐이다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

#: 시드 조직 (`bootstrap/seed.py`) — 지호가 제품팀장이고 민아가 그 팀원이다.
#: `work_request.decide` 는 팀장(`team-lead`)과 대표(`executive`)만 갖는다 —
#: 「수신자가 아닌 사람의 판단」을 시험하려면 **그 권한을 가진 제3자**가 있어야 하고 그 자리가 유나다.
YUNA = {"X-Demo-Persona": "yuna"}        # 대표 — 조직 전체 범위 · work_request.decide
JIHO = {"X-Demo-Persona": "jiho"}        # 제품팀장 — work_request.decide · task.assign
MINA = {"X-Demo-Persona": "mina"}        # 제품팀원
HYEON = {"X-Demo-Persona": "hyeon"}      # 인사 담당자
MINSEOK = {"X-Demo-Persona": "minseok"}  # 재무팀원
SORA = {"X-Demo-Persona": "sora"}        # 외부 법무 자문 — 회의만 본다


def stack(tmp_path) -> tuple[TestClient, str]:
    """계약 테스트의 기존 관례 그대로 — SQLite 하나에 앱 하나. 외부 DB·서비스를 켜지 않는다."""
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def detail(client: TestClient, task_id: str, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def version(client: TestClient, task_id: str, headers: dict[str, str]) -> int:
    return int(detail(client, task_id, headers)["version"])


def own_task(client: TestClient, title: str, headers: dict[str, str] = MINA, **body: Any) -> str:
    created = client.post("/api/tasks", headers=headers, json={"title": title, **body})
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def send_request(
    client: TestClient, title: str, assignee_id: str, headers: dict[str, str] = MINA, **body: Any
) -> dict[str, Any]:
    """발송 — 응답은 요청 묶음이고 `task_id` 로 그 자리에 선 업무를 가리킨다 (SPEC-003 §4)."""
    created = client.post(
        "/api/work-requests", headers=headers, json={"title": title, "assignee_id": assignee_id, **body}
    )
    assert created.status_code == 201, created.text
    return created.json()


def request_version(client: TestClient, request_id: str, headers: dict[str, str]) -> int:
    response = client.get(f"/api/work-requests/{request_id}", headers=headers)
    assert response.status_code == 200, response.text
    return int(response.json()["version"])


def accept_request(client: TestClient, request_id: str, headers: dict[str, str]) -> dict[str, Any]:
    accepted = client.post(
        f"/api/work-requests/{request_id}/accept",
        headers=headers,
        json={"expected_version": request_version(client, request_id, headers)},
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


def start(client: TestClient, task_id: str, headers: dict[str, str]) -> dict[str, Any]:
    started = client.post(
        f"/api/tasks/{task_id}/start", headers=headers, json={"expected_version": version(client, task_id, headers)}
    )
    assert started.status_code == 200, started.text
    return started.json()


def report_completion(client: TestClient, task_id: str, summary: str, headers: dict[str, str]):
    return client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=headers,
        json={"expected_version": version(client, task_id, headers), "summary": summary},
    )


# ---- 판단함 — 요청 수락·완료 확인·담당 제안이 사람에게 서는 자리 ----


def _item(client: TestClient, headers: dict[str, str], kind: str, resource_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in client.get("/api/action-items", headers=headers).json()
            if row["kind"] == kind and row["resource"]["id"] == resource_id
        ),
        None,
    )


def acceptance_item(client: TestClient, headers: dict[str, str], request_id: str) -> dict[str, Any] | None:
    return _item(client, headers, "work_request.acceptance", request_id)


def delivery_item(client: TestClient, headers: dict[str, str], task_id: str) -> dict[str, Any] | None:
    return _item(client, headers, "task.delivery", task_id)


def assignment_item(client: TestClient, headers: dict[str, str], task_id: str) -> dict[str, Any] | None:
    return _item(client, headers, "task.assignment", task_id)


def run_command(client: TestClient, item: dict[str, Any], command: str, headers: dict[str, str], **payload: Any):
    return client.post(
        f"/api/action-items/{item['action_item_id']}/commands/{command}",
        headers=headers,
        json={"expected_version": item["expected_version"], **payload},
    )


def approve_delivery(client: TestClient, task_id: str, headers: dict[str, str]):
    item = delivery_item(client, headers, task_id)
    assert item is not None, "확인할 완료 보고가 판단함에 서 있어야 한다"
    return run_command(client, item, "accept", headers)
