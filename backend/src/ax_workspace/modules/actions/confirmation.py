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
from ax_workspace.modules.ax_execution.command_contracts import COMMAND_CONTRACTS
from ax_workspace.modules.actions.policy import RETIRED_ACTION_TYPES
from ax_workspace.modules.meetings.commands import MeetingInfoPatch, MeetingReservationInput
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.work.errors import TaskError
from ax_workspace.modules.work.drafts import (
    normalize_assigned_task_draft,
    normalize_task_draft,
    normalize_work_request_draft,
)
from ax_workspace.modules.work.request_errors import WorkRequestError


SUPPORTED_ACTION_TYPES = frozenset(COMMAND_CONTRACTS) | frozenset(
    {
        "task.create_self",
        "task.assign",
        "work_request.create",
        "meeting.reservation.create",
        # AX 회의 수정 카드 — 편집 계약 `editor="meeting_update"` (SPEC-010 §2.4 · §4.3 · WP3 계약 고정 1).
        "meeting.info.update",
        "task.progress.batch",
    }
)
#: AX 회의 수정 카드에서 사람이 고칠 수 있는 칸 — 장소 글자 칸은 없다(OQ-1005 · W-r2-6). 회의실은 `room`.
MEETING_UPDATE_DRAFT_FIELDS = frozenset(
    {"title", "purpose", "starts_at", "ends_at", "attendee_ids", "external_attendees", "room"}
)
ATTACHABLE_ACTION_TYPES = frozenset(
    {"task.create_self", "task.assign", "meeting.reservation.create"}
)


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
    requester_id: str | None = None,
) -> dict[str, Any]:
    if action_type in COMMAND_CONTRACTS:
        try:
            return COMMAND_CONTRACTS[action_type].normalize(value)
        except (TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
    if action_type == "task.progress.batch":
        try:
            return normalize_task_progress_batch(dict(value) if isinstance(value, dict) else value)
        except (TaskError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
    if action_type == "meeting.info.update":
        return normalize_meeting_update(value)
    if action_type == "work_request.create":
        try:
            return normalize_work_request_draft(value, requester_id=requester_id)
        except (WorkRequestError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
    if action_type != "meeting.reservation.create":
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
        return MeetingReservationInput.model_validate(fields).model_dump(mode="json")
    except (MeetingError, TypeError, ValueError) as error:
        raise ActionError(str(error)) from error


#: 「바뀐 칸만」 덮는 AX 생성 초안 — 사람이 [수정]→저장할 때와 AX 의 `save_draft` 가 같은 규칙을 탄다(E-6 · OQ-901).
PATCHABLE_DRAFT_ACTION_TYPES = frozenset({"task.create_self", "work_request.create", "meeting.reservation.create"})
#: 그 종류의 편집 계약에 없는 칸 — 값을 보내면 422 다(조용히 버리지 않는다). 회의 장소는 회의실(`room_id`)로만 정한다(W-r2-6).
_LOCKED_DRAFT_FIELDS: dict[str, frozenset[str]] = {"meeting.reservation.create": frozenset({"location"})}


def merge_creation_draft(
    action_type: str,
    base: Mapping[str, Any],
    draft: Any,
    *,
    requester_id: str | None = None,
) -> dict[str, Any]:
    """**부분 수정** — 보낸 칸만 지금 회차 위에 덮는다 (E-6 · 메디니스 `merge_intake_patch` 와 같은 규칙).

    - 보낸 칸만 바꾸고 안 보낸 칸은 지금 회차 그대로다. 전체를 보내도(화면) 결과가 같다.
    - 값 `null` = 비우기 — 비울 수 없는 칸(제목·시각 등)은 생성 명령의 검증이 422 로 거절한다.
    - 목록 칸(체크리스트·참석자·안건 …)은 **목록 전체를 보내면 그 목록으로 교체**한다. 항목 단위 더하기·빼기는 없다.
    - 모르는 칸 · 바꿀 수 없는 칸은 422 다 — 조용히 무시하지 않는다(사유는 그대로 호출자에게 간다).
    """
    if not isinstance(draft, Mapping):
        raise ActionError("초안은 칸별 값이어야 합니다")
    locked = sorted(
        key for key in _LOCKED_DRAFT_FIELDS.get(action_type, frozenset()) if draft.get(key) not in (None, "")
    )
    if locked:
        raise ActionError(f"이 초안에서 바꿀 수 없는 항목입니다: {', '.join(locked)}")
    current = normalize_ax_draft(action_type, dict(base), requester_id=requester_id)
    return {**current, **{key: value for key, value in draft.items() if key != "attachment_draft_ids"}}


def normalize_meeting_update(value: Any) -> dict[str, Any]:
    """AX 회의 수정 제안의 정본 모양 — `{meeting_id, changes}`(장소 글자 없음 · 회의실은 `changes.room`).

    `changes` 는 수정 API(`MeetingInfoPatch`)와 같은 검증을 지난다 — 시각 tz · 참석자 합치기 · `room: {room_id}`.
    AX 가 사외 장소를 글자로 냈어도 **저장하지 않는다**(W-r2-6) — 여기서 떼어 낸다.
    """
    if not isinstance(value, dict) or not value.get("meeting_id"):
        raise ActionError("meeting_id is required")
    raw = dict(value.get("changes") or {})
    raw.pop("location", None)
    try:
        patch = MeetingInfoPatch.model_validate(raw)
    except (MeetingError, TypeError, ValueError) as error:
        raise ActionError(str(error)) from error
    changes = patch.model_dump(mode="json", exclude_unset=True, exclude={"room"})
    choice = patch.room_choice()
    if choice is not None:
        changes["room"] = choice
    return {"meeting_id": str(value["meeting_id"]), "changes": changes}


def merge_meeting_update_draft(base: Mapping[str, Any], draft: Any) -> dict[str, Any]:
    """화면이 보낸 확정 `draft` = **바뀐 칸만** + 회의실은 `room: {room_id}`(안 바꾸면 없음) — WP3 계약 고정 1 · 5.

    제안(`base.changes`) 위에 겹친다: 사람이 고친 칸이 이기고, 안 고친 칸은 AX 가 제안한 그대로 남는다. 값 모양
    (`meeting_id`·`room_id`·`room_name`·`proposed_room_*`)이 섞여 와도 읽지 않는다 — 회의는 제안의 회의다.

    **회의실은 draft 의 사람 선택만 따른다**(계약 고정 5 · 검수 F-1): draft 에 `room` 이 없거나 `{keep: true}` 면
    **바꾸지 않는다** — AX 가 제안한 방이 있어도 적용하지 않는다(카드가 그 방을 미리 골라 두므로, 사람이 그대로 두면
    화면이 `room: {room_id: 제안}` 을 싣는다). 「변경 안 함」 을 보고 등록했는데 방이 옮겨지는 일이 없다.
    """
    if not isinstance(draft, dict):
        raise ActionError("draft must be an object")
    unknown = set(draft) - MEETING_UPDATE_DRAFT_FIELDS - {
        "meeting_id", "room_id", "room_name", "proposed_room_id", "proposed_room_name", "room_proposed"
    }
    if unknown:
        raise ActionError(f"unsupported meeting update fields: {sorted(unknown)}")
    edited = {key: value for key, value in draft.items() if key in MEETING_UPDATE_DRAFT_FIELDS}
    proposal = {key: value for key, value in dict(base.get("changes") or {}).items() if key != "room"}
    return normalize_meeting_update({"meeting_id": base["meeting_id"], "changes": {**proposal, **edited}})


def decide_ax_confirmation(
    context: AxConfirmationContext,
    payload: Mapping[str, Any],
) -> AxConfirmationDecision:
    if context.state != "pending":
        raise ActionError("action is no longer pending")
    if context.action_type in RETIRED_ACTION_TYPES:
        raise ActionError(RETIRED_ACTION_TYPES[context.action_type])
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
    final_draft = payload.get("draft")
    canonical_final = (
        merge_meeting_update_draft(canonical_base, final_draft)
        if context.action_type == "meeting.info.update"
        and not (isinstance(final_draft, dict) and "changes" in final_draft)
        else normalize_ax_draft(
            context.action_type,
            final_draft,
            requester_id=context.owner_id,
        )
    )
    if context.action_type in COMMAND_CONTRACTS:
        try:
            COMMAND_CONTRACTS[context.action_type].validate_edit(canonical_base, canonical_final)
        except ValueError as error:
            raise ActionError(str(error)) from error
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
