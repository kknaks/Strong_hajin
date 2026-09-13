"""Pure decisions and effect requests for confirming an editable AX proposal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ax_workspace.modules.actions.domain import ActionError
from ax_workspace.modules.actions.payloads import (
    attachment_draft_ids,
    normalize_task_progress_batch,
    payload_diff,
    validate_task_progress_batch_edit,
)
from ax_workspace.modules.ax_execution.actions import action_payload_hash
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.meetings.drafts import normalize_meeting_draft
from ax_workspace.modules.work.errors import TaskError
from ax_workspace.modules.work.drafts import (
    normalize_assigned_task_draft,
    normalize_task_draft,
    normalize_work_request_draft,
)
from ax_workspace.modules.work.request_errors import WorkRequestError


SUPPORTED_ACTION_TYPES = frozenset(
    {"task.create_self", "task.assign", "work_request.create", "meeting.create", "task.progress.batch"}
)
ATTACHABLE_ACTION_TYPES = frozenset({"task.create_self", "task.assign", "meeting.create"})


@dataclass(frozen=True, slots=True)
class AxConfirmationContext:
    action_type: str
    state: str
    version: int
    owner_id: str
    decision_open: bool
    submission_version: int
    base_snapshot: Mapping[str, Any]
    has_active_assignment: bool


@dataclass(frozen=True, slots=True)
class OpenAxSubmissionRound:
    submission_version: int
    snapshot: dict[str, Any]
    diff: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AxConfirmationDecision:
    final_snapshot: dict[str, Any]
    final_draft: dict[str, Any]
    attachment_draft_ids: tuple[str, ...]
    open_round: OpenAxSubmissionRound | None
    expected_version: int
    base_submission_version: int


@dataclass(frozen=True, slots=True)
class AxReplayContext:
    state: str
    version: int
    has_decision_item: bool
    stored_actor_id: str | None
    stored_decision: str | None
    stored_conditions: Mapping[str, Any]


def required_version(payload: Mapping[str, Any]) -> int:
    value = payload.get("expected_version")
    if value is None or isinstance(value, bool):
        raise ActionError("expected_version is required")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ActionError("expected_version must be an integer") from error


def required_base_submission_version(payload: Mapping[str, Any]) -> int:
    value = payload.get("base_submission_version")
    if value is None or isinstance(value, bool):
        raise ActionError("base_submission_version is required")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ActionError("base_submission_version must be an integer") from error


def is_ax_replay(
    context: AxReplayContext,
    *,
    actor_id: str,
    command: str,
    normalized_payload: Mapping[str, Any],
) -> bool:
    expected_state = {"confirm": "approved", "approve": "approved", "reject": "rejected"}.get(command)
    if context.state != expected_state:
        return False
    try:
        expected_version = required_version(normalized_payload)
    except ActionError:
        return False
    if context.version != expected_version + 1:
        return False
    if not context.has_decision_item:
        return command == "reject"
    if context.stored_actor_id != actor_id or context.stored_decision != command:
        return False
    conditions = context.stored_conditions
    if command in {"approve", "reject"}:
        return conditions.get("expected_version") == expected_version
    if command != "confirm":
        return False
    attachments = list(normalized_payload.get("attachment_draft_ids") or [])
    effect = {
        **dict(normalized_payload.get("draft") or {}),
        **({"attachment_draft_ids": attachments} if attachments else {}),
    }
    return (
        conditions.get("expected_version") == expected_version
        and conditions.get("base_submission_version") == normalized_payload.get("base_submission_version")
        and conditions.get("payload_hash") == action_payload_hash(effect)
        and conditions.get("attachment_draft_ids", []) == attachments
    )


def normalize_ax_draft(
    action_type: str,
    value: Any,
    *,
    frozen_meeting_source: dict[str, Any] | None = None,
    requester_id: str | None = None,
) -> dict[str, Any]:
    if action_type == "task.progress.batch":
        try:
            return normalize_task_progress_batch(dict(value) if isinstance(value, dict) else value)
        except (TaskError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
    if action_type == "work_request.create":
        try:
            return normalize_work_request_draft(value, requester_id=requester_id)
        except (WorkRequestError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
    if action_type != "meeting.create":
        try:
            fields = dict(value) if isinstance(value, dict) else value
            if isinstance(fields, dict):
                fields.pop("attachment_draft_ids", None)
            return normalize_assigned_task_draft(fields) if action_type == "task.assign" else normalize_task_draft(fields)
        except (TaskError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
    try:
        fields = dict(value) if isinstance(value, dict) else value
        if isinstance(fields, dict):
            fields.pop("attachment_draft_ids", None)
        if isinstance(fields, dict) and frozen_meeting_source is not None:
            fields["initial_note_source_status"] = frozen_meeting_source.get(
                "initial_note_source_status", "not_requested"
            )
            fields["initial_note_source_evidence"] = list(
                frozen_meeting_source.get("initial_note_source_evidence") or []
            )
        return normalize_meeting_draft(fields)
    except (MeetingError, TypeError, ValueError) as error:
        raise ActionError(str(error)) from error


def decide_ax_confirmation(
    context: AxConfirmationContext,
    payload: Mapping[str, Any],
) -> AxConfirmationDecision:
    if context.state != "pending":
        raise ActionError("action is no longer pending")
    expected_version = required_version(payload)
    if context.version != expected_version:
        raise ActionError("action version is stale")
    if not context.decision_open:
        raise ActionError("action is no longer pending")
    base_submission_version = required_base_submission_version(payload)
    if context.submission_version != base_submission_version:
        raise ActionError("base submission version is stale")
    if context.action_type not in SUPPORTED_ACTION_TYPES:
        raise ActionError("이 AX 제안은 아직 수정 확정을 지원하지 않습니다")

    raw_base = dict(context.base_snapshot)
    canonical_base = normalize_ax_draft(
        context.action_type,
        raw_base,
        requester_id=context.owner_id,
    )
    canonical_final = normalize_ax_draft(
        context.action_type,
        payload.get("draft"),
        frozen_meeting_source=canonical_base if context.action_type == "meeting.create" else None,
        requester_id=context.owner_id,
    )
    if context.action_type == "task.progress.batch":
        validate_task_progress_batch_edit(canonical_base, canonical_final)

    base_attachments = (
        attachment_draft_ids(raw_base.get("attachment_draft_ids"))
        if context.action_type in ATTACHABLE_ACTION_TYPES
        else []
    )
    attachments = (
        attachment_draft_ids(payload.get("attachment_draft_ids"))
        if context.action_type in ATTACHABLE_ACTION_TYPES
        else []
    )
    base_snapshot = {**canonical_base, **({"attachment_draft_ids": base_attachments} if base_attachments else {})}
    final_snapshot = {**canonical_final, **({"attachment_draft_ids": attachments} if attachments else {})}
    revised = final_snapshot != base_snapshot
    if not revised and not context.has_active_assignment:
        raise ActionError("active AX review assignment was not found")
    return AxConfirmationDecision(
        final_snapshot=final_snapshot,
        final_draft=canonical_final,
        attachment_draft_ids=tuple(attachments),
        open_round=(
            OpenAxSubmissionRound(
                submission_version=context.submission_version + 1,
                snapshot=final_snapshot,
                diff=payload_diff(raw_base, final_snapshot),
            )
            if revised
            else None
        ),
        expected_version=expected_version,
        base_submission_version=base_submission_version,
    )
