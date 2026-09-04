"""Evidence-bound Meeting summary suggestion contract."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


STATEMENT_KINDS = frozenset({"summary", "decision", "risk", "followup"})


@dataclass(frozen=True, slots=True)
class SummaryStatement:
    kind: str
    text: str
    refinement_start_sequence: int
    refinement_end_sequence: int


def summary_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["body", "statements"],
        "properties": {
            "body": {"type": "string", "minLength": 1},
            "statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "text", "refinement_start_sequence", "refinement_end_sequence"],
                    "properties": {
                        "kind": {"type": "string", "enum": sorted(STATEMENT_KINDS)},
                        "text": {"type": "string", "minLength": 1},
                        "refinement_start_sequence": {"type": "integer", "minimum": 1},
                        "refinement_end_sequence": {"type": "integer", "minimum": 1},
                    },
                },
            },
        },
    }


def parse_summary(body: str) -> tuple[str, list[SummaryStatement]]:
    try:
        payload = json.loads(body)
        summary_body = str(payload["body"]).strip()
        rows = payload["statements"]
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("summary provider returned an invalid structured response") from error
    if not summary_body or not isinstance(rows, list):
        raise ValueError("summary provider returned an invalid body or statements")
    statements: list[SummaryStatement] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("summary statement must be an object")
        try:
            statement = SummaryStatement(
                kind=str(row["kind"]),
                text=str(row["text"]).strip(),
                refinement_start_sequence=int(row["refinement_start_sequence"]),
                refinement_end_sequence=int(row["refinement_end_sequence"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("summary statement has invalid fields") from error
        if (
            statement.kind not in STATEMENT_KINDS
            or not statement.text
            or statement.refinement_start_sequence > statement.refinement_end_sequence
        ):
            raise ValueError("summary statement violates the strict contract")
        statements.append(statement)
    return summary_body, statements


def build_summary_prompt(refined_segments: list[dict[str, Any]], *, kind: str) -> str:
    evidence = json.dumps(refined_segments, ensure_ascii=False, separators=(",", ":"))
    return (
        "Create a meeting summary only from the refined transcript evidence. Return only the requested JSON. "
        "Do not invent facts, names, numbers, dates, decisions, owners, or follow-up work. Each statement must cite "
        "a contiguous refinement sequence range that supports it. A followup is a candidate, not an executed task. "
        f"This is a {kind} summary. Refined evidence follows:\n{evidence}"
    )
