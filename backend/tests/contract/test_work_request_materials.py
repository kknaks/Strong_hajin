"""요청 자료 — 발송한 요청에 **2단계로** 붙는 참고 자료 (WORK-003).

생성 payload 에 자료 칸은 없다. 요청을 만들고, 돌아온 `request_id` 로 붙인다 — 내 업무가 지나는
길과 같은 모양이다. 댓글 첨부·판단 근거는 **뜻이 다른 자리**이며 여기서 그 둘을 대용으로 쓰지 않는다.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import AttachmentBindingRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}
MINSEOK = {"X-Demo-Persona": "minseok"}


def _client(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _request(client: TestClient, **fields) -> dict:
    created = client.post(
        "/api/work-requests", headers=MINA, json={"title": "계약 초안 검토", "assignee_id": "jiho", **fields}
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_file_and_a_link_attach_to_the_request_and_come_back_as_one_list(tmp_path) -> None:
    """붙이고 · 목록으로 읽고 · 내려받고 · 뗀다. **생성 payload 에는 자료 이름이 한 개도 없다.**"""
    client, _ = _client(tmp_path)
    request = _request(client)
    rid = request["request_id"]
    assert request["materials"] == []

    uploaded = client.post(
        f"/api/work-requests/{rid}/materials", headers=MINA,
        files={"file": ("초안.txt", "계약 초안".encode(), "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    # 요청 자료는 **참고 자료뿐**이고, 붙은 것은 요청의 회차를 올린다 (업무 자료의 `task_version` 과 같은 자리).
    assert uploaded.json()["kind"] == "input" and uploaded.json()["request_id"] == rid
    assert uploaded.json()["request_version"] == request["version"] + 1

    linked = client.post(
        f"/api/work-requests/{rid}/materials/links", headers=MINA,
        json={"url": "https://example.com/spec", "label": "지난 계약서"},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["source_kind"] == "external_link" and linked.json()["mutable_source"] is True

    listed = client.get(f"/api/work-requests/{rid}/materials", headers=MINA)
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["초안.txt", "지난 계약서"]

    material_id = uploaded.json()["material_id"]
    content = client.get(f"/api/work-requests/{rid}/materials/{material_id}/content", headers=MINA)
    assert content.status_code == 200 and content.content == "계약 초안".encode()

    detached = client.delete(f"/api/work-requests/{rid}/materials/{linked.json()['material_id']}", headers=MINA)
    assert detached.status_code == 200 and detached.json()["removed_at"] is not None
    assert [item["name"] for item in client.get(f"/api/work-requests/{rid}/materials", headers=MINA).json()] == ["초안.txt"]


def test_only_the_requester_attaches_and_only_a_participant_reads(tmp_path) -> None:
    """붙이는 자리는 **보낸 사람 하나**, 읽는 자리는 **참여자 전부** — 요청 조회와 같은 문이다.

    거절된 뒤에도 자료는 요청 기록으로 남아 읽힌다. 바꿀 수만 없다.
    """
    client, _ = _client(tmp_path)
    request = _request(client, cc_member_ids=["yuna"])
    rid = request["request_id"]
    client.post(f"/api/work-requests/{rid}/materials", headers=MINA, files={"file": ("근거.txt", b"why", "text/plain")})

    # 받는 사람도 참조자도 읽는다 — 그러나 붙이지 못한다.
    assert client.get(f"/api/work-requests/{rid}/materials", headers=JIHO).status_code == 200
    assert [item["name"] for item in client.get(f"/api/work-requests/{rid}/materials", headers=YUNA).json()] == ["근거.txt"]
    refused = client.post(f"/api/work-requests/{rid}/materials", headers=JIHO, files={"file": ("x.txt", b"x", "text/plain")})
    assert refused.status_code == 403, refused.text
    # 관계가 없는 사람에게는 **없는 것과 같은 말**로 끝난다 — 존재를 알리지 않는다.
    assert client.get(f"/api/work-requests/{rid}/materials", headers=MINSEOK).status_code == 404

    current = client.get(f"/api/work-requests/{rid}", headers=MINA).json()
    rejected = client.post(
        f"/api/work-requests/{rid}/reject", headers=JIHO,
        json={"expected_version": current["version"], "reason": "일정이 맞지 않습니다"},
    )
    assert rejected.status_code == 200, rejected.text
    # 거절 뒤에도 기록으로 읽힌다. 붙이고 떼는 것만 닫힌다.
    assert [item["name"] for item in client.get(f"/api/work-requests/{rid}/materials", headers=MINA).json()] == ["근거.txt"]
    late = client.post(f"/api/work-requests/{rid}/materials/links", headers=MINA, json={"url": "https://example.com/z", "label": "늦은"})
    assert late.status_code == 409, late.text


def test_accepting_keeps_the_request_binding_and_adds_a_task_binding(tmp_path) -> None:
    """수락이 같은 Attachment 를 업무에도 세운다 — **복사본이 아니라 두 번째 binding 이다.**"""
    client, database_url = _client(tmp_path)
    request = _request(client)
    rid = request["request_id"]
    uploaded = client.post(
        f"/api/work-requests/{rid}/materials", headers=MINA, files={"file": ("사양.txt", b"spec", "text/plain")}
    ).json()

    current = client.get(f"/api/work-requests/{rid}", headers=MINA).json()
    accepted = client.post(f"/api/work-requests/{rid}/accept", headers=JIHO, json={"expected_version": current["version"]})
    assert accepted.status_code == 200, accepted.text
    task_id = accepted.json()["task_id"]

    assert [item["name"] for item in client.get(f"/api/tasks/{task_id}/materials", headers=JIHO).json()] == ["사양.txt"]
    assert [item["name"] for item in client.get(f"/api/work-requests/{rid}/materials", headers=MINA).json()] == ["사양.txt"]
    with make_session_factory(database_url)() as session:
        bindings = session.scalars(
            select(AttachmentBindingRecord).where(
                AttachmentBindingRecord.attachment_id == UUID(uploaded["material_id"])
            )
        ).all()
        # 파일도 무결성 해시도 하나다 — 두 자리가 같은 것을 가리킨다.
        assert {(binding.context_type, binding.context_id) for binding in bindings} == {
            ("work_request", rid), ("task", task_id)
        }
        assert all(binding.unbound_at is None and binding.role == "input" for binding in bindings)


def test_the_request_projection_carries_predecessors_references_and_materials(tmp_path) -> None:
    """생성이 받던 셋이 **조회로 돌아온다** — 생성·발송·조회가 같은 관계를 말한다 (gap C)."""
    client, _ = _client(tmp_path)
    earlier = client.post("/api/tasks", headers=MINA, json={"title": "선행 정리"}).json()
    project = client.post("/api/projects", headers=JIHO, json={"name": "가을 개편"}).json()["project_id"]
    for member_id in ("mina", "jiho"):
        assert client.post(f"/api/projects/{project}/members", headers=JIHO, json={"member_id": member_id}).status_code == 201
    predecessor = client.post("/api/tasks", headers=MINA, json={"title": "규격 확정", "project_id": project}).json()

    request = _request(
        client,
        project_id=project,
        preceding_task_ids=[predecessor["task_id"]],
        reference_task_ids=[earlier["task_id"]],
    )
    rid = request["request_id"]
    assert request["preceding_task_ids"] == [predecessor["task_id"]]
    assert request["reference_task_ids"] == [earlier["task_id"]]

    client.post(f"/api/work-requests/{rid}/materials", headers=MINA, files={"file": ("붙임.txt", b"a", "text/plain")})
    detail = client.get(f"/api/work-requests/{rid}", headers=MINA).json()
    assert detail["preceding_task_ids"] == [predecessor["task_id"]]
    assert detail["reference_task_ids"] == [earlier["task_id"]]
    assert [item["name"] for item in detail["materials"]] == ["붙임.txt"]


def test_a_request_link_opened_through_the_legacy_attachment_path_is_refused_not_a_server_error(tmp_path) -> None:
    """링크는 **내려받을 내용이 없다** — 말이 되는 요청에 서버 잘못이라고 답하지 않는다 (리뷰 F-2).

    요청 자료가 기존 첨부 목록(`material_bindings`)에 서면서 링크도 옛 내려받기 경로에 닿게 됐다.
    링크의 `source_ref` 는 주소이지 storage key 가 아니라, 그대로 저장소에 넘기면 키 검사가
    `ValueError` 로 터져 **500** 이 나갔다. 파일의 내려받기는 그대로여야 한다.
    """
    client, _ = _client(tmp_path)
    rid = _request(client)["request_id"]
    link = client.post(
        f"/api/work-requests/{rid}/materials/links", headers=MINA,
        json={"url": "https://example.com/spec", "label": "지난 계약서"},
    ).json()
    uploaded = client.post(
        f"/api/work-requests/{rid}/materials", headers=MINA, files={"file": ("초안.txt", b"draft", "text/plain")}
    ).json()

    refused = client.get(f"/api/work-requests/{rid}/attachments/{link['attachment_id']}/content", headers=MINA)
    assert refused.status_code == 422, refused.text
    # 파일은 그대로 열린다 — 가드가 내려받기를 막지 않았다.
    opened = client.get(f"/api/work-requests/{rid}/attachments/{uploaded['attachment_id']}/content", headers=MINA)
    assert opened.status_code == 200 and opened.content == b"draft"
    # 새 요청 자료 경로도 같은 답이다 — 두 입구가 같은 사실을 말한다.
    assert client.get(f"/api/work-requests/{rid}/materials/{link['material_id']}/content", headers=MINA).status_code == 422
