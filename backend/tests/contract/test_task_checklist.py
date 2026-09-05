"""A Task's checklist: the small steps inside one piece of work.

A checklist item is not a Task. It carries no assignment, no lineage and no judgement; it belongs to exactly one Task
and only the person who holds that Task can see or change it.
"""
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))))


def _task(client) -> dict:
    return client.post("/api/tasks", headers=MINA, json={"title": "분기 보고 준비"}).json()


def test_checklist_items_keep_their_order_and_track_progress(tmp_path) -> None:
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"

    assert client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["checklist"] == []

    first = client.post(url, headers=MINA, json={"text": "자료 모으기"})
    assert first.status_code == 201, first.text
    second = client.post(url, headers=MINA, json={"text": "초안 쓰기"})
    third = client.post(url, headers=MINA, json={"text": "검토 요청"})
    assert [item.json()["position"] for item in (first, second, third)] == [1, 2, 3]

    view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert [item["text"] for item in view["checklist"]] == ["자료 모으기", "초안 쓰기", "검토 요청"]
    assert all(item["done"] is False for item in view["checklist"])
    assert view["checklist_progress"] == {"done": 0, "total": 3}

    # Checking one off is recorded with who did it and when, and the progress follows.
    checked = client.patch(f"{url}/{second.json()['item_id']}", headers=MINA, json={"done": True})
    assert checked.status_code == 200, checked.text
    assert checked.json()["done"] is True and checked.json()["completed_by"] == "mina" and checked.json()["completed_at"]
    view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert view["checklist_progress"] == {"done": 1, "total": 3}
    assert [item["done"] for item in view["checklist"]] == [False, True, False]

    # Unchecking clears the completion facts rather than keeping a stale actor.
    unchecked = client.patch(f"{url}/{second.json()['item_id']}", headers=MINA, json={"done": False})
    assert unchecked.json()["done"] is False and unchecked.json()["completed_by"] is None and unchecked.json()["completed_at"] is None
    assert client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["checklist_progress"] == {"done": 0, "total": 3}


def test_an_item_can_be_renamed_and_removed_without_touching_the_others(tmp_path) -> None:
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"
    first = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()
    second = client.post(url, headers=MINA, json={"text": "초안 쓰기"}).json()
    third = client.post(url, headers=MINA, json={"text": "검토 요청"}).json()

    renamed = client.patch(f"{url}/{first['item_id']}", headers=MINA, json={"text": "  자료 정리하기  "})
    assert renamed.status_code == 200 and renamed.json()["text"] == "자료 정리하기"
    assert client.patch(f"{url}/{first['item_id']}", headers=MINA, json={"text": "   "}).status_code == 422

    removed = client.delete(f"{url}/{second['item_id']}", headers=MINA)
    # Removing a step moves the Task, so the answer says which version it moved to.
    assert removed.status_code == 200, removed.text
    assert removed.json()["task_version"] == client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["version"]
    view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert [item["text"] for item in view["checklist"]] == ["자료 정리하기", "검토 요청"]
    # Removing the middle item leaves the survivors' order intact and a new item still lands last.
    assert [item["position"] for item in view["checklist"]] == [1, 3]
    fourth = client.post(url, headers=MINA, json={"text": "제출"}).json()
    assert fourth["position"] == 4
    assert client.delete(f"{url}/{third['item_id']}", headers=MINA).status_code == 200
    assert client.delete(f"{url}/{third['item_id']}", headers=MINA).status_code == 404


def test_only_the_person_holding_the_task_can_see_or_change_its_checklist(tmp_path) -> None:
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"
    item = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()

    # Jiho holds no assignment on Mina's task: the checklist is not readable, writable or deletable.
    assert client.post(url, headers=JIHO, json={"text": "몰래 추가"}).status_code == 404
    assert client.patch(f"{url}/{item['item_id']}", headers=JIHO, json={"done": True}).status_code == 404
    assert client.delete(f"{url}/{item['item_id']}", headers=JIHO).status_code == 404
    assert client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).status_code == 404
    assert [row["text"] for row in client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["checklist"]] == ["자료 모으기"]

    # An item id from another Task is not reachable through this Task's checklist.
    other = client.post("/api/tasks", headers=JIHO, json={"title": "지호의 업무"}).json()
    assert client.patch(f"/api/tasks/{other['task_id']}/checklist/{item['item_id']}", headers=JIHO, json={"done": True}).status_code == 404


def test_a_checklist_item_is_not_a_task_and_never_reaches_the_judgement_ledger(tmp_path) -> None:
    client = _client(tmp_path)
    task = _task(client)
    client.post(f"/api/tasks/{task['task_id']}/checklist", headers=MINA, json={"text": "자료 모으기"})

    assert [row["title"] for row in client.get("/api/my-work", headers=MINA).json()] == ["분기 보고 준비"]
    assert client.get("/api/action-items", headers=MINA).json() == []
    # Empty text is refused, and the text is bounded.
    assert client.post(f"/api/tasks/{task['task_id']}/checklist", headers=MINA, json={"text": "   "}).status_code == 422


def test_the_task_list_carries_the_checklist_count_without_its_items(tmp_path) -> None:
    """A list needs enough for a progress cue and no more; the items stay on the detail read."""
    client = _client(tmp_path)
    empty = _task(client)
    tracked = client.post("/api/tasks", headers=MINA, json={"title": "체크리스트 있는 업무"}).json()
    url = f"/api/tasks/{tracked['task_id']}/checklist"
    first = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()
    client.post(url, headers=MINA, json={"text": "초안 쓰기"})
    client.patch(f"{url}/{first['item_id']}", headers=MINA, json={"done": True})

    rows = {row["task_id"]: row for row in client.get("/api/my-work", headers=MINA).json()}
    assert rows[tracked["task_id"]]["checklist_progress"] == {"done": 1, "total": 2}
    assert rows[empty["task_id"]]["checklist_progress"] == {"done": 0, "total": 0}
    assert "checklist" not in rows[tracked["task_id"]]
    # The detail read still carries the items themselves.
    assert [row["text"] for row in client.get(f"/api/tasks/{tracked['task_id']}", headers=MINA).json()["checklist"]] == ["자료 모으기", "초안 쓰기"]
