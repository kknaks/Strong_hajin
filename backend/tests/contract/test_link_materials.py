"""참고 자료는 파일만이 아니다.

Real work lives behind links as often as behind files: a design file, a doc, a dashboard, a pull request. The canonical
design admits `source_kind=file|external_link|resource_ref`, so a Task can point at a URL without SCAX pretending to
hold its bytes. What it must never do is let a link look like a frozen artifact: nothing was fetched and no revision
was pinned, so it is a changeable link and every surface says so.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import AttachmentRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _task(client, title: str = "링크가 붙는 업무") -> str:
    return client.post("/api/tasks", headers=MINA, json={"title": title}).json()["task_id"]


def _link(client, task_id: str, headers=MINA, **body):
    return client.post(f"/api/tasks/{task_id}/materials/links", headers=headers, json=body)


def test_a_task_can_point_at_work_that_lives_somewhere_else(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    task_id = _task(client)

    attached = _link(client, task_id, kind="input", url="https://docs.example.com/spec/v2", label="설계 문서")
    assert attached.status_code == 201, attached.text
    material = attached.json()
    assert material["source_kind"] == "external_link" and material["kind"] == "input"
    assert material["name"] == "설계 문서" and material["url"] == "https://docs.example.com/spec/v2"
    # Nothing was fetched, so nothing may look like a frozen artifact.
    assert material["mutable_source"] is True
    assert material["size_bytes"] == 0
    assert not material["integrity_ref"].startswith("sha256:")

    [listed] = client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()
    # The same material either way. Only the attach answer carries the Task version it moved to; a read has none.
    assert material["task_version"] == 2
    volatile = {"created_at", "task_version"}
    assert {key: value for key, value in listed.items() if key not in volatile} == {
        key: value for key, value in material.items() if key not in volatile
    }
    assert "task_version" not in listed
    with make_session_factory(database_url)() as session:
        [row] = session.scalars(select(AttachmentRecord)).all()
        assert row.source_kind == "external_link" and row.source_ref == "https://docs.example.com/spec/v2"
        assert row.size_bytes == 0

    # Outputs live behind links too.
    delivered = _link(client, task_id, kind="output", url="https://github.example.com/pr/42", label="구현 PR")
    assert delivered.status_code == 201 and delivered.json()["kind"] == "output"
    assert {row["name"] for row in client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()} == {"설계 문서", "구현 PR"}


def test_a_link_is_never_dressed_up_as_a_file(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    material = _link(client, task_id, kind="input", url="https://docs.example.com/spec", label="설계 문서").json()

    # There are no bytes to download.
    content = client.get(f"/api/tasks/{task_id}/materials/{material['material_id']}/content", headers=MINA)
    assert content.status_code == 422

    # And nothing to read, so a search says it could not be read rather than pretending it found nothing.
    found = client.get(f"/api/tasks/{task_id}/materials/search", headers=MINA, params={"q": "설계"})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["results"] == []
    assert [row["name"] for row in body["unavailable_materials"]] == ["설계 문서"]
    assert body["unavailable_materials"][0]["reason"] == "external_link"


def test_a_link_must_be_a_link_and_must_not_carry_a_secret(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    task_id = _task(client)

    for label, body in (
        ("빈 URL", {"kind": "input", "url": "", "label": "무엇"}),
        ("http가 아님", {"kind": "input", "url": "ftp://files.example.com/a", "label": "무엇"}),
        ("스킴 없음", {"kind": "input", "url": "docs.example.com/a", "label": "무엇"}),
        ("자바스크립트", {"kind": "input", "url": "javascript:alert(1)", "label": "무엇"}),
        ("자격 증명 포함", {"kind": "input", "url": "https://user:secret@docs.example.com/a", "label": "무엇"}),
        ("라벨 없음", {"kind": "input", "url": "https://docs.example.com/a", "label": "   "}),
        ("알 수 없는 종류", {"kind": "evidence", "url": "https://docs.example.com/a", "label": "무엇"}),
    ):
        refused = _link(client, task_id, **body)
        assert refused.status_code == 422, f"{label}: {refused.text}"

    with make_session_factory(database_url)() as session:
        assert session.scalars(select(AttachmentRecord)).all() == []
    assert client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json() == []


def test_only_the_person_holding_the_task_may_attach_or_remove_a_link(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    # Someone else's task answers as one that is not there: no role, no existence, nothing to learn.
    assert _link(client, task_id, JIHO, kind="input", url="https://docs.example.com/a", label="남의 업무").status_code in {403, 404, 422}
    assert client.get(f"/api/tasks/{task_id}/materials", headers=JIHO).status_code in {403, 404, 422}

    material = _link(client, task_id, kind="input", url="https://docs.example.com/a", label="설계 문서").json()
    removed = client.post(f"/api/tasks/{task_id}/materials/{material['material_id']}/detach", headers=MINA)
    assert removed.status_code == 200, removed.text
    assert client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json() == []


def test_the_same_link_attached_again_is_a_new_binding_not_a_rewritten_one(tmp_path) -> None:
    """A URL that changes is a new material, never an overwrite of what someone already looked at."""
    client, database_url = _stack(tmp_path)
    first, second = _task(client, "첫 업무"), _task(client, "둘째 업무")
    url = "https://docs.example.com/shared"

    one = _link(client, first, kind="input", url=url, label="공유 문서").json()
    two = _link(client, second, kind="input", url=url, label="같은 문서").json()

    # One artifact identity, two places it is used.
    assert one["attachment_id"] == two["attachment_id"]
    assert one["material_id"] != two["material_id"]
    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(AttachmentRecord)).all()) == 1

    # Pointing the task at a different URL adds a material; it does not rewrite the one that was there.
    moved = _link(client, first, kind="input", url="https://docs.example.com/shared-v2", label="공유 문서 v2").json()
    assert moved["attachment_id"] != one["attachment_id"]
    assert {row["url"] for row in client.get(f"/api/tasks/{first}/materials", headers=MINA).json()} == {url, "https://docs.example.com/shared-v2"}


def _reference(client, task_id: str, headers=MINA, **body):
    return client.post(f"/api/tasks/{task_id}/materials/references", headers=headers, json=body)


def test_a_task_can_point_at_another_thing_inside_scax(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    subject = _task(client, "먼저 한 업무")
    task_id = _task(client, "이어서 하는 업무")

    attached = _reference(client, task_id, kind="input", resource_type="task", resource_id=subject)
    assert attached.status_code == 201, attached.text
    material = attached.json()
    assert material["source_kind"] == "resource_ref" and material["kind"] == "input"
    # The reference resolves now, through the same authorization the resource itself uses.
    assert material["resource"] == {"type": "task", "id": subject, "title": "먼저 한 업무"}
    assert material["name"] == "먼저 한 업무" and material["url"] is None
    assert material["mutable_source"] is True and material["size_bytes"] == 0

    with make_session_factory(database_url)() as session:
        [row] = [item for item in session.scalars(select(AttachmentRecord)).all() if item.source_kind == "resource_ref"]
        assert row.source_ref == f"task:{subject}"

    # Renaming the referenced work is not a rewrite of this material; the reference still resolves to the truth.
    version = client.get(f"/api/tasks/{subject}", headers=MINA).json()["version"]
    client.patch(f"/api/tasks/{subject}", headers=MINA, json={"expected_version": version, "title": "이름이 바뀐 업무"})
    [listed] = [row for row in client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json() if row["source_kind"] == "resource_ref"]
    assert listed["resource"]["title"] == "이름이 바뀐 업무"


def test_a_reference_can_only_point_at_something_the_person_may_read(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    mine = _task(client, "내 업무")
    theirs = client.post("/api/tasks", headers=JIHO, json={"title": "지호의 업무"}).json()["task_id"]

    refused = _reference(client, mine, kind="input", resource_type="task", resource_id=theirs)
    assert refused.status_code in {403, 404, 422}, refused.text
    assert client.get(f"/api/tasks/{mine}/materials", headers=MINA).json() == []

    for label, body in (
        ("알 수 없는 종류", {"kind": "input", "resource_type": "workflow", "resource_id": mine}),
        ("없는 자원", {"kind": "input", "resource_type": "task", "resource_id": "11111111-1111-4111-8111-111111111111"}),
        ("자기 자신", {"kind": "input", "resource_type": "task", "resource_id": mine}),
    ):
        assert _reference(client, mine, **body).status_code in {403, 404, 422}, label
    assert client.get(f"/api/tasks/{mine}/materials", headers=MINA).json() == []


def test_a_reference_says_nothing_about_work_the_reader_may_no_longer_open(tmp_path) -> None:
    """A material row must not become a way to read a title you lost access to."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "비밀 요청", "assignee_id": "jiho"}).json()
    [item] = client.get("/api/action-items", headers=JIHO).json()
    client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept", headers=JIHO,
        json={"expected_version": item["expected_version"]},
    )
    [derived] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "비밀 요청"]

    # 지호 holds the derived task and may reference it from another task of his own.
    holder_task = client.post("/api/tasks", headers=JIHO, json={"title": "지호의 정리 업무"}).json()["task_id"]
    attached = _reference(client, holder_task, JIHO, kind="input", resource_type="task", resource_id=derived["task_id"])
    assert attached.status_code == 201, attached.text
    assert attached.json()["resource"]["title"] == "비밀 요청"

    # Someone with no relationship to the holder's task cannot read the material list at all.
    assert client.get(f"/api/tasks/{holder_task}/materials", headers={"X-Demo-Persona": "sora"}).status_code in {403, 404, 422}
