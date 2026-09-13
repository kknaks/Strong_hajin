"""Pure Meeting material access decisions."""

from __future__ import annotations

from dataclasses import dataclass

from ax_workspace.modules.meetings.domain import MeetingStatus, parse_status


ACCEPTED_CONTENT_TYPES = frozenset({"application/pdf", "text/markdown"})
ACCEPTED_EXTENSIONS = frozenset({".pdf", ".md", ".markdown"})


@dataclass(frozen=True, slots=True)
class MeetingMaterialContext:
    status: MeetingStatus | str
    actor_is_attendee: bool
    actor_id: str
    uploaded_by: str | None


@dataclass(frozen=True, slots=True)
class MaterialAccessDecision:
    can_attach: bool
    can_detach: bool


def decide_material_access(context: MeetingMaterialContext) -> MaterialAccessDecision:
    """Project material controls from Meeting facts without reading or writing storage."""
    current = parse_status(context.status)
    return MaterialAccessDecision(
        can_attach=context.actor_is_attendee and current is not MeetingStatus.IN_PROGRESS,
        can_detach=(
            context.actor_is_attendee
            and is_detachable(current)
            and context.uploaded_by is not None
            and str(context.uploaded_by) == str(context.actor_id)
        ),
    )


def is_detachable(status: object) -> bool:
    """자료를 뗄 수 있는 자리인가 — **「예정」 하나다** (사용자 결정 D34, 2026-09-11).

    회의가 한 번 시작되면 그 자료는 회의에서 실제로 쓰인 것이 된다. 붙이는 것은 되돌릴 수 있지만
    떼는 것은 그 사실을 지우는 쪽이라, 시작 전으로만 자리를 좁혔다.
    """
    return parse_status(status) is MeetingStatus.SCHEDULED


def accepts(name: str, content_type: str) -> bool:
    """Whether a declared type or familiar extension identifies an accepted meeting material."""
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in ACCEPTED_CONTENT_TYPES:
        return True
    lowered = (name or "").lower()
    return any(lowered.endswith(extension) for extension in ACCEPTED_EXTENSIONS)


def inline_media_type(name: str, content_type: str) -> str | None:
    """Return the safe inline type for a meeting material, independent of its stored declaration."""
    declared = (content_type or "").split(";")[0].strip().lower()
    lowered = (name or "").lower()
    if declared == "application/pdf" or lowered.endswith(".pdf"):
        return "application/pdf"
    if declared == "text/markdown" or lowered.endswith((".md", ".markdown")):
        return "text/markdown; charset=utf-8"
    return None
