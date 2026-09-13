"""Pure receipt identity rules for Action Item retries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ax_workspace.modules.actions.domain import ActionError
from ax_workspace.modules.actions.payloads import proposed_changes


@dataclass(frozen=True, slots=True)
class WorkRequestReplayContext:
    request_state: str
    request_version: int
    requester_id: str
    submission_actor_id: str | None
    revision_consumed_version: int | None
    current_snapshot: Mapping[str, Any] | None
    previous_snapshot: Mapping[str, Any] | None
    decision_actor_id: str | None
    decision: str | None
    decision_expected_version: int | None
    decision_reason: str | None
    decision_suggested_changes: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AssignmentReplayContext:
    status: str
    assignee_id: str
    task_version: int
    decline_reason: str | None


@dataclass(frozen=True, slots=True)
class RecordedDeliveryDecision:
    actor_id: str
    decision: str
    expected_version: int
    reason: str | None


def is_work_request_replay(
    context: WorkRequestReplayContext,
    *,
    actor_id: str,
    command: str,
    payload: Mapping[str, Any],
) -> bool:
    targeted = _version(payload.get("expected_version"))
    if targeted is None or context.current_snapshot is None:
        return False
    if command == "revise":
        return (
            context.submission_actor_id == actor_id
            and context.revision_consumed_version == targeted
            and context.previous_snapshot is not None
            and _apply_revision(context.previous_snapshot, payload.get("changes")) == dict(context.current_snapshot)
        )
    if command == "withdraw":
        return (
            context.request_state == "withdrawn"
            and context.requester_id == actor_id
            and context.request_version == targeted + 1
        )
    expected_decision = {"accept": "accept", "reject": "reject", "adjust": "negotiate"}.get(command)
    if (
        expected_decision is None
        or context.decision_actor_id != actor_id
        or context.decision != expected_decision
        or context.decision_expected_version != targeted
    ):
        return False
    if command == "accept":
        return True
    if (context.decision_reason or "") != str(payload.get("reason") or "").strip():
        return False
    if command != "adjust":
        return True
    try:
        changes = proposed_changes(payload.get("changes"))
    except ActionError:
        return False
    return dict(context.decision_suggested_changes) == changes


def is_assignment_replay(
    context: AssignmentReplayContext,
    *,
    actor_id: str,
    command: str,
    expected_version: int,
    reason: str | None,
) -> bool:
    if context.assignee_id != actor_id:
        return False
    if command == "accept":
        return context.status == "active" and context.task_version == expected_version
    return (
        command == "decline"
        and context.status == "declined"
        and context.task_version == expected_version + 1
        and (context.decline_reason or "") == str(reason or "").strip()
    )


def is_delivery_replay(
    decisions: Sequence[RecordedDeliveryDecision],
    *,
    actor_id: str,
    command: str,
    expected_version: int,
    reason: str | None,
) -> bool:
    expected_decision = "accept" if command == "accept" else "negotiate"
    clean_reason = str(reason or "").strip()
    return any(
        decision.actor_id == actor_id
        and decision.decision == expected_decision
        and decision.expected_version == expected_version
        and (expected_decision != "negotiate" or (decision.reason or "") == clean_reason)
        for decision in decisions
    )


def _version(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_revision(snapshot: Mapping[str, Any], value: Any) -> dict[str, Any]:
    revised = dict(snapshot)
    changes = dict(value or {}) if isinstance(value, Mapping) else {}
    if changes.get("title") is not None:
        revised["title"] = str(changes["title"]).strip()
    if changes.get("description") is not None:
        revised["description"] = str(changes["description"]).strip() or None
    if changes.get("clear_due_date"):
        revised["due_date"] = None
    elif changes.get("due_date") is not None:
        revised["due_date"] = str(changes["due_date"]).strip() or None
    return revised
