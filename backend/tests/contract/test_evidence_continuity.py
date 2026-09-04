"""Evidence that survives a round trip, and a judgement that remembers what it was made on.

A WorkRequest can be adjusted and resubmitted several times. Two things must hold across that. The basis a person
already judged on can never change afterwards — a decision freezes the evidence set it saw. And the basis does not have
to be assembled again on every round: a revision inherits what was already adopted, as new Evidence rows of its own, so
each round owns an independent set that starts from the last one.
"""
import hashlib
import json
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import AttachmentRecord, EvidenceRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), database_url, settings


def _adopt(client, headers, request_id: str, name: str, body: bytes):
    return client.post(f"/api/work-requests/{request_id}/evidence", headers=headers, files={"file": (name, body, "text/plain")})


def _command(client, headers, action_item_id: str, command: str, **payload):
    return client.post(f"/api/action-items/{action_item_id}/commands/{command}", headers=headers, json=payload)


def _pending(client, headers) -> list[dict]:
    return client.get("/api/action-items", headers=headers).json()


def _expected_hash(entries: list[dict]) -> str:
    canonical = sorted(
        ({"attachment_id": row["attachment_id"], "evidence_role": row["evidence_role"], "fixed_snapshot_ref": row["fixed_snapshot_ref"]} for row in entries),
        key=lambda row: (row["attachment_id"], row["evidence_role"], row["fixed_snapshot_ref"]),
    )
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_evidence_is_adopted_only_while_the_round_is_still_being_judged(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "근거 시점", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    assert _adopt(client, MINA, rid, "제안서.txt", b"proposal").status_code == 201

    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="근거를 보강해 주세요")

    # The reviewer already judged this round: its basis is history now, and history does not grow.
    refused = _adopt(client, JIHO, rid, "뒤늦은근거.txt", b"late")
    assert refused.status_code == 422, refused.text
    assert "재상신" in refused.text
    assert _adopt(client, MINA, rid, "뒤늦은지원.txt", b"late").status_code == 422

    # After the revision the new round is open again, and evidence belongs to it.
    [waiting] = _pending(client, MINA)
    revised = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "보강한 설명"})
    assert revised.status_code == 200, revised.text
    added = _adopt(client, MINA, rid, "보강근거.txt", b"more")
    assert added.status_code == 201 and added.json()["submission_version"] == 2

    # And once the question is answered for good, nothing may be added to it at all.
    [second] = _pending(client, JIHO)
    assert _command(client, JIHO, second["action_item_id"], "accept", expected_version=second["expected_version"]).status_code == 200
    assert _adopt(client, MINA, rid, "판단후.txt", b"after").status_code == 422


def test_a_revision_inherits_the_basis_as_its_own_evidence_rows(tmp_path) -> None:
    client, database_url, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "근거 상속", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    supporting = _adopt(client, MINA, rid, "견적서.txt", b"quote").json()
    basis = _adopt(client, JIHO, rid, "시장가.txt", b"prices").json()

    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="기한을 늦춰 주세요")
    [waiting] = _pending(client, MINA)
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "근거 상속 (조정)"})

    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
    rounds = {row["submission_version"]: row for row in timeline["submissions"]}
    assert set(rounds) == {1, 2}

    # The second round starts from the first round's basis: same attachments, same integrity, same roles.
    def manifest(version: int) -> list[tuple[str, str, str]]:
        return sorted((row["attachment_id"], row["evidence_role"], row["fixed_snapshot_ref"]) for row in rounds[version]["evidence"])

    assert manifest(1) == manifest(2)
    assert {row["evidence_role"] for row in rounds[2]["evidence"]} == {"supporting", "decision_basis"}
    assert rounds[1]["evidence_hash"] == rounds[2]["evidence_hash"]

    # They are its own rows though, so the two rounds can diverge from here.
    with make_session_factory(database_url)() as session:
        rows = session.scalars(select(EvidenceRecord)).all()
        assert len(rows) == 4
        assert len({row.id for row in rows}) == 4
        inherited = [row for row in rows if str(row.submission_id) == rounds[2]["submission_id"]]
        original = {str(row.attachment_id): row for row in rows if str(row.submission_id) == rounds[1]["submission_id"]}
        assert len(inherited) == 2
        for row in inherited:
            source = original[str(row.attachment_id)]
            assert row.fixed_snapshot_ref == source.fixed_snapshot_ref and row.evidence_role == source.evidence_role
            assert row.adopted_by == source.adopted_by and row.id != source.id
        # No file was copied: both rounds point at the same two attachments.
        assert session.scalars(select(AttachmentRecord)).all().__len__() == 2

    added = _adopt(client, MINA, rid, "추가근거.txt", b"extra").json()
    after = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
    second = [row for row in after["submissions"] if row["submission_version"] == 2][0]
    first = [row for row in after["submissions"] if row["submission_version"] == 1][0]
    assert len(second["evidence"]) == 3 and len(first["evidence"]) == 2
    assert first["evidence_hash"] != second["evidence_hash"]
    assert added["attachment_id"] not in {row["attachment_id"] for row in first["evidence"]}
    assert supporting["attachment_id"] in {row["attachment_id"] for row in second["evidence"]}
    assert basis["attachment_id"] in {row["attachment_id"] for row in second["evidence"]}


def test_a_decision_freezes_the_evidence_it_was_made_on(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "근거 동결", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    _adopt(client, MINA, rid, "근거1.txt", b"one")
    _adopt(client, JIHO, rid, "근거2.txt", b"two")

    [item] = _pending(client, JIHO)
    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    first_round = [row for row in timeline["submissions"] if row["submission_version"] == 1][0]
    at_decision = first_round["evidence_hash"]
    assert at_decision == _expected_hash(first_round["evidence"])

    adjusted = _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="근거를 더 주세요")
    assert adjusted.status_code == 200, adjusted.text

    after = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    [decision] = after["review_decisions"]
    assert decision["evidence_hash"] == at_decision
    assert {row["attachment_id"] for row in decision["conditions"]["evidence_manifest"]} == {row["attachment_id"] for row in first_round["evidence"]}
    # The frozen manifest is the stable identity of the basis, not the row ids that happened to hold it.
    assert set(decision["conditions"]["evidence_manifest"][0]) == {"attachment_id", "evidence_role", "fixed_snapshot_ref"}
    # An adjustment's own conditions survive alongside it.
    assert decision["reason"] == "근거를 더 주세요"

    # The next round grows, and the earlier decision still says what it was made on.
    [waiting] = _pending(client, MINA)
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "보강"})
    _adopt(client, MINA, rid, "근거3.txt", b"three")
    grown = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    assert [row for row in grown["review_decisions"]][0]["evidence_hash"] == at_decision
    second_round = [row for row in grown["submissions"] if row["submission_version"] == 2][0]
    assert second_round["evidence_hash"] != at_decision

    [final] = _pending(client, JIHO)
    accepted = _command(client, JIHO, final["action_item_id"], "accept", expected_version=final["expected_version"])
    assert accepted.status_code == 200, accepted.text
    settled = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    assert [row["evidence_hash"] for row in settled["review_decisions"]] == [at_decision, second_round["evidence_hash"]]


def test_the_same_basis_hashes_the_same_however_it_was_assembled(tmp_path) -> None:
    """The hash is of the set, not of the order its rows were written or read in."""
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "순서 무관", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    for name in ("가.txt", "나.txt", "다.txt"):
        _adopt(client, MINA, rid, name, name.encode())
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "reject", expected_version=item["expected_version"], reason="거절")

    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    frozen = timeline["review_decisions"][0]["evidence_hash"]
    entries = timeline["review_decisions"][0]["conditions"]["evidence_manifest"]
    assert len(entries) == 3
    assert frozen == _expected_hash(entries)
    assert frozen == _expected_hash(list(reversed(entries)))
    assert frozen == _expected_hash([entries[1], entries[2], entries[0]])
    # The manifest itself is handed out in that one canonical order.
    assert entries == sorted(entries, key=lambda row: (row["attachment_id"], row["evidence_role"], row["fixed_snapshot_ref"]))

    # And an empty basis is a stable answer of its own, not a missing one.
    bare = client.post("/api/work-requests", headers=MINA, json={"title": "근거 없음", "assignee_id": "jiho"}).json()
    [empty_item] = [row for row in _pending(client, JIHO) if row["subject"] == "근거 없음"]
    _command(client, JIHO, empty_item["action_item_id"], "accept", expected_version=empty_item["expected_version"])
    empty = client.get(f"/api/work-requests/{bare['request_id']}/timeline", headers=JIHO).json()
    assert empty["review_decisions"][0]["evidence_hash"] == _expected_hash([])
    assert empty["review_decisions"][0]["conditions"]["evidence_manifest"] == []


def test_the_judgement_ledger_reads_the_same_basis_the_request_timeline_does(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "원장 대조", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    _adopt(client, MINA, rid, "근거.txt", b"one")
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="더 주세요")
    [waiting] = _pending(client, MINA)
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "보강"})
    _adopt(client, MINA, rid, "근거2.txt", b"two")

    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
    rounds = {row["submission_version"]: row for row in timeline["submissions"]}
    for round_view in detail["rounds"]:
        version = round_view["submission_version"]
        assert round_view["evidence_hash"] == rounds[version]["evidence_hash"]
        assert {row["attachment_id"] for row in round_view["evidence"]} == {row["attachment_id"] for row in rounds[version]["evidence"]}
    assert detail["rounds"][0]["decisions"][0]["evidence_hash"] == timeline["review_decisions"][0]["evidence_hash"]
    assert detail["rounds"][1]["decisions"] == []
    # Someone with no relationship to the request learns nothing about its basis.
    assert client.get(f"/api/action-items/{item['action_item_id']}", headers={"X-Demo-Persona": "sora"}).status_code in {403, 422}


def test_a_judgement_that_did_not_happen_freezes_nothing(tmp_path) -> None:
    client, database_url, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "실패한 판단", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    _adopt(client, MINA, rid, "근거.txt", b"one")
    [item] = _pending(client, JIHO)

    # Stale, unauthorized and malformed answers all leave the ledger exactly as it was.
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] + 1).status_code == 422
    assert _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code in {403, 422}
    assert _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"]).status_code == 422
    assert _command(
        client, JIHO, item["action_item_id"], "adjust",
        expected_version=item["expected_version"], reason="사유", changes={"assignee_id": "sora"},
    ).status_code == 422

    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    assert timeline["review_decisions"] == []
    assert timeline["request"]["state"] == "pending"
    assert len(timeline["submissions"]) == 1 and len(timeline["submissions"][0]["evidence"]) == 1
    assert client.get("/api/my-work", headers=JIHO).json() == []
    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(EvidenceRecord)).all()) == 1


def test_every_way_of_answering_freezes_the_same_basis(tmp_path, monkeypatch) -> None:
    """The compatibility endpoint, the canonical command and a delegated turn's confirmation are one decision path."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, database_url, settings = _stack(tmp_path)
    application = client.app.state.workflow_application
    frozen: dict[str, str] = {}

    # 1) the per-kind REST endpoint the product kept for compatibility
    legacy = client.post("/api/work-requests", headers=MINA, json={"title": "구 endpoint", "assignee_id": "jiho"}).json()
    adopted = _adopt(client, MINA, legacy["request_id"], "근거.txt", b"one").json()
    # Adopting moved the request on, so the answer names the version that now stands.
    assert client.post(
        f"/api/work-requests/{legacy['request_id']}/reject", headers=JIHO,
        json={"expected_version": adopted["request_version"], "reason": "거절"},
    ).status_code == 200
    legacy_timeline = client.get(f"/api/work-requests/{legacy['request_id']}/timeline", headers=JIHO).json()
    frozen["legacy"] = legacy_timeline["review_decisions"][0]["evidence_hash"]
    assert frozen["legacy"] == _expected_hash(legacy_timeline["submissions"][0]["evidence"])

    # 2) the canonical ActionItem command
    canonical = client.post("/api/work-requests", headers=MINA, json={"title": "정식 command", "assignee_id": "jiho"}).json()
    _adopt(client, MINA, canonical["request_id"], "근거.txt", b"one")
    [item] = [row for row in _pending(client, JIHO) if row["subject"] == "정식 command"]
    assert _command(client, JIHO, item["action_item_id"], "reject", expected_version=item["expected_version"], reason="거절").status_code == 200
    canonical_timeline = client.get(f"/api/work-requests/{canonical['request_id']}/timeline", headers=JIHO).json()
    frozen["canonical"] = canonical_timeline["review_decisions"][0]["evidence_hash"]

    # 3) a delegated turn's confirmation, approved by a person
    delegated = client.post("/api/work-requests", headers=MINA, json={"title": "위임 확인", "assignee_id": "jiho"}).json()
    _adopt(client, MINA, delegated["request_id"], "근거.txt", b"one")
    [pending_item] = [row for row in _pending(client, JIHO) if row["subject"] == "위임 확인"]
    conversation = client.post("/api/conversations", headers=JIHO, json={"title": "위임"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**JIHO, "Idempotency-Key": "evidence-delegated"},
        json={"body": "판단해줘", "context": []},
    )
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    wrapper = McpReportsFacade(settings, "jiho").run_action_command(
        pending_item["action_item_id"], "reject", expected_version=pending_item["expected_version"], reason="거절"
    )
    assert wrapper["state"] == "pending"
    assert client.get(f"/api/work-requests/{delegated['request_id']}/timeline", headers=JIHO).json()["review_decisions"] == []
    approved = client.post(
        f"/api/actions/{wrapper['action_id']}/decide", headers=JIHO,
        json={"expected_version": wrapper["version"], "decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    delegated_timeline = client.get(f"/api/work-requests/{delegated['request_id']}/timeline", headers=JIHO).json()
    frozen["delegated"] = delegated_timeline["review_decisions"][0]["evidence_hash"]

    # One decision path, one rule: each door froze exactly the basis its own round was standing on.
    assert len(frozen) == 3 and all(frozen.values())
    manifests = []
    for door, timeline in (("legacy", legacy_timeline), ("canonical", canonical_timeline), ("delegated", delegated_timeline)):
        [decision] = timeline["review_decisions"]
        [round_one] = timeline["submissions"]
        assert decision["evidence_hash"] == frozen[door] == _expected_hash(round_one["evidence"]), door
        assert [row["evidence_role"] for row in decision["conditions"]["evidence_manifest"]] == ["supporting"]
        assert decision["reason"] == "거절"
        manifests.append(decision["conditions"]["evidence_manifest"])
    # The same bytes were adopted in all three, so the three bases differ only in which attachment row holds them.
    assert len({tuple((row["evidence_role"], row["fixed_snapshot_ref"]) for row in manifest) for manifest in manifests}) == 1
    assert len({tuple(row["attachment_id"] for row in manifest) for manifest in manifests}) == 3


def test_every_kind_of_judgement_answers_the_same_round_shape(tmp_path) -> None:
    """A client reads a round the same way whatever raised it, even for kinds that adopt no evidence."""
    client, _, _ = _stack(tmp_path)
    application = client.app.state.workflow_application
    application.assign_task(application.authenticated_principal("jiho"), "배정된 업무", "mina")
    client.post("/api/work-requests", headers=MINA, json={"title": "요청", "assignee_id": "jiho"})

    for headers in (MINA, JIHO):
        for envelope in _pending(client, headers):
            detail = client.get(f"/api/action-items/{envelope['action_item_id']}", headers=headers).json()
            for round_view in detail["rounds"]:
                assert "evidence" in round_view and "evidence_hash" in round_view, envelope["kind"]
                for decision in round_view["decisions"]:
                    assert "evidence_hash" in decision, envelope["kind"]


def test_inheriting_a_basis_records_the_copy_instead_of_a_new_adoption(tmp_path) -> None:
    """A copied adoption keeps its own author and moment; the copy itself is what the revision is credited with."""
    client, database_url, _ = _stack(tmp_path)
    from ax_workspace.platform.persistence import ActivityEventRecord, WorkRequestAuditEventRecord

    request = client.post("/api/work-requests", headers=MINA, json={"title": "출처 보존", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    _adopt(client, JIHO, rid, "담당자근거.txt", b"basis")
    _adopt(client, MINA, rid, "요청자근거.txt", b"support")

    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="조정")
    [waiting] = _pending(client, MINA)
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "보강"})

    with make_session_factory(database_url)() as session:
        rows = sorted(session.scalars(select(EvidenceRecord)).all(), key=lambda row: (str(row.submission_id), row.adopted_by))
        by_attachment: dict[str, list] = {}
        for row in rows:
            by_attachment.setdefault(str(row.attachment_id), []).append(row)
        assert len(by_attachment) == 2
        for original, inherited in by_attachment.values():
            # The copy says what actually happened: this person adopted this file at that moment, once.
            assert inherited.adopted_by == original.adopted_by
            assert inherited.adopted_at == original.adopted_at
            assert inherited.id != original.id and inherited.submission_id != original.submission_id

        # The revision is credited with the copy, as its own append-only fact.
        [inheritance] = [row for row in session.scalars(select(ActivityEventRecord)) if row.event_kind == "work_request.evidence_inherited"]
        assert inheritance.actor_id == "mina"
        assert "2" in inheritance.safe_summary
        submissions = {str(row.submission_id) for row in rows}
        assert inheritance.before_ref is not None and inheritance.before_ref.split(":")[1] in submissions
        assert inheritance.after_ref is not None and inheritance.after_ref.split(":")[1] in submissions
        assert inheritance.before_ref != inheritance.after_ref
        # Only the two real adoptions are audited as adoptions.
        adoptions = [row for row in session.scalars(select(WorkRequestAuditEventRecord)) if row.event_type == "work_request.evidence_adopted"]
        assert [row.actor_id for row in adoptions] == ["jiho", "mina"]

    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
    assert [row["event_kind"] for row in timeline["activity"]].count("work_request.evidence_inherited") == 1


def test_the_flat_evidence_list_is_ordered_the_same_way_every_time(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "정렬", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    for name in ("가.txt", "나.txt", "다.txt"):
        _adopt(client, MINA, rid, name, name.encode())
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="조정")
    [waiting] = _pending(client, MINA)
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "보강"})

    # Inherited rows share their source's adoption time, so the order must not rest on that alone.
    reads = [
        [(row["submission_version"], row["name"], row["evidence_id"]) for row in client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()["evidence"]]
        for _ in range(3)
    ]
    assert reads[0] == reads[1] == reads[2]
    assert [row[0] for row in reads[0]] == [1, 1, 1, 2, 2, 2]
    assert [row[1] for row in reads[0][:3]] == ["가.txt", "나.txt", "다.txt"]


def test_adopting_evidence_moves_the_request_on_so_an_open_reviewer_is_not_deciding_blind(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "버전 이동", "assignee_id": "jiho"}).json()
    rid = request["request_id"]
    [before] = _pending(client, JIHO)

    adopted = _adopt(client, MINA, rid, "뒤늦은근거.txt", b"late")
    assert adopted.status_code == 201, adopted.text
    assert adopted.json()["request_version"] == before["expected_version"] + 1

    # The reviewer who opened the item before the basis moved cannot answer with what they saw.
    stale = _command(client, JIHO, before["action_item_id"], "accept", expected_version=before["expected_version"])
    assert stale.status_code == 422, stale.text
    assert client.get("/api/my-work", headers=JIHO).json() == []

    # Reading it again shows the new basis and the version that answers it.
    [after] = _pending(client, JIHO)
    assert after["expected_version"] == before["expected_version"] + 1
    accepted = _command(client, JIHO, after["action_item_id"], "accept", expected_version=after["expected_version"])
    assert accepted.status_code == 200, accepted.text
    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=JIHO).json()
    assert timeline["review_decisions"][0]["evidence_hash"] == timeline["submissions"][0]["evidence_hash"]
    assert len(timeline["submissions"][0]["evidence"]) == 1
