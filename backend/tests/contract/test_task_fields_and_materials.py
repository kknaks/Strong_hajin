"""Task description/schedule edits, request due dates, and task materials behind the storage port."""
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings))


def test_task_carries_description_and_schedule_and_owner_edits_them(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/tasks",
        headers=MINA,
        json={"title": "제안서 초안", "description": "고객사 요구사항 반영", "start_date": "2026-09-03", "due_date": "2026-09-05"},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["description"] == "고객사 요구사항 반영"
    assert task["start_date"] == "2026-09-03" and task["due_date"] == "2026-09-05"

    edited = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": task["version"], "description": "범위 확정", "due_date": "2026-09-08"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == task["version"] + 1
    assert edited.json()["description"] == "범위 확정"
    assert edited.json()["due_date"] == "2026-09-08"
    assert edited.json()["start_date"] == "2026-09-03"

    stale = client.patch(f"/api/tasks/{task['task_id']}", headers=MINA, json={"expected_version": task["version"], "title": "x"})
    assert stale.status_code == 422
    inverted = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": task["version"] + 1, "start_date": "2026-09-10"},
    )
    assert inverted.status_code == 422
    cleared = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": task["version"] + 1, "clear_due_date": True},
    )
    assert cleared.status_code == 200 and cleared.json()["due_date"] is None


def test_request_due_date_flows_into_the_accepted_task(tmp_path) -> None:
    client = _client(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "검토 요청", "assignee_id": "jiho", "due_date": "2026-09-12", "description": "초안 검토"},
    )
    assert request.status_code == 201, request.text
    assert request.json()["due_date"] == "2026-09-12"
    accepted = client.post(
        f"/api/work-requests/{request.json()['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request.json()["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    task = client.get(f"/api/tasks/{accepted.json()['task_id']}", headers=JIHO).json()
    assert task["due_date"] == "2026-09-12"
    assert task["description"] == "초안 검토"
    assert task["start_date"] is None


def test_materials_upload_download_detach_and_stay_private_to_the_owner(tmp_path) -> None:
    client = _client(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "산출물 업무"}).json()
    task_id = task["task_id"]

    uploaded = client.post(
        f"/api/tasks/{task_id}/materials",
        headers=MINA,
        data={"kind": "output"},
        files={"file": ("결과 보고서.txt", b"hello deliverable", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    material = uploaded.json()
    assert material["kind"] == "output" and material["name"] == "결과 보고서.txt" and material["size_bytes"] == 17
    assert material["source_kind"] == "file" and material["integrity_ref"].startswith("sha256:")
    stored = list((tmp_path / "materials").rglob("*"))
    assert any(path.is_file() for path in stored)

    listed = client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()
    assert [item["material_id"] for item in listed] == [material["material_id"]]

    content = client.get(f"/api/tasks/{task_id}/materials/{material['material_id']}/content", headers=MINA)
    assert content.status_code == 200 and content.content == b"hello deliverable"
    assert content.headers["content-type"].startswith("text/plain")

    assert client.get(f"/api/tasks/{task_id}/materials", headers=JIHO).status_code == 404
    assert client.get(f"/api/tasks/{task_id}/materials/{material['material_id']}/content", headers=JIHO).status_code == 404

    bad_kind = client.post(
        f"/api/tasks/{task_id}/materials",
        headers=MINA,
        data={"kind": "evidence"},
        files={"file": ("x.txt", b"x", "text/plain")},
    )
    assert bad_kind.status_code == 422

    detached = client.post(f"/api/tasks/{task_id}/materials/{material['material_id']}/detach", headers=MINA)
    assert detached.status_code == 200 and detached.json()["removed_at"]
    assert client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json() == []
    assert client.get(f"/api/tasks/{task_id}/materials/{material['material_id']}/content", headers=MINA).status_code == 404


def test_completed_task_can_be_reopened_but_cancelled_stays_terminal(tmp_path) -> None:
    client = _client(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "되돌릴 업무"}).json()
    started = client.post(f"/api/tasks/{task['task_id']}/start", headers=MINA, json={"expected_version": task["version"]}).json()
    done = client.post(f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": started["version"]}).json()
    assert done["state"] == "done"
    reopened = client.post(f"/api/tasks/{task['task_id']}/resume", headers=MINA, json={"expected_version": done["version"]})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["state"] == "in_progress" and reopened.json()["version"] == done["version"] + 1

    cancelled = client.post(
        f"/api/tasks/{task['task_id']}/cancel", headers=MINA, json={"expected_version": reopened.json()["version"]}
    ).json()
    assert cancelled["state"] == "cancelled"
    assert client.post(
        f"/api/tasks/{task['task_id']}/resume", headers=MINA, json={"expected_version": cancelled["version"]}
    ).status_code == 422
