"""Strict, provenance-preserving transcript refinement contract."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


ALLOWED_CORRECTION_KINDS = frozenset(
    {"none", "deduplicate", "merge", "split", "spacing", "stt_correction", "speaker_boundary"}
)


@dataclass(frozen=True, slots=True)
class RefinedTranscriptSegment:
    raw_start_source_key: str
    raw_end_source_key: str
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None
    correction_kind: str
    confidence: float | None


def refinement_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["segments"],
        "properties": {
            "segments": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "raw_start_source_key",
                        "raw_end_source_key",
                        "start_ms",
                        "end_ms",
                        "text",
                        "speaker_label",
                        "correction_kind",
                        "confidence",
                    ],
                    "properties": {
                        "raw_start_source_key": {"type": "string", "minLength": 1},
                        "raw_end_source_key": {"type": "string", "minLength": 1},
                        "start_ms": {"type": "integer", "minimum": 0},
                        "end_ms": {"type": "integer", "minimum": 1},
                        "text": {"type": "string", "minLength": 1},
                        "speaker_label": {"type": ["string", "null"]},
                        "correction_kind": {"type": "string", "enum": sorted(ALLOWED_CORRECTION_KINDS)},
                        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                    },
                },
            }
        },
    }


def parse_refinement(body: str) -> list[RefinedTranscriptSegment]:
    try:
        payload = json.loads(body)
        rows = payload["segments"]
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("refinement provider returned an invalid structured response") from error
    if not isinstance(rows, list) or not rows:
        raise ValueError("refinement provider returned no segments")
    result: list[RefinedTranscriptSegment] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("refinement segment must be an object")
        try:
            segment = RefinedTranscriptSegment(
                raw_start_source_key=str(row["raw_start_source_key"]).strip(),
                raw_end_source_key=str(row["raw_end_source_key"]).strip(),
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                text=str(row["text"]).strip(),
                speaker_label=(str(row["speaker_label"]).strip() if row["speaker_label"] is not None else None),
                correction_kind=str(row["correction_kind"]),
                confidence=(float(row["confidence"]) if row["confidence"] is not None else None),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("refinement segment has invalid fields") from error
        if (
            not segment.raw_start_source_key
            or not segment.raw_end_source_key
            or not segment.text
            or segment.end_ms <= segment.start_ms
            or segment.correction_kind not in ALLOWED_CORRECTION_KINDS
            or (segment.confidence is not None and not 0 <= segment.confidence <= 1)
        ):
            raise ValueError("refinement segment violates the strict contract")
        result.append(segment)
    return result


def build_refinement_prompt(raw_segments: list[dict[str, Any]]) -> str:
    """The schema and server verifier are the safety boundary; prompt makes semantic limits explicit."""
    evidence = json.dumps(raw_segments, ensure_ascii=False, separators=(",", ":"))
    return (
        "Refine this immutable meeting STT into a readable Korean transcript. Return only the requested JSON. "
        "You may remove exact repetitions, repair spacing/punctuation, merge or split turns, correct only an obvious "
        "speech-recognition error, and adjust anonymous speaker turn boundaries. Never invent or normalize names, "
        "numbers, dates, decisions, attendees, or facts. Preserve every raw source segment through source-key ranges; "
        "use the supplied anonymous speaker labels only. Raw evidence follows:\n"
        f"{evidence}"
    )
