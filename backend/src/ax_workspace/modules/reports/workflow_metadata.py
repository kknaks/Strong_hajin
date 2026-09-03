"""Validated metadata for the only product-integrated workflow.

The JSON document is data, not executable configuration.  The runtime only
interprets node types registered here and only invokes canonical operations
listed in ``ALLOWED_OPERATIONS``.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


SAFE_NODE_TYPES = frozenset({"operation.query", "template.render", "llm.generate", "output.validate"})
ALLOWED_OPERATIONS = frozenset({"work_record.list"})
DAILY_REPORT_PROVIDER_PROFILE = "codex-cli:gpt-5.6-terra:fast:low"


def daily_report_generation_v1() -> dict[str, Any]:
    definition = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "sources",
                "type": "operation.query",
                "operation": "work_record.list",
                "inputs": [],
            },
            {
                "id": "render",
                "type": "template.render",
                "template_ref": "daily-report-v1",
                "inputs": ["sources"],
            },
            {
                "id": "generate",
                "type": "llm.generate",
                "provider_profile": DAILY_REPORT_PROVIDER_PROFILE,
                "inputs": ["render"],
            },
            {
                "id": "validate",
                "type": "output.validate",
                "schema": "daily-report-body-v1",
                "inputs": ["generate"],
            },
        ],
    }
    validate_definition(definition)
    return definition


def validate_definition(definition: dict[str, Any]) -> None:
    if definition.get("schema_version") != 1:
        raise ValueError("workflow metadata must use schema version 1")
    nodes = definition.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("workflow metadata requires an ordered node list")

    seen: set[str] = set()
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in seen:
            raise ValueError("workflow metadata node ids must be unique")
        if node.get("type") not in SAFE_NODE_TYPES:
            raise ValueError("workflow metadata contains an unregistered node type")
        if node.get("type") == "operation.query" and node.get("operation") not in ALLOWED_OPERATIONS:
            raise ValueError("workflow metadata references an unregistered operation")
        if node.get("type") == "llm.generate" and node.get("provider_profile") != DAILY_REPORT_PROVIDER_PROFILE:
            raise ValueError("workflow metadata references an unapproved provider profile")
        inputs = node.get("inputs", [])
        if not isinstance(inputs, list) or any(input_id not in seen for input_id in inputs):
            raise ValueError("workflow metadata inputs must reference preceding nodes")
        seen.add(node_id)


def content_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode()).hexdigest()
