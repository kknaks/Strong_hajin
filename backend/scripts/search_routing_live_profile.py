"""Synthetic-only natural-language routing evaluation through the real Conversation worker and Codex/MCP.

Default is a local fixture dry run. --live explicitly invokes the existing provider profile; no vault data is used.
"""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir
from uuid import UUID, uuid4

from material_corpus_profile import NoExternalProvider

ACTOR = {"X-Demo-Persona": "jiho"}
REQUESTER = {"X-Demo-Persona": "mina"}
CASES = [
    {"kind": "list", "question": "내가 맡은 업무 목록과 현재 상태를 알려줘.", "first": "task_list", "required": ["task_list"], "max_calls": 1},
    {"kind": "relation", "question": "민아가 요청해서 내가 맡은 '노을 검토 1788669499950' 업무의 요청자, 담당자, 하위 업무와 연결 자료를 관계를 따라 확인해줘.", "first": "graph_search", "required": ["graph_search", "graph_neighbors"], "max_calls": 6},
    {"kind": "task_content", "question": "노을 견적서에 적힌 공급사와 납기일을 찾아줘.", "first": "material_search", "required": ["material_search"], "max_calls": 3, "material": "task_material"},
    {"kind": "independent", "question": "가람 운영가이드에서 변경 승인을 받는 창구와 제출 기한을 알려줘.", "first": "material_search", "required": ["material_search"], "max_calls": 3, "material": "personal_material"},
    {"kind": "meeting", "question": "새봄 일정 회의에서 확정한 출시 날짜와 담당자를 찾아줘.", "first": "material_search", "required": ["material_search"], "max_calls": 3, "material": "meeting_material"},
    {"kind": "followup", "question": "그 견적서의 결제 조건도 확인해줘.", "first": "material_search", "required": ["material_search"], "max_calls": 3, "material": "task_material"},
    {"kind": "precreation", "question": "노을 견적서에서 납기일을 먼저 확인하고, 그 근거로 납품 검토 업무를 제안해줘.", "first": "material_search", "required": ["material_search", "task_create_self"], "max_calls": 4, "material": "task_material"},
]
REVOKED = {"kind": "revoked_followup", "question": "그 가람 운영가이드의 승인 창구와 제출 기한을 다시 확인해줘.", "first": "material_search", "required": ["material_search"], "max_calls": 3}


def setup(work: Path):
    from fastapi.testclient import TestClient
    from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
    from ax_workspace.entrypoints.http import create_app
    from ax_workspace.entrypoints.reset_demo import reset_database
    from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment

    settings = Settings(RuntimeProfile.TEST, f"sqlite:///{work / 'demo.db'}", materials_dir=str(work / "materials"), recordings_dir=str(work / "recordings"))
    reset_database(settings.database_url)
    app = create_app(settings, report_provider=NoExternalProvider())
    client, application = TestClient(app), app.state.workflow_application
    principal = application.authenticated_principal("jiho")
    request = client.post("/api/work-requests", headers=REQUESTER, json={"title": "노을 검토 1788669499950", "assignee_id": "jiho", "description": "합성 평가용 납품 검토", "due_date": "2026-09-30"}).json()
    accepted = application.accept_work_request(principal, UUID(request["request_id"]), request["version"])
    task_id = accepted["task_id"]
    child = client.post("/api/tasks", headers=ACTOR, json={"title": "노을 품질 확인", "parent_task_id": task_id}).json()
    client.post("/api/tasks", headers=ACTOR, json={"title": "노을 검토 1788669499949", "description": "별도 합성 업무"})
    material = client.post(f"/api/tasks/{task_id}/materials", headers=ACTOR, data={"kind": "input"},
        files={"file": ("노을 견적서.md", "# 노을 견적서\n공급사는 햇살상사입니다. 납기일은 2026-09-30입니다. 결제 조건은 납품 후 30일입니다.\n".encode(), "text/markdown")}).json()
    folder = client.post("/api/material-folders", headers=ACTOR, json={"kind": "personal", "title": "가람 자료함"}).json()
    personal = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=ACTOR,
        files={"file": ("가람 운영가이드.md", "# 가람 운영가이드\n변경 승인 창구는 운영기획팀입니다. 제출 기한은 2026-09-25입니다.\n".encode(), "text/markdown")}).json()
    # A separate team's source exercises owner discovery without putting its words in the questions.
    team = client.post("/api/material-folders", headers=ACTOR, json={"kind": "team", "organization_id": "product", "title": "팀 참고 자료"}).json()
    client.post(f"/api/material-folders/{team['folder_id']}/materials", headers=ACTOR,
        files={"file": ("다른 안내.md", "합성 제품팀 안내. 문의는 제품지원팀입니다.".encode(), "text/markdown")})
    meeting = client.post("/api/meetings", headers=REQUESTER, json={"organization_id": "scax", "title": "새봄 일정 회의", "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z", "visibility": "private", "attendee_ids": ["jiho"]}).json()
    mid = meeting["meeting_id"]
    recording = client.post(f"/api/meetings/{mid}/recordings/start", headers=REQUESTER, json={"purpose": "합성 평가"}).json()
    stopped = client.post(f"/api/meetings/{mid}/recordings/{recording['recording_id']}/stop", headers=REQUESTER,
        data={"expected_version": str(recording["version"])}, files={"audio": ("fixture.webm", b"synthetic audio only", "audio/webm")})
    if stopped.status_code != 200:
        raise ValueError("synthetic recording setup failed")
    raw = application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture", provider_reference="routing:raw",
        segments=[FinalTranscriptSegment("raw-a", 1200, 3400, "새봄 출시는 2026년 10월 15일로 확정했습니다. 출시 담당자는 민아입니다.")])
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    async def drain():
        while await worker.run_once():
            pass
    asyncio.run(drain())
    meeting_hit = application.search_materials(principal, "새봄 출시", resource_types=["meeting"])["results"][0]
    fixture = {"task_id": task_id, "child_task_id": child["task_id"], "request_id": request["request_id"],
        "task_material": material["material_id"], "personal_material": personal["material_id"],
        "folder_id": folder["folder_id"], "meeting_id": mid, "meeting_material": meeting_hit["material_id"],
        "raw_revision_id": raw["transcript_revision_id"]}
    for key, query in (("task_material", "노을 공급사"), ("personal_material", "가람 승인"), ("meeting_material", "새봄 출시")):
        if not any(row["material_id"] == fixture[key] for row in application.search_materials(principal, query)["results"]):
            raise ValueError("synthetic source search failed")
    return client, application, settings, fixture


class ObservedProvider:
    def __init__(self, settings, work: Path):
        from ax_workspace.platform.codex_cli import CodexCliProfile, CodexCliProviderAdapter, CodexCliMcpServer
        self.trace = {}
        self.profile = CodexCliProfile(runtime_home=work / "codex-runtime")
        self.delegate = CodexCliProviderAdapter(profile=self.profile, runner=self.run_process,
            scax_mcp_server=CodexCliMcpServer(command=sys.executable, arguments=("-m", "ax_workspace.entrypoints.mcp"),
                environment={"AX_PROFILE": "test", "DATABASE_URL": settings.database_url}))

    def run_process(self, command, arguments, cwd, environment, timeout_seconds, on_line=None, should_cancel=None):
        from ax_workspace.platform.codex_cli import _subprocess_runner
        def observe(line):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {}
            item = event.get("item") or {}
            if item.get("type") == "mcp_tool_call":
                key = str(item.get("id"))
                call = self.trace["calls"].setdefault(key, {"tool": item.get("tool"), "arguments": {}, "status": "running"})
                if isinstance(item.get("arguments"), dict):
                    call["arguments"] = item["arguments"]
                call["status"] = item.get("status", call["status"])
                result = item.get("result") or {}
                if isinstance(result, dict):
                    structured = result.get("structured_content") or result.get("structuredContent") or {}
                    if result.get("isError") or result.get("is_error"):
                        call["error_code"] = "mcp_error"
                    if isinstance(structured, dict) and structured.get("error_code"):
                        call["error_code"] = structured["error_code"]
            if on_line is not None:
                on_line(line)
        return _subprocess_runner(command, arguments, cwd, environment, timeout_seconds, on_line=observe, should_cancel=should_cancel)

    def converse(self, request, *, sink=None, cancel=None):
        self.trace = {"calls": {}, "checkpoint_reused": bool(request.provider_session_ref), "seed_refs": [row["ref"] for row in request.seed_references]}
        return self.delegate.converse(request, sink=sink, cancel=cancel)


def assess(case: dict, view: dict, turn_id: str, fixture: dict, trace: dict) -> dict:
    turn = next(row for row in view["turns"] if row["turn_id"] == turn_id)
    tools = [row for row in view["tool_invocations"] if row["turn_id"] == turn_id]
    names = [row["tool_name"] for row in tools]
    succeeded = {row["tool_name"] for row in tools if row["state"] == "completed"}
    errors = sum(row["state"] in {"failed", "denied"} for row in tools)
    business_errors = sum(bool(row.get("error_code")) for row in trace.get("calls", {}).values())
    evidence = [row for row in view["material_evidence"] if row["turn_id"] == turn_id]
    resources = [row for row in view["answer_resources"] if row["turn_id"] == turn_id]
    graph = [row for row in view["graph_receipts"] if row["turn_id"] == turn_id]
    answer = "\n".join(row["body"] for row in view["messages"] if row.get("turn_id") == turn_id and row["role"] != "user")
    checks = {"completed": turn["state"] == "completed", "first_tool": bool(names) and names[0] == case["first"],
              "required_tools": set(case["required"]) <= succeeded, "within_call_budget": len(tools) <= case["max_calls"],
              "no_tool_error": errors == business_errors == 0}
    if case.get("material"):
        identifier = fixture[case["material"]]
        checks["expected_receipt"] = any(row["material_id"] == identifier for row in evidence)
        checks["canonical_answer_link"] = any(row["resource_type"] == "material" and row["resource_id"] == identifier and row.get("origin") for row in resources)
    if case["kind"] == "list":
        checks["no_graph_walk"] = not any(name.startswith("graph_") for name in names)
        checks["canonical_task"] = any(row["resource_type"] == "task" and row["resource_id"] == fixture["task_id"] for row in resources)
    if case["kind"] == "relation":
        task, request = f"task:{fixture['task_id']}", f"work_request:{fixture['request_id']}"
        expected = {
            ("holds", "person:jiho", task), ("produced", request, task),
            ("requested", "person:mina", request), ("asked_of", request, "person:jiho"),
            ("parent_of", task, f"task:{fixture['child_task_id']}"),
            ("has_material", task, f"material:{fixture['task_material']}"),
        }
        observed = {(row.get("edge_kind"), row.get("from_ref"), row.get("to_ref")) for row in graph if row["kind"] == "edge"}
        checks["expected_relationships"] = expected <= observed
        checks["correct_numeric_title_anchor"] = any(f"task:{fixture['task_id']}" in (row.get("node_ref"), row.get("from_ref"), row.get("to_ref")) for row in graph)
        checks["canonical_task"] = any(row["resource_type"] == "task" and row["resource_id"] == fixture["task_id"] for row in resources)
    if case["kind"] == "task_content":
        checks["supplier_fact"] = "햇살상사" in answer
        checks["due_date_fact"] = bool(re.search(r"2026[-./]0?9[-./]30|9\s*월\s*30\s*일", answer))
    if case["kind"] == "independent":
        checks["approval_owner_fact"] = "운영기획팀" in answer
        checks["submission_date_fact"] = bool(re.search(r"2026[-./]0?9[-./]25|9\s*월\s*25\s*일", answer))
    if case["kind"] == "meeting":
        checks["meeting_segment_locator"] = any(row.get("source_locator", {}).get("source_revision_id") == fixture["raw_revision_id"] and row["source_locator"].get("start_ms") == 1200 for row in evidence)
        checks["launch_date_fact"] = bool(re.search(r"2026[-./]10[-./]15|10\s*월\s*15\s*일", answer))
        checks["launch_owner_fact"] = "민아" in answer
    if case["kind"] == "followup":
        checks["checkpoint_reset"] = not trace.get("checkpoint_reused", True)
        checks["canonical_seed"] = f"material:{fixture['task_material']}" in trace.get("seed_refs", [])
        checks["payment_fact"] = bool(re.search(r"납품\s*후\s*30\s*일", answer))
    if case["kind"] == "precreation":
        actions = [row for row in view["actions"] if row["turn_id"] == turn_id]
        checks["pending_proposal_with_evidence"] = any(row["state"] == "pending" and any(field["id"] == "evidence" and "노을 견적서.md" in field["value"] for field in row["preview"]) for row in actions)
    if case["kind"] == "revoked_followup":
        checks["checkpoint_reset"] = not trace.get("checkpoint_reused", True)
        checks["revoked_receipt_absent"] = not evidence and all(row["resource_id"] != fixture["personal_material"] for row in view["answer_resources"])
        checks["revoked_seed_absent"] = f"material:{fixture['personal_material']}" not in trace.get("seed_refs", [])
        checks["old_fact_not_replayed"] = "운영기획팀" not in answer and not re.search(r"2026[-./]0?9[-./]25|9\s*월\s*25\s*일", answer)
    invalid_ids = 0
    for call in trace.get("calls", {}).values():
        for key, value in call.get("arguments", {}).items():
            if key == "node" and isinstance(value, str):
                kind, separator, identifier = value.partition(":")
                if not separator:
                    invalid_ids += 1
                elif kind not in {"person", "team"}:
                    try:
                        UUID(identifier)
                    except ValueError:
                        invalid_ids += 1
            if key in {"task_id", "request_id", "meeting_id", "material_id", "resource_id"} and value is not None:
                try:
                    UUID(str(value))
                except ValueError:
                    invalid_ids += 1
    checks["no_malformed_id"] = invalid_ids == 0
    return {"kind": case["kind"], "question": case["question"], "state": turn["state"], "checks": checks,
            "passed": all(checks.values()), "tool_names": names, "tool_error_count": errors, "business_error_count": business_errors,
            "malformed_id_count": invalid_ids, "over_call_count": max(0, len(tools)-case["max_calls"]),
            "recovered_after_error": bool((errors or business_errors) and all(value for key, value in checks.items() if key not in {"first_tool", "within_call_budget", "no_tool_error", "no_malformed_id"})),
            "queue_wait_ms": turn.get("queue_wait_ms"), "run_ms": turn.get("run_ms"),
            "tool_latency_ms": sum(row.get("latency_ms") or 0 for row in tools), "usage": turn.get("usage"),
            "material_receipts": len(evidence), "graph_edges": sum(row["kind"] == "edge" for row in graph),
            "answer_resource_count": len(resources), "answer_sha256": sha256(answer.encode()).hexdigest(),
            "observed_relations": [{key: row.get(key) for key in ("kind", "node_ref", "edge_kind", "from_ref", "to_ref")} for row in graph],
            "answer_refs": [{key: row.get(key) for key in ("resource_type", "resource_id")} for row in resources],
            "trace": trace}


async def send_turn(client, worker, provider, fixture, case, *, conversation_id=None):
    if conversation_id is None:
        conversation_id = client.post("/api/conversations", headers=ACTOR, json={"title": f"routing-{case['kind']}"}).json()["conversation_id"]
    response = client.post(f"/api/conversations/{conversation_id}/messages", headers={**ACTOR, "Idempotency-Key": str(uuid4())}, json={"body": case["question"], "context": []})
    if response.status_code != 202:
        raise ValueError("conversation input failed")
    turn_id = response.json()["turn_id"]
    before = len(client.get("/api/tasks", headers=ACTOR).json())
    if not await worker.run_once():
        raise ValueError("conversation worker did not claim the turn")
    view = client.get(f"/api/conversations/{conversation_id}", headers=ACTOR).json()
    result = assess(case, view, turn_id, fixture, provider.trace)
    result["checks"]["no_unapproved_task_creation"] = before == len(client.get("/api/tasks", headers=ACTOR).json())
    result["passed"] = all(result["checks"].values())
    print(json.dumps({key: result[key] for key in ("kind", "state", "passed", "tool_names", "run_ms", "checks")}, ensure_ascii=False), flush=True)
    return conversation_id, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--focus", nargs="+", choices=("relation", "meeting", "precreation", "independent"))
    parser.add_argument("--confirmable-proposal", action="store_true", help="use an explicit approval-proposal question in the focused precreation evaluation")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if not 1 <= options.repeats <= 3 or options.output.exists() or not options.output.resolve().is_relative_to(Path(gettempdir()).resolve()):
        parser.error("use 1–3 repeats and a new OS-temp output path")
    if options.confirmable_proposal and (not options.focus or "precreation" not in options.focus):
        parser.error("--confirmable-proposal requires --focus precreation")
    cases = [dict(case) for case in CASES if not options.focus or case["kind"] in options.focus]
    if options.confirmable_proposal:
        next(case for case in cases if case["kind"] == "precreation")["question"] = "노을 견적서에서 납기일을 먼저 확인하고, 그 근거로 내가 승인할 수 있는 납품 검토 업무 생성안을 준비해줘."
    logging.disable(logging.CRITICAL)
    with TemporaryDirectory(prefix="scax-routing-eval-") as temporary:
        work = Path(temporary)
        client, application, settings, fixture = setup(work)
        from ax_workspace.bootstrap.conversation_worker import ConversationWorker
        from ax_workspace.platform.conversation_jobs import ConversationJobQueue
        provider = ObservedProvider(settings, work)
        summary = {"profile": "scax-natural-routing-v2", "live": options.live, "model": provider.profile.model,
            "service_tier": provider.profile.service_tier, "reasoning_effort": provider.profile.reasoning_effort,
            "fixture_kind": "synthetic_only", "fixture": fixture, "focus": options.focus,
            "confirmable_proposal": options.confirmable_proposal, "repeats": options.repeats, "results": []}
        source_root = Path(__file__).parents[1]
        summary["source_sha256"] = {name: sha256((source_root / name).read_bytes()).hexdigest() for name in (
            "scripts/search_routing_live_profile.py", "src/ax_workspace/platform/codex_cli.py",
            "src/ax_workspace/entrypoints/mcp.py", "src/ax_workspace/bootstrap/application.py")}
        if options.live:
            summary["cli_version"] = subprocess.run(["codex", "--version"], check=True, capture_output=True, text=True).stdout.strip()
        if options.live:
            worker = ConversationWorker(settings, provider=provider, queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue, worker_id="routing-eval"))
            async def run():
                independent_id = None
                for repeat in range(options.repeats):
                    task_conversation = None
                    for case in cases:
                        cid, result = await send_turn(client, worker, provider, fixture, case,
                            conversation_id=task_conversation if case["kind"] == "followup" else None)
                        result["repeat"] = repeat + 1
                        summary["results"].append(result)
                        options.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                        if case["kind"] == "task_content": task_conversation = cid
                        if case["kind"] == "independent": independent_id = cid
                if independent_id is not None:
                    response = client.post(f"/api/material-folders/{fixture['folder_id']}/archive", headers=ACTOR)
                    if response.status_code != 200:
                        raise ValueError("synthetic permission revocation failed")
                    _, result = await send_turn(client, worker, provider, fixture, REVOKED, conversation_id=independent_id)
                    summary["results"].append(result)
            asyncio.run(run())
        else:
            summary["questions"] = [case["question"] for case in cases]
            summary["fixture_ready"] = True
    summary["temporary_data_removed"] = not work.exists()
    options.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"profile": summary["profile"], "live": options.live, "turns": len(summary["results"]), "passed": sum(row["passed"] for row in summary["results"]), "temporary_data_removed": summary["temporary_data_removed"]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
