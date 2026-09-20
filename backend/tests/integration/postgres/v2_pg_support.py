"""WORK-002 v2 의 **PostgreSQL 경합 시험**이 함께 쓰는 자리 (WORK-002 Phase 8 · §검증 계획).

이 모듈은 테스트를 담지 않는다 — 이름이 `test_` 로 시작하지 않는 것은 그래서다. 여기 있는 것은
격리 PG 에 앱을 세우는 자리, 실제 HTTP 명령을 부르는 **얇은 감싸개**, 그리고 **두 transaction 을
실제로 겹치게 만드는 잠금 도구**뿐이다.

**감싸개는 계약을 대신하지 않는다.** 어느 함수도 application·facade 의 private helper 로 상태를
세우지 않는다 — 전부 `TestClient` 를 지나는 REST 호출이고, 단언은 부르는 테스트가 한다.

계약 테스트(`tests/contract/test_task_lifecycle_v2.py`)와 **같은 모양의 감싸개**를 쓰되 stack 만
다르다: 저쪽은 SQLite 한 벌이고 여기는 격리 PostgreSQL 이다. 저쪽이 못 박는 것은 「두 명령이 순서대로
들어왔을 때 남는 상태」이고, 여기서 못 박는 것은 **같은 순간의 두 transaction** 이다.
"""

from __future__ import annotations

import tempfile
from typing import Any

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

#: 시드 조직 (`bootstrap/seed.py`) — 계약 테스트와 같은 사람들이다.
YUNA = {"X-Demo-Persona": "yuna"}        # 대표
JIHO = {"X-Demo-Persona": "jiho"}        # 제품팀장 — work_request.decide · task.assign
MINA = {"X-Demo-Persona": "mina"}        # 제품팀원
MINSEOK = {"X-Demo-Persona": "minseok"}  # 재무팀원


def pg_stack(database_url: str) -> TestClient:
    """격리 PostgreSQL 한 벌 위에 앱 하나. **여기서만 스키마를 초기화한다.**"""
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp())
    return TestClient(create_app(settings))


# ---- 얇은 감싸개 — 전부 실제 REST 입구다 ------------------------------------


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
    created = client.post(
        "/api/work-requests", headers=headers, json={"title": title, "assignee_id": assignee_id, **body}
    )
    assert created.status_code == 201, created.text
    return created.json()


def request_version(client: TestClient, request_id: str, headers: dict[str, str]) -> int:
    response = client.get(f"/api/work-requests/{request_id}", headers=headers)
    assert response.status_code == 200, response.text
    return int(response.json()["version"])


def request_state(client: TestClient, request_id: str, headers: dict[str, str]) -> str:
    response = client.get(f"/api/work-requests/{request_id}", headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["state"])


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


def complete(client: TestClient, task_id: str, headers: dict[str, str]):
    return client.post(
        f"/api/tasks/{task_id}/complete", headers=headers, json={"expected_version": version(client, task_id, headers)}
    )


def reassign(client: TestClient, task_id: str, assignee_id: str, headers: dict[str, str]) -> dict[str, Any]:
    proposed = client.post(
        f"/api/tasks/{task_id}/reassign",
        headers=headers,
        json={
            "expected_version": version(client, task_id, headers),
            "assignee_id": assignee_id,
            "reason": "일정이 바뀌었습니다",
        },
    )
    assert proposed.status_code == 200, proposed.text
    return proposed.json()


def assignment_ledger(client: TestClient, task_id: str, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(f"/api/tasks/{task_id}/assignments", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()
