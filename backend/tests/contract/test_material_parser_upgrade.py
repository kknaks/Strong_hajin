"""A historical completion is not evidence that the current projection contract was met."""
import asyncio
import pytest
from uuid import UUID

from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.platform.persistence import MaterialExtractionRecord, make_session_factory
from test_material_search import MINA, _stack, _upload


def test_legacy_completion_is_unavailable_until_the_current_parser_reads_the_source(tmp_path):
    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "이전 추출 검증"}).json()
    material = _upload(client, task["task_id"], "legacy.txt", b"legacysearchtoken", "text/plain").json()
    assert asyncio.run(worker.run_once())
    with make_session_factory(settings.database_url)() as session:
        old = session.get(MaterialExtractionRecord, UUID(material["extraction"]["extraction_id"]))
        old.parser_version = "1"
        old.coverage = None
        session.commit()
    listed = client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).json()[0]["extraction"]
    assert listed["status"] == "failed" and listed["failure_reason"] == "parser_upgrade_required"
    assert listed["stored_status"] == "completed" and listed["reextraction_required"] is True
    facade = McpReportsFacade(settings, "mina")
    for anchor in (None, material["material_id"]):
        found = facade.search_materials('legacysearchtoken', material_id=anchor)
        assert found["results"] == [] and found["searched_materials"] == 0
        assert found["unavailable_materials"][0]["extraction"] == listed
    # A read does not rewrite history or pretend a job has been queued.
    with make_session_factory(settings.database_url)() as session:
        old = session.get(MaterialExtractionRecord, UUID(material["extraction"]["extraction_id"]))
        assert old.status == "completed" and old.coverage is None and old.superseded_at is None


def test_idle_worker_upgrades_legacy_projection_once_without_rewriting_old_blocks(tmp_path):
    from sqlalchemy import select
    from ax_workspace.modules.work.material_extraction import PARSER_VERSION, MaterialExtractionJob
    from ax_workspace.platform.persistence import MaterialBlockRecord

    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "원본 재추출"}).json()
    source = ("synthetic source " * 13000 + "latestparsertailtoken").encode()
    material = _upload(client, task["task_id"], "legacy.txt", source, "text/plain").json()
    assert asyncio.run(worker.run_once())
    old_id = UUID(material["extraction"]["extraction_id"])
    with make_session_factory(settings.database_url)() as session:
        old = session.get(MaterialExtractionRecord, old_id)
        old.parser_version, old.coverage = "1", None
        old_blocks = [(row.id, row.text) for row in session.scalars(select(MaterialBlockRecord))]
        session.commit()
    # An idle pass schedules a separate extraction; the next pass consumes the ordinary durable job.
    assert asyncio.run(worker.run_once())
    queued = client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).json()[0]["extraction"]
    assert queued["status"] == "queued" and queued["parser_version"] == PARSER_VERSION
    assert queued["extraction_id"] != str(old_id)
    assert asyncio.run(worker.run_once())
    assert asyncio.run(worker.run_once()) is False
    found = McpReportsFacade(settings, 'mina').search_materials('latestparsertailtoken')
    assert found["results"] and found["searched_materials"] == 1
    assert {hit["extraction_id"] for hit in found["results"]} == {queued["extraction_id"]}
    assert all(hit["extraction"]["coverage"]["complete"] for hit in found["results"])
    with make_session_factory(settings.database_url)() as session:
        rows = list(session.scalars(select(MaterialExtractionRecord)))
        assert len(rows) == 2
        old = session.get(MaterialExtractionRecord, old_id)
        assert old.status == "completed" and old.coverage is None and old.superseded_at is not None
        assert [(row.id, row.text) for row in session.scalars(select(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == old_id))] == old_blocks
    assert worker.process(MaterialExtractionJob(old_id, UUID(material["attachment_id"]))) == "skipped"


@pytest.mark.parametrize("old_status", ["queued", "running"])
def test_old_pending_delivery_is_redirected_without_publishing_under_the_old_version(tmp_path, old_status):
    from sqlalchemy import select
    from ax_workspace.modules.work.material_extraction import PARSER_VERSION
    from ax_workspace.platform.persistence import MaterialChunkRecord

    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "배포 전 대기 자료"}).json()
    material = _upload(client, task["task_id"], "queued.txt", b"queuedupgradetoken", "text/plain").json()
    old_id = UUID(material["extraction"]["extraction_id"])
    with make_session_factory(settings.database_url)() as session:
        old = session.get(MaterialExtractionRecord, old_id)
        old.parser_version, old.status = "1", old_status
        session.commit()
    assert asyncio.run(worker.run_once())
    with make_session_factory(settings.database_url)() as session:
        old = session.get(MaterialExtractionRecord, old_id)
        assert old.status == old_status and old.superseded_at is not None
        assert list(session.scalars(select(MaterialChunkRecord))) == []
        current = session.scalar(select(MaterialExtractionRecord).where(MaterialExtractionRecord.superseded_at.is_(None)))
        assert current.parser_version == PARSER_VERSION and current.status == "queued"
    assert asyncio.run(worker.run_once())
    assert McpReportsFacade(settings, 'mina').search_materials('queuedupgradetoken')["results"]


def test_future_parser_delivery_is_left_for_a_compatible_worker(tmp_path):
    from sqlalchemy import select
    from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, PARSER_VERSION
    from ax_workspace.platform.persistence import MaterialChunkRecord

    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "새 배포의 자료"}).json()
    material = _upload(client, task["task_id"], "future.txt", b"futuretoken", "text/plain").json()
    extraction_id = UUID(material["extraction"]["extraction_id"])
    with make_session_factory(settings.database_url)() as session:
        session.get(MaterialExtractionRecord, extraction_id).parser_version = "4"
        session.commit()
    assert worker.process(MaterialExtractionJob(extraction_id, UUID(material["attachment_id"]))) == "contended"
    assert worker._catch_up_index() is False
    with pytest.raises(ValueError, match="active parser version is incompatible"):
        application.reextract_material(attachment_id=UUID(material["attachment_id"]), parser_version=PARSER_VERSION)
    with make_session_factory(settings.database_url)() as session:
        rows = list(session.scalars(select(MaterialExtractionRecord)))
        assert len(rows) == 1 and rows[0].parser_version == "4" and rows[0].status == "queued"
        assert not list(session.scalars(select(MaterialChunkRecord)))


def test_manual_reextraction_cannot_mislabel_output_with_an_uninstalled_parser(tmp_path):
    client, application, _, _ = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "설치된 parser 계약"}).json()
    material = _upload(client, task["task_id"], "source.txt", b"content", "text/plain").json()
    with pytest.raises(ValueError, match="parser version is not installed"):
        application.reextract_material(attachment_id=UUID(material["attachment_id"]), parser_version="invented-parser")


def test_superseded_running_attempt_cannot_publish_after_a_parser_upgrade(tmp_path):
    from ax_workspace.modules.work.material_extraction import PARSER_VERSION, ExtractionOutcome, MaterialExtractionJob, MaterialExtractionService
    from ax_workspace.platform.material_extraction import SqlAlchemyMaterialExtractionRepository

    client, application, _, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "늦은 구버전 결과"}).json()
    material = _upload(client, task["task_id"], "source.txt", b"source", "text/plain").json()
    old_id = UUID(material["extraction"]["extraction_id"])
    with make_session_factory(settings.database_url)() as session:
        service = MaterialExtractionService(SqlAlchemyMaterialExtractionRepository(session))
        claimed = service.claim(MaterialExtractionJob(old_id, UUID(material["attachment_id"])))
        session.get(MaterialExtractionRecord, old_id).parser_version = "1"
        session.commit()
    application.reextract_material(attachment_id=UUID(material["attachment_id"]), parser_version=PARSER_VERSION)
    with make_session_factory(settings.database_url)() as session:
        service = MaterialExtractionService(SqlAlchemyMaterialExtractionRepository(session))
        assert service.finish(claimed, ExtractionOutcome(status="completed", extractor="text")) == "stale"
        assert session.get(MaterialExtractionRecord, old_id).status == "running"
