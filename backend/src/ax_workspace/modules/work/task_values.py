"""Task-owned value normalization shared by commands and drafts."""

from __future__ import annotations

from datetime import date
from typing import Any

from ax_workspace.modules.work.errors import TaskError


MAX_INITIAL_STEPS = 50


def clean_checklist(texts: Any) -> list[str]:
    if not texts:
        return []
    if isinstance(texts, str) or not isinstance(texts, (list, tuple)):
        raise TaskError("checklist must be a list of steps")
    cleaned = []
    for text in texts:
        step = " ".join(str(text).split())
        if step:
            cleaned.append(step[:300])
    if len(cleaned) > MAX_INITIAL_STEPS:
        raise TaskError(f"a new task can start with at most {MAX_INITIAL_STEPS} steps")
    return cleaned


def validate_schedule(start_date: date | None, due_date: date | None) -> None:
    if start_date is not None and due_date is not None and start_date > due_date:
        raise TaskError("start date cannot be later than the due date")
