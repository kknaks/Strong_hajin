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


def test_a_step_carries_its_own_identity_and_version(tmp_path) -> None:
    """An item is a thing with a history, not a line of text: it knows who wrote it and how many times it moved."""
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"

    created = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()
    assert created["version"] == 1 and created["state"] == "active"
    assert created["created_by"] == "mina" and created["done"] is False

    renamed = client.patch(f"{url}/{created['item_id']}", headers=MINA, json={"expected_version": 1, "text": "자료 정리하기"}).json()
    assert renamed["version"] == 2 and renamed["text"] == "자료 정리하기"
    checked = client.patch(f"{url}/{created['item_id']}", headers=MINA, json={"expected_version": 2, "done": True}).json()
    assert checked["version"] == 3 and checked["completed_by"] == "mina"

    # A change that changes nothing does not invent a new version.
    same = client.patch(f"{url}/{created['item_id']}", headers=MINA, json={"expected_version": 3, "done": True}).json()
    assert same["version"] == 3


def test_two_people_editing_one_step_do_not_overwrite_each_other(tmp_path) -> None:
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"
    item = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()

    assert client.patch(f"{url}/{item['item_id']}", headers=MINA, json={"expected_version": 1, "text": "먼저 고침"}).status_code == 200
    # The second writer answers the version they were shown, which is no longer the one on the row.
    stale = client.patch(f"{url}/{item['item_id']}", headers=MINA, json={"expected_version": 1, "text": "나중에 고침"})
    assert stale.status_code == 422 and "version" in stale.text
    assert client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["checklist"][0]["text"] == "먼저 고침"

    # A caller may also answer the Task version they were shown, and a stale one is refused before anything moves.
    current = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    refused = client.post(url, headers=MINA, json={"text": "새 단계", "expected_task_version": current["version"] - 1})
    assert refused.status_code == 422
    assert client.post(url, headers=MINA, json={"text": "새 단계", "expected_task_version": current["version"]}).status_code == 201


def test_a_step_is_archived_rather_than_erased(tmp_path) -> None:
    """What someone did is not deleted because the step is no longer on the list."""
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"
    kept = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()
    dropped = client.post(url, headers=MINA, json={"text": "필요 없어진 단계"}).json()

    archived = client.delete(f"{url}/{dropped['item_id']}", headers=MINA)
    assert archived.status_code == 200, archived.text
    assert archived.json()["state"] == "archived"

    # Gone from the list and from the progress a person reads.
    view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert [row["text"] for row in view["checklist"]] == ["자료 모으기"]
    assert view["checklist_progress"] == {"done": 0, "total": 1}

    # Still there in what the Task was, with who archived it and when.
    history = client.get(f"/api/tasks/{task['task_id']}/history", headers=MINA).json()
    assert any(
        any(step["item_id"] == dropped["item_id"] for step in row["snapshot"]["checklist"]) for row in history["versions"]
    )
    latest = history["versions"][-1]["snapshot"]["checklist"]
    assert [step["state"] for step in latest] == ["active", "archived"]
    assert any(row["event_kind"] == "task.checklist.archived" for row in history["activity"])
    # Archiving twice is not a second event; the step is already gone from the list.
    assert client.delete(f"{url}/{dropped['item_id']}", headers=MINA).status_code == 404
    assert kept["item_id"] != dropped["item_id"]


def test_the_steps_can_be_put_in_the_order_the_work_actually_happens(tmp_path) -> None:
    client = _client(tmp_path)
    task = _task(client)
    url = f"/api/tasks/{task['task_id']}/checklist"
    first = client.post(url, headers=MINA, json={"text": "자료 모으기"}).json()
    second = client.post(url, headers=MINA, json={"text": "초안 쓰기"}).json()
    third = client.post(url, headers=MINA, json={"text": "검토 요청"}).json()

    moved = client.post(
        f"{url}/order",
        headers=MINA,
        json={"item_ids": [third["item_id"], first["item_id"], second["item_id"]]},
    )
    assert moved.status_code == 200, moved.text
    assert [row["text"] for row in moved.json()["checklist"]] == ["검토 요청", "자료 모으기", "초안 쓰기"]
    assert [row["position"] for row in moved.json()["checklist"]] == [1, 2, 3]

    # The order survives a fresh read, and it moved the Task exactly once.
    view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert [row["text"] for row in view["checklist"]] == ["검토 요청", "자료 모으기", "초안 쓰기"]
    assert view["version"] == moved.json()["task_version"]

    # A partial or unknown order is refused rather than applied to whatever matched.
    assert client.post(f"{url}/order", headers=MINA, json={"item_ids": [first["item_id"]]}).status_code == 422
    assert client.post(
        f"{url}/order", headers=MINA, json={"item_ids": [first["item_id"], second["item_id"], task["task_id"]]}
    ).status_code == 422
    assert client.post(f"{url}/order", headers=JIHO, json={"item_ids": [first["item_id"]]}).status_code == 404
    assert [row["text"] for row in client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["checklist"]] == [
        "검토 요청", "자료 모으기", "초안 쓰기",
    ]
