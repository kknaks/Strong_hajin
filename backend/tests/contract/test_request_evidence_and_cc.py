"""ERD RESOURCE_RELATIONSHIP (cc), EVIDENCE adoption, and COMMENT attachment bindings on a WorkRequest."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import AttachmentBindingRecord, EvidenceRecord, ResourceRelationshipRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
ADMIN = {"X-Demo-Persona": "demo-admin"}
SORA = {"X-Demo-Persona": "sora"}


def _client(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def test_cc_members_can_read_and_discuss_but_never_decide(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    candidates = client.get("/api/work-request-cc-candidates", headers=MINA)
    assert candidates.status_code == 200 and "demo-admin" in {item["id"] for item in candidates.json()}
    created = client.post(
        "/api/work-requests", headers=MINA,
        json={"title": "계약 초안 검토", "assignee_id": "jiho", "cc_member_ids": ["demo-admin", "mina", "jiho", "demo-admin"]},
    )
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["cc_member_ids"] == ["demo-admin"]
    with make_session_factory(database_url)() as session:
        kinds = session.execute(
            select(ResourceRelationshipRecord.member_id, ResourceRelationshipRecord.relationship_kind).where(ResourceRelationshipRecord.resource_id == request["request_id"])
        ).all()
        assert set(kinds) == {("mina", "requester"), ("jiho", "assignee"), ("demo-admin", "cc")}
    rid = request["request_id"]
    # cc sees it in the list, can read the timeline and comment…
    assert rid in {item["request_id"] for item in client.get("/api/work-requests", headers=ADMIN).json()}
    assert client.get(f"/api/work-requests/{rid}/timeline", headers=ADMIN).status_code == 200
    comment = client.post(f"/api/work-requests/{rid}/comments", headers=ADMIN, json={"body": "참고: 지난 분기 조건 확인 필요"})
    assert comment.status_code == 201
    # …but cannot decide, and an unrelated member sees nothing.
    assert client.post(f"/api/work-requests/{rid}/accept", headers=ADMIN, json={"expected_version": 1}).status_code in {403, 422}
    assert client.get(f"/api/work-requests/{rid}", headers=ADMIN).json()["state"] == "pending"
    assert client.get(f"/api/work-requests/{rid}/timeline", headers=SORA).status_code == 403
    unknown = client.post("/api/work-requests", headers=MINA, json={"title": "x", "assignee_id": "jiho", "cc_member_ids": ["nobody"]})
    assert unknown.status_code == 422


def test_evidence_is_adopted_for_the_current_submission_and_pinned_by_hash(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "견적 승인", "assignee_id": "jiho", "cc_member_ids": ["demo-admin"]}).json()
    rid = request["request_id"]
    supporting = client.post(f"/api/work-requests/{rid}/evidence", headers=MINA, files={"file": ("견적서.pdf", b"quote-v1", "application/pdf")})
    assert supporting.status_code == 201, supporting.text
    assert supporting.json()["evidence_role"] == "supporting" and supporting.json()["fixed_snapshot_ref"].startswith("sha256:")
    basis = client.post(f"/api/work-requests/{rid}/evidence", headers=JIHO, files={"file": ("시장가.xlsx", b"prices", "application/octet-stream")})
    assert basis.status_code == 201 and basis.json()["evidence_role"] == "decision_basis"
    # cc may read but not adopt evidence
    assert client.post(f"/api/work-requests/{rid}/evidence", headers=ADMIN, files={"file": ("x.txt", b"x", "text/plain")}).status_code == 422
    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=ADMIN).json()
    assert [item["name"] for item in timeline["evidence"]] == ["견적서.pdf", "시장가.xlsx"]
    assert {item["submission_version"] for item in timeline["evidence"]} == {1}
    content = client.get(f"/api/work-requests/{rid}/attachments/{supporting.json()['attachment_id']}/content", headers=ADMIN)
    assert content.status_code == 200 and content.content == b"quote-v1"
    assert client.get(f"/api/work-requests/{rid}/attachments/{supporting.json()['attachment_id']}/content", headers=SORA).status_code == 403
    with make_session_factory(database_url)() as session:
        evidence = session.scalars(select(EvidenceRecord)).all()
        assert len(evidence) == 2 and all(not item.mutable_source for item in evidence)
        bindings = session.scalars(select(AttachmentBindingRecord).where(AttachmentBindingRecord.context_type == "submission")).all()
        assert {binding.role for binding in bindings} == {"supplemental"}


def test_comment_attachments_bind_to_the_author_comment(tmp_path) -> None:
    client, _ = _client(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "로고 시안", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    comment = client.post(f"/api/work-requests/{rid}/comments", headers=JIHO, json={"body": "이 버전은 어때요?"}).json()
    assert comment["attachments"] == []
    attached = client.post(
        f"/api/work-requests/{rid}/comments/{comment['comment_id']}/attachments", headers=JIHO,
        files={"file": ("시안-b.png", b"png-bytes", "image/png")},
    )
    assert attached.status_code == 201, attached.text
    assert [item["name"] for item in attached.json()["attachments"]] == ["시안-b.png"]
    # only the author may attach
    other = client.post(f"/api/work-requests/{rid}/comments/{comment['comment_id']}/attachments", headers=MINA, files={"file": ("x.png", b"x", "image/png")})
    assert other.status_code == 422
    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
    attachment = timeline["comments"][0]["attachments"][0]
    content = client.get(f"/api/work-requests/{rid}/attachments/{attachment['attachment_id']}/content", headers=MINA)
    assert content.status_code == 200 and content.content == b"png-bytes"
