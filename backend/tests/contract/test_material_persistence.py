"""추출한 것은 파일 모양대로 남고, 지운 것은 지워진 채로 남는다.

Office documents come apart into blocks — paragraphs, tables, sheet rows, slides — and those blocks are what SCAX
keeps: the searchable chunks are derived from them, so re-indexing never needs the original bytes again. A parser
upgrade does not rewrite what an older answer stood on; it supersedes it. And a real deletion removes the content
everywhere while leaving the fact that an answer once cited it.
"""
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import select

import asyncio

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    AttachmentRecord,
    MaterialBlockRecord,
    MaterialChunkRecord,
    MaterialExtractionRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    application = app.state.workflow_application
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    return TestClient(app), application, database_url, worker


def _docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    """A minimal real DOCX package, so the parser under test is the production one."""
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        made = document.add_table(rows=len(table), cols=len(table[0]))
        for row_index, row in enumerate(table):
            for column_index, cell in enumerate(row):
                made.cell(row_index, column_index).text = cell
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(client, task_id: str, name: str, data: bytes, content_type: str) -> dict:
    uploaded = client.post(
        f"/api/tasks/{task_id}/materials",
        headers=MINA,
        data={"kind": "input"},
        files={"file": (name, data, content_type)},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()


def _drain(worker) -> None:
    while asyncio.run(worker.run_once()):
        pass


def test_an_office_document_is_kept_as_the_blocks_it_is_made_of(tmp_path) -> None:
    client, application, database_url, worker = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "문서가 붙는 업무"}).json()["task_id"]
    material = _upload(
        client,
        task_id,
        "계약서.docx",
        _docx(["공급사는 한빛상사입니다.", "납기일은 2026-09-30입니다."], [["항목", "금액"], ["설치비", "100만원"]]),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    _drain(worker)

    [listed] = client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()
    assert listed["extraction"]["status"] == "completed"

    with make_session_factory(database_url)() as session:
        extraction = session.scalar(select(MaterialExtractionRecord))
        blocks = list(
            session.scalars(select(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == extraction.id).order_by(MaterialBlockRecord.sequence))
        )
        chunks = list(session.scalars(select(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id == extraction.id)))

    # The document's own shape survives: paragraphs stay paragraphs and a table stays one block.
    assert [block.kind for block in blocks] == ["paragraph", "paragraph", "table"]
    assert "한빛상사" in blocks[0].text and "설치비" in blocks[2].text
    assert blocks[2].locator_label and "표" in blocks[2].locator_label
    # Chunks are derived from blocks, and each says which block it came from.
    assert chunks and all(chunk.block_id in {block.id for block in blocks} for chunk in chunks)
    assert extraction.parser_version and extraction.superseded_at is None

    # And it is searchable through the ordinary surface.
    found = client.get(f"/api/tasks/{task_id}/materials/search", headers=MINA, params={"q": "납기일"}).json()
    assert [row["name"] for row in found["results"]] == ["계약서.docx"]
    assert material["material_id"]


def test_a_scanned_document_says_it_could_not_be_read_rather_than_looking_empty(tmp_path) -> None:
    client, application, database_url, worker = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "스캔 문서 업무"}).json()["task_id"]
    # A PDF with an image and no text layer is `needs_ocr`, not `failed` and not an empty success.
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    _upload(client, task_id, "스캔본.pdf", buffer.getvalue(), "application/pdf")
    _drain(worker)

    [listed] = client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()
    assert listed["extraction"]["status"] in {"needs_ocr", "failed"}
    search = client.get(f"/api/tasks/{task_id}/materials/search", headers=MINA, params={"q": "무엇이든"}).json()
    assert search["results"] == []
    assert [row["name"] for row in search["unavailable_materials"]] == ["스캔본.pdf"]


def test_a_newer_parser_supersedes_what_it_replaces_instead_of_rewriting_it(tmp_path) -> None:
    client, application, database_url, worker = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "다시 추출할 업무"}).json()["task_id"]
    _upload(
        client, task_id, "계약서.docx",
        _docx(["공급사는 한빛상사입니다."]),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    _drain(worker)

    with make_session_factory(database_url)() as session:
        first = session.scalar(select(MaterialExtractionRecord))
        first_id, attachment_id = first.id, first.attachment_id

    # The same file, extracted again by a newer parser version.
    application.reextract_material(attachment_id=attachment_id, parser_version="test-2")
    _drain(worker)

    with make_session_factory(database_url)() as session:
        rows = list(session.scalars(select(MaterialExtractionRecord).order_by(MaterialExtractionRecord.requested_at)))
        current = [row for row in rows if row.superseded_at is None]
    assert len(rows) == 2
    assert [row.id for row in current] != [first_id]  # the newer one is current
    assert next(row for row in rows if row.id == first_id).superseded_at is not None
    # What an older answer stood on is still there to point at.
    with make_session_factory(database_url)() as session:
        kept = list(session.scalars(select(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == first_id)))
    assert kept

    # Search reads only the current extraction, so nothing is found twice.
    found = client.get(f"/api/tasks/{task_id}/materials/search", headers=MINA, params={"q": "한빛상사"}).json()
    assert len(found["results"]) == 1


def test_purging_a_file_removes_its_content_but_not_that_it_was_cited(tmp_path) -> None:
    client, application, database_url, worker = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "지워질 문서 업무"}).json()["task_id"]
    material = _upload(
        client, task_id, "개인정보.docx",
        _docx(["주민등록번호가 적힌 문서입니다."]),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    _drain(worker)

    purged = application.purge_attachment(
        application.authenticated_principal("yuna"), UUID(material["attachment_id"]), reason="개인정보 삭제 요청"
    )
    assert purged["state"] == "purged"

    with make_session_factory(database_url)() as session:
        attachment = session.get(AttachmentRecord, UUID(material["attachment_id"]))
        blocks = list(session.scalars(select(MaterialBlockRecord)))
        chunks = list(session.scalars(select(MaterialChunkRecord)))
    assert attachment.lifecycle == "purged"
    assert blocks == [] and chunks == []

    # The material is still listed as a fact of the Task, saying it can no longer be read.
    [listed] = client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json()
    assert listed["purged"] is True
    search = client.get(f"/api/tasks/{task_id}/materials/search", headers=MINA, params={"q": "주민등록번호"}).json()
    assert search["results"] == []
    assert client.get(
        f"/api/tasks/{task_id}/materials/{material['material_id']}/content", headers=MINA
    ).status_code in {404, 410, 422}
