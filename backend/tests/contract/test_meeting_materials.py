"""회의 자료 · 볼 수 있는 사람 · 회의 원문 검색 (SCAX-SPEC-004 §3.2 · §5.7 · §10 · SCAX-WP-005).

자료는 회의를 보며 열어 놓는 파일이다 — 보관하는 자리가 아니다. 되는 것만 붙고 안 되는 것은 사유가 남는다.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.materials import MAX_MEETING_MATERIAL_BYTES

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        materials_dir=str(tmp_path / "materials"),
        recordings_dir=str(tmp_path / "audio"),
    )
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, settings


def _meeting(client: TestClient, *, attendees=("jiho",)) -> dict:
    starts = datetime.now(UTC) + timedelta(minutes=5)
    return client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "자료를 붙일 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": list(attendees),
            "agendas": [{"title": "첫 안건"}],
        },
    ).json()


def _attach(client: TestClient, meeting_id: str, files, *, headers=MINA):
    return client.post(f"/api/meetings/{meeting_id}/materials", headers=headers, files=files)


def _file(name: str, data: bytes, content_type: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, data, content_type))


# --------------------------------------------------------------------- Phase 1 · 자료


def test_several_files_go_up_at_once_and_come_back_as_the_meeting_materials(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]

    answer = _attach(
        client,
        meeting_id,
        [_file("계약서.pdf", PDF, "application/pdf"), _file("메모.md", "# 정리\n한 줄".encode(), "text/markdown")],
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["failed"] == []
    assert {row["name"] for row in body["attached"]} == {"계약서.pdf", "메모.md"}
    for row in body["attached"]:
        assert set(row) == {"material_id", "name", "content_type", "size", "uploaded_by", "uploaded_at", "can_detach"}
        assert row["uploaded_by"] == "mina" and row["size"] > 0
        # 저장 위치는 나가지 않는다.
        assert "storage_key" not in row and "source_ref" not in row

    listed = client.get(f"/api/meetings/{meeting_id}/materials", headers=MINA)
    assert listed.status_code == 200
    assert {row["name"] for row in listed.json()} == {"계약서.pdf", "메모.md"}


def test_what_the_demo_does_not_take_comes_back_with_a_reason_and_the_rest_still_goes_up(tmp_path) -> None:
    """되는 것만 붙고 안 되는 것은 **왜 안 됐는지**와 함께 돌아온다 (SPEC §10)."""
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]

    answer = _attach(
        client,
        meeting_id,
        [
            _file("좋은.pdf", PDF, "application/pdf"),
            _file("사진.png", b"\x89PNG\r\n", "image/png"),
            _file("큰.pdf", b"x" * (MAX_MEETING_MATERIAL_BYTES + 1), "application/pdf"),
        ],
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert [row["name"] for row in body["attached"]] == ["좋은.pdf"]
    assert {(row["name"], row["reason"]) for row in body["failed"]} == {
        ("사진.png", "unsupported_type"),
        ("큰.pdf", "too_large"),
    }


def test_a_request_where_nothing_could_be_attached_is_a_failed_request(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]

    answer = _attach(client, meeting_id, [_file("사진.png", b"\x89PNG", "image/png")])
    assert answer.status_code == 422
    detail = answer.json()["detail"]
    assert detail["code"] == "meeting_materials_rejected"
    assert detail["failed"] == [{"name": "사진.png", "reason": "unsupported_type"}]
    assert client.get(f"/api/meetings/{meeting_id}/materials", headers=MINA).json() == []


def test_a_shared_viewer_reads_the_materials_and_an_outsider_is_not_told_they_exist(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    material = _attach(client, meeting_id, [_file("공유될.pdf", PDF, "application/pdf")]).json()["attached"][0]
    content = f"/api/meetings/{meeting_id}/materials/{material['material_id']}/content"

    assert client.get(f"/api/meetings/{meeting_id}/materials", headers=JIHO).status_code == 200
    assert client.get(content, headers=JIHO).content == PDF
    assert client.get(f"/api/meetings/{meeting_id}/materials", headers=SORA).status_code == 404
    assert client.get(content, headers=SORA).status_code == 404

    assert client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora"]}).status_code == 200
    assert client.get(f"/api/meetings/{meeting_id}/materials", headers=SORA).status_code == 200
    assert client.get(content, headers=SORA).content == PDF
    # 공유는 열람만 연다 — 붙이거나 떼지 못한다.
    assert _attach(client, meeting_id, [_file("소라.pdf", PDF, "application/pdf")], headers=SORA).status_code == 404


def test_taking_a_file_off_a_meeting_leaves_the_bytes_where_they_were(tmp_path) -> None:
    """binding 만 끊는다 — 원본과 그 자료의 다른 자리는 그대로다."""
    client, application, settings = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    material = _attach(client, meeting_id, [_file("떼어낼.pdf", PDF, "application/pdf")]).json()["attached"][0]

    from ax_workspace.platform.persistence import AttachmentRecord

    with application._session_factory() as session:
        stored = session.get(AttachmentRecord, UUID(material["material_id"]))
        key = stored.source_ref
    assert client.delete(f"/api/meetings/{meeting_id}/materials/{material['material_id']}", headers=MINA).status_code == 204
    with application._session_factory() as session:
        assert session.get(AttachmentRecord, UUID(material["material_id"])) is not None
    assert application._material_storage.get(key) == PDF


# --------------------------------------------------------------------- Phase 2 · 볼 수 있는 사람


def test_the_viewer_list_says_who_is_there_and_who_was_shown(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]

    before = client.get(f"/api/meetings/{meeting_id}/shares", headers=MINA)
    assert before.status_code == 200
    assert {(row["member_id"], row["basis"]) for row in before.json()} == {("mina", "attendee"), ("jiho", "attendee")}
    assert all(row["name"] for row in before.json())

    after = client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora", "minseok"]})
    assert after.status_code == 200
    assert {(row["member_id"], row["basis"]) for row in after.json()} == {
        ("mina", "attendee"), ("jiho", "attendee"), ("sora", "share"), ("minseok", "share"),
    }


def test_a_shared_meeting_lands_in_the_past_column_and_not_on_the_calendar(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora"]})

    board = client.get("/api/meetings", headers=SORA).json()
    assert board["upcoming"] == []
    assert [row["viewer_relation"] for row in board["past"]["items"]] == ["shared"]
    # 캘린더에는 서지 않는다 — 공유는 목록에 담기는 것이 전부다 (SPEC §3.2-5).
    calendar = application.list_meetings(application.authenticated_principal("sora"))
    assert all(row.get("meeting_id") != meeting_id for row in calendar if row.get("kind") == "meeting")


# --------------------------------------------------------------------- Phase 3 · 원문 검색 다리


def _worker(application, settings):
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker

    return MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)


def _indexed(application, settings, principal, query: str, **filters) -> list[dict]:
    """회의 원문은 **처음 읽힐 때 자료가 된다** — 검색이 그 자리를 세우고 추출이 뒤따른다.

    첫 검색이 자료를 등록하고 잡을 걸며, 워커가 돌고 나면 두 번째 검색이 그것을 찾는다.
    """
    worker = _worker(application, settings)
    for _ in range(6):
        found = application.search_materials(principal, query, **filters)
        if found["results"]:
            return found["results"]
        if not asyncio.run(worker.run_once()):
            break
    return application.search_materials(principal, query, **filters)["results"]


def _speech(application, meeting_id: str, texts: list[str]) -> None:
    from sqlalchemy import func, select

    from ax_workspace.platform.persistence import MeetingTranscriptRecord

    with application._session_factory() as session:
        highest = session.scalar(
            select(func.max(MeetingTranscriptRecord.seq)).where(
                MeetingTranscriptRecord.meeting_id == UUID(meeting_id)
            )
        ) or 0
        for index, text in enumerate(texts, start=highest):
            session.add(
                MeetingTranscriptRecord(
                    meeting_id=UUID(meeting_id), seq=index + 1, speaker_label=str(index % 2 + 1),
                    at_ms=index * 65_000, end_ms=index * 65_000 + 4_000, text=text,
                    created_at=datetime.now(UTC),
                )
            )
        session.commit()


@pytest.mark.serial
def test_the_settled_speech_of_a_meeting_is_searchable_by_the_people_who_may_read_it(tmp_path) -> None:
    client, application, settings = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    _speech(application, meeting_id, ["권한모델을회의원문토큰으로정한다", "다음은다른이야기"])
    client.post(f"/api/meetings/{meeting_id}/start", headers=MINA)
    application.meeting_batch.drain()

    principal = application.authenticated_principal("mina")
    results = _indexed(application, settings, principal, "회의원문토큰", resource_types=["meeting"])
    assert len(results) == 1, results
    hit = results[0]
    assert hit["source_resource_type"] == "meeting" and hit["source_resource_id"] == meeting_id
    assert hit["source_locator"]["kind"] == "meeting_transcript"
    # 화자와 시각이 함께 읽힌다 — 스크립트에서 찾아 들을 수 있는 모양이다.
    assert hit["source_locator"]["speaker_label"] == "1" and hit["source_locator"]["start_ms"] == 0

    # 참석자도 읽는다. 참석도 공유도 아닌 사람은 아무것도 못 찾는다.
    assert _indexed(application, settings, application.authenticated_principal("jiho"), "회의원문토큰")
    assert application.search_materials(application.authenticated_principal("sora"), "회의원문토큰")["results"] == []

    client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora"]})
    assert _indexed(application, settings, application.authenticated_principal("sora"), "회의원문토큰")


def test_a_meeting_that_has_not_spoken_yet_has_nothing_to_search(tmp_path) -> None:
    client, application, settings = _stack(tmp_path)
    _meeting(client)
    assert asyncio.run(_worker(application, settings).run_once()) in (True, False)
    principal = application.authenticated_principal("mina")
    assert application.search_materials(principal, "아무말", resource_types=["meeting"])["results"] == []


@pytest.mark.serial
def test_a_file_attached_to_a_meeting_is_searchable_through_the_meeting(tmp_path) -> None:
    client, application, settings = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    _attach(client, meeting_id, [_file("정리.md", "# 회의자료검색토큰\n본문".encode(), "text/markdown")])

    principal = application.authenticated_principal("mina")
    results = _indexed(application, settings, principal, "회의자료검색토큰", resource_types=["meeting"])
    assert len(results) == 1, results
    hit = results[0]
    assert hit["source_resource_id"] == meeting_id
    assert hit["origin"] == f"/api/meetings/{meeting_id}/materials/{hit['material_id']}/content"
    assert client.get(hit["origin"], headers=MINA).status_code == 200
    assert client.get(hit["origin"], headers=SORA).status_code == 404


@pytest.mark.serial
def test_the_transcript_material_follows_the_meeting_as_it_keeps_talking(tmp_path) -> None:
    """회의는 판을 쌓지 않는다 — 자료도 하나이고 내용이 자라면 그 자리를 갱신한다."""
    client, application, settings = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    _speech(application, meeting_id, ["첫번째회의원문덩어리"])
    principal = application.authenticated_principal("mina")
    first = _indexed(application, settings, principal, "첫번째회의원문덩어리", resource_types=["meeting"])
    assert first
    material_id = first[0]["material_id"]

    _speech(application, meeting_id, ["두번째회의원문덩어리"])
    second = _indexed(application, settings, principal, "두번째회의원문덩어리", resource_types=["meeting"])
    # 자료는 같은 하나다 — 새 판이 서지 않는다.
    assert second and second[0]["material_id"] == material_id


def test_taking_a_share_back_answers_with_who_can_still_see_it(tmp_path) -> None:
    """거두고 나서 남은 「볼 수 있는 사람」을 그대로 낸다 — 화면이 다시 물어보지 않는다."""
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora", "minseok"]})

    revoked = client.delete(f"/api/meetings/{meeting_id}/shares/sora", headers=MINA)
    assert revoked.status_code == 200, revoked.text
    rows = revoked.json()
    assert {(row["member_id"], row["basis"]) for row in rows} == {
        ("mina", "attendee"), ("jiho", "attendee"), ("minseok", "share"),
    }
    # `GET /shares` 와 같은 목록이다.
    assert rows == client.get(f"/api/meetings/{meeting_id}/shares", headers=MINA).json()


def test_a_material_opens_in_a_new_tab_rather_than_dropping_into_downloads(tmp_path) -> None:
    """미리보기를 걷고 새 탭으로 여는 이상 `attachment` 로는 안 된다 — 탭이 뜨자마자 내려받기가 된다 (D37)."""
    client, _, _ = _stack(tmp_path)
    meeting_id = _meeting(client)["meeting"]["meeting_id"]
    attached = _attach(
        client,
        meeting_id,
        [_file("계약서.pdf", PDF, "application/pdf"), _file("메모.md", "# 정리".encode(), "text/plain")],
    ).json()["attached"]
    by_name = {row["name"]: row["material_id"] for row in attached}

    pdf = client.get(f"/api/meetings/{meeting_id}/materials/{by_name['계약서.pdf']}/content", headers=MINA)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    # 이름은 그대로 실린다 — 새 탭에서 내려받을 때의 이름을 지킨다.
    assert pdf.headers["content-disposition"] == "inline; filename*=UTF-8''%EA%B3%84%EC%95%BD%EC%84%9C.pdf"

    # 브라우저가 Markdown 을 `text/plain` 으로 올려도, 내보낼 때는 **우리가 아는 형식**으로 낸다.
    markdown = client.get(f"/api/meetings/{meeting_id}/materials/{by_name['메모.md']}/content", headers=MINA)
    assert markdown.headers["content-disposition"].startswith("inline; filename*=UTF-8''")
    assert markdown.headers["content-type"] == "text/markdown; charset=utf-8"
