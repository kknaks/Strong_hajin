"""The live evaluation rubric rejects plausible answers without the expected observed tool path."""
import importlib.util
from pathlib import Path
from uuid import uuid4


def _module(monkeypatch):
    directory = Path(__file__).parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("search_routing_live_profile", directory / "search_routing_live_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _view(identifier, *, tool="material_search", state="completed"):
    return {"turns": [{"turn_id": "t1", "state": "completed"}],
        "tool_invocations": [{"turn_id": "t1", "tool_name": tool, "state": state}],
        "material_evidence": [{"turn_id": "t1", "material_id": identifier}],
        "answer_resources": [{"turn_id": "t1", "resource_type": "material", "resource_id": identifier, "origin": "/synthetic"}],
        "graph_receipts": [], "actions": [],
        "messages": [{"turn_id": "t1", "role": "assistant", "body": "햇살상사, 2026-09-30"}]}


def test_factually_matching_answer_without_content_search_is_not_a_routing_pass(monkeypatch):
    module = _module(monkeypatch)
    identifier = str(uuid4())
    case = next(row for row in module.CASES if row["kind"] == "task_content")
    result = module.assess(case, _view(identifier, tool="task_list"), "t1", {"task_material": identifier}, {"calls": {}})
    assert result["checks"]["supplier_fact"] and result["checks"]["due_date_fact"]
    assert not result["passed"] and not result["checks"]["required_tools"]


def test_numeric_title_as_node_id_and_business_errors_are_counted(monkeypatch):
    module = _module(monkeypatch)
    identifier = str(uuid4())
    case = next(row for row in module.CASES if row["kind"] == "task_content")
    trace = {"calls": {"bad": {"arguments": {"node": "task:1788669499950"}, "error_code": "invalid_query"}}}
    result = module.assess(case, _view(identifier), "t1", {"task_material": identifier}, trace)
    assert not result["passed"] and result["malformed_id_count"] == result["business_error_count"] == 1


def test_question_set_does_not_tell_the_model_tool_names(monkeypatch):
    module = _module(monkeypatch)
    names = {name for case in module.CASES for name in case["required"]}
    assert all(name not in case["question"] for case in [*module.CASES, module.REVOKED] for name in names)


def test_relation_requires_requested_edges_and_canonical_task_link(monkeypatch):
    module = _module(monkeypatch)
    fixture = {key: str(uuid4()) for key in ("task_id", "child_task_id", "request_id", "task_material")}
    case = next(row for row in module.CASES if row["kind"] == "relation")
    view = _view(fixture["task_id"], tool="graph_search")
    view["tool_invocations"].append({"turn_id": "t1", "tool_name": "graph_neighbors", "state": "completed"})
    view["answer_resources"] = []
    view["graph_receipts"] = [
        {"turn_id": "t1", "kind": "node", "node_ref": f"task:{fixture['task_id']}"},
        {"turn_id": "t1", "kind": "edge", "edge_kind": "member_of", "from_ref": "person:mina", "to_ref": "team:product"},
    ]
    result = module.assess(case, view, "t1", fixture, {"calls": {}})
    assert not result["passed"]
    assert not result["checks"]["expected_relationships"]
    task, request = f"task:{fixture['task_id']}", f"work_request:{fixture['request_id']}"
    for kind, source, target in (
        ("holds", "person:jiho", task), ("produced", request, task),
        ("requested", "person:mina", request), ("asked_of", request, "person:jiho"),
        ("parent_of", task, f"task:{fixture['child_task_id']}"),
        ("has_material", task, f"material:{fixture['task_material']}"),
    ):
        view["graph_receipts"].append({"turn_id": "t1", "kind": "edge", "edge_kind": kind, "from_ref": source, "to_ref": target})
    assert not module.assess(case, view, "t1", fixture, {"calls": {}})["passed"]
    view["answer_resources"] = [{"turn_id": "t1", "resource_type": "task", "resource_id": fixture["task_id"]}]
    assert module.assess(case, view, "t1", fixture, {"calls": {}})["passed"]


def test_meeting_receipt_without_requested_facts_is_not_a_pass(monkeypatch):
    module = _module(monkeypatch)
    fixture = {"meeting_material": str(uuid4()), "raw_revision_id": str(uuid4())}
    case = next(row for row in module.CASES if row["kind"] == "meeting")
    view = _view(fixture["meeting_material"])
    view["material_evidence"][0]["source_locator"] = {"source_revision_id": fixture["raw_revision_id"], "start_ms": 1200}
    view["messages"][0]["body"] = "날짜와 담당자를 확인할 수 없습니다."
    assert not module.assess(case, view, "t1", fixture, {"calls": {}})["passed"]
    view["messages"][0]["body"] = "출시일은 2026년 10월 15일, 담당자는 민아입니다."
    assert module.assess(case, view, "t1", fixture, {"calls": {}})["passed"]
