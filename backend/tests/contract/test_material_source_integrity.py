"""An indexed revision must match its bytes; purging removes copied source context as well as excerpts."""
import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from ax_workspace.platform.materials import LocalDirectoryMaterialStorage
from ax_workspace.platform.persistence import AttachmentRecord, make_session_factory
from test_material_search import MINA, _stack, _upload


class UnreachableParser:
    def extract(self, **kwargs):
        raise AssertionError("Changed bytes must be rejected before parser execution")


@pytest.mark.serial
def test_worker_rejects_source_bytes_that_no_longer_match_the_uploaded_revision(tmp_path):
    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "원본 무결성"}).json()
    material = _upload(client, task["task_id"], "source.txt", b"originalsourcetoken", "text/plain").json()
    with make_session_factory(settings.database_url)() as session:
        source_ref = session.get(AttachmentRecord, UUID(material["attachment_id"])).source_ref
    LocalDirectoryMaterialStorage(Path(settings.materials_dir)).put(source_ref, b"replacedsourcetoken", "text/plain")
    worker._extractor = UnreachableParser()
    assert asyncio.run(worker.run_once())
    extraction = client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).json()[0]["extraction"]
    assert extraction["status"] == "failed" and extraction["failure_reason"] == "integrity_mismatch"
    assert extraction["chunk_count"] == 0
    found = client.get('/api/materials/search', headers=MINA, params={'q': 'replacedsourcetoken'}).json()
    assert found["results"] == [] and found["searched_materials"] == 0


def test_upload_admission_keeps_the_exact_25_mib_boundary_before_storage_or_queue(tmp_path):
    from sqlalchemy import func, select
    from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION
    from ax_workspace.modules.work.materials import MAX_MATERIAL_BYTES

    client, application, _, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "업로드 크기 경계"}).json()
    rejected = _upload(client, task["task_id"], "too-large.txt", b"x" * (MAX_MATERIAL_BYTES + 1), "text/plain")
    assert rejected.status_code == 422 and "25MB" in rejected.text
    with make_session_factory(settings.database_url)() as session:
        assert session.scalar(select(func.count()).select_from(AttachmentRecord)) == 0
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 0
    assert not list(Path(settings.materials_dir).rglob("*"))
    accepted = _upload(client, task["task_id"], "at-limit.txt", b"x" * MAX_MATERIAL_BYTES, "text/plain")
    assert accepted.status_code == 201
    assert accepted.json()["size_bytes"] == MAX_MATERIAL_BYTES
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 1
