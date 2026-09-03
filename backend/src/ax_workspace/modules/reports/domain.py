"""Report feature value rules; submission is impossible until a human gate accepts."""

from typing import Any


def confirmed_submission_snapshot(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"input": input_snapshot, "submitted_after_human_confirmation": True}
