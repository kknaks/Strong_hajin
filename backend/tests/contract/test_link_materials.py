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
    found = client.get('/api/materials/search', headers=MINA, params={'q': '설계', 'resource_type': 'task', 'resource_id': task_id})
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
    removed = client.post(f'/api/tasks/{task_id}/material-bindings/{material['binding_id']}/detach', headers=MINA)
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
    assert one["material_id"] == two["material_id"]
    assert one["binding_id"] != two["binding_id"]
    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(AttachmentRecord)).all()) == 1

    # Pointing the task at a different URL adds a material; it does not rewrite the one that was there.
    moved = _link(client, first, kind="input", url="https://docs.example.com/shared-v2", label="공유 문서 v2").json()
    assert moved["attachment_id"] != one["attachment_id"]
    assert {row["url"] for row in client.get(f"/api/tasks/{first}/materials", headers=MINA).json()} == {url, "https://docs.example.com/shared-v2"}


def _reference(client, task_id: str, headers=MINA, **body):
    return client.post(f"/api/tasks/{task_id}/materials/references", headers=headers, json=body)


def _meeting(client, title: str = "설계 회의") -> str:
    created = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": title,
            # 종료 시각이 지난 빈 회의는 스스로 취소된다 — 이 시험이 보려는 것이 아니므로 앞으로 잡는다.
            "starts_at": "2027-09-10T01:00:00Z",
            "ends_at": "2027-09-10T02:00:00Z",
            "attendee_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["meeting"]["meeting_id"]


def test_a_task_can_point_at_another_thing_inside_scax(tmp_path) -> None:
    """A material reference points at a thing SCAX holds. Earlier work is not one of them — that is a 참고 업무."""
    client, database_url = _stack(tmp_path)
    meeting_id = _meeting(client, "설계 회의")
    task_id = _task(client, "회의에서 나온 업무")

    attached = _reference(client, task_id, kind="input", resource_type="meeting", resource_id=meeting_id)
    assert attached.status_code == 201, attached.text
    material = attached.json()
    assert material["source_kind"] == "resource_ref" and material["kind"] == "input"
    # The reference resolves now, through the same authorization the resource itself uses.
    assert material["resource"] == {"type": "meeting", "id": meeting_id, "title": "설계 회의"}
    assert material["name"] == "설계 회의" and material["url"] is None
    assert material["mutable_source"] is True and material["size_bytes"] == 0

    with make_session_factory(database_url)() as session:
        [row] = [item for item in session.scalars(select(AttachmentRecord)).all() if item.source_kind == "resource_ref"]
        assert row.source_ref == f"meeting:{meeting_id}"

    # Renaming the referenced meeting is not a rewrite of this material; the reference still resolves to the truth.
    renamed = client.patch(
        f"/api/meetings/{meeting_id}",
        headers=MINA,
        json={"title": "이름이 바뀐 회의"},
    )
    assert renamed.status_code == 200, renamed.text
    [listed] = [row for row in client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json() if row["source_kind"] == "resource_ref"]
    assert listed["resource"]["title"] == "이름이 바뀐 회의"
    assert client.get(f"/api/materials/{material['material_id']}", headers=MINA).json()["name"] == "이름이 바뀐 회의"


def test_a_reference_can_only_point_at_something_the_person_may_read(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    mine = _task(client, "내 업무")
    theirs = client.post("/api/tasks", headers=JIHO, json={"title": "지호의 업무"}).json()["task_id"]

    for label, body in (
        ("업무는 자료가 아니라 참고 업무다", {"kind": "input", "resource_type": "task", "resource_id": theirs}),
        ("내 업무도 마찬가지", {"kind": "input", "resource_type": "task", "resource_id": mine}),
        ("알 수 없는 종류", {"kind": "input", "resource_type": "workflow", "resource_id": mine}),
        ("없는 자원", {"kind": "input", "resource_type": "meeting", "resource_id": "11111111-1111-4111-8111-111111111111"}),
    ):
        assert _reference(client, mine, **body).status_code in {403, 404, 422}, label
    assert client.get(f"/api/tasks/{mine}/materials", headers=MINA).json() == []


def test_a_reference_says_nothing_about_a_resource_the_reader_may_not_open(tmp_path) -> None:
    """A material row must not become a way to read the title of something you have no access to."""
    client, _ = _stack(tmp_path)
    private_meeting = _meeting(client, "비공개 회의")
    holder_task = _task(client, "회의 정리 업무")
    attached = _reference(client, holder_task, kind="input", resource_type="meeting", resource_id=private_meeting)
    assert attached.status_code == 201, attached.text
    assert attached.json()["resource"]["title"] == "비공개 회의"

    # Someone with no relationship to the task cannot read the material list at all.
    assert client.get(f"/api/tasks/{holder_task}/materials", headers={"X-Demo-Persona": "sora"}).status_code in {403, 404, 422}

    # And someone who holds a task but may not open the meeting is told so rather than given its title.
    other_task = client.post("/api/tasks", headers=JIHO, json={"title": "지호의 업무"}).json()["task_id"]
    refused = _reference(client, other_task, JIHO, kind="input", resource_type="meeting", resource_id=private_meeting)
    assert refused.status_code in {403, 404, 422}
