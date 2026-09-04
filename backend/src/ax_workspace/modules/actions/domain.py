"""The shape every judgement wears, whatever raised it.

An ActionItem is one independent question put to a person. A revision of the same question is a new Submission on the
same ActionItem, never a second item. The envelope below is the only thing a client sees: it carries the server's own
presentation and the commands policy allows right now, so no consumer re-derives a control, a field, or a permission
from the kind.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ax_workspace.modules.organization_access.domain import Principal

#: The principal owes an answer on the current Submission.
AWAITING_REVIEW = "awaiting_review"
#: A reviewer asked for a change; the submitter owes the next Submission.
AWAITING_REVISION = "awaiting_revision"
#: The question has been answered; it is history, not work.
RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class ActionCommand:
    """One thing the current principal may do to this ActionItem right now."""

    id: str
    label: str
    tone: str = "neutral"
    requires_reason: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "tone": self.tone, "requires_reason": self.requires_reason}


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    """One pending or historical judgement, ready to render."""

    action_item_id: str
    kind: str
    status: str
    subject: str
    operation_label: str
    current_question: str
    preview: list[dict[str, str]]
    allowed_commands: list[ActionCommand]
    submission_version: int
    waiting_on: dict[str, str] | None
    resource: dict[str, str]
    expected_version: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_item_id": self.action_item_id,
            "kind": self.kind,
            "status": self.status,
            "subject": self.subject,
            "operation_label": self.operation_label,
            "current_question": self.current_question,
            "preview": self.preview,
            "allowed_commands": [command.as_dict() for command in self.allowed_commands],
            "submission_version": self.submission_version,
            "waiting_on": self.waiting_on,
            "resource": self.resource,
            "expected_version": self.expected_version,
            **self.extra,
        }


class ActionKindHandler(Protocol):
    """Each origin keeps its own business rules; only the judgement shape is shared."""

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        """ActionItems of this kind that `principal` must answer now."""
        ...


class ActionCenterApplication:
    """The one query behind `판단할 일`: every kind, one envelope, ordered by how long it has waited."""

    def __init__(self, handlers: list[ActionKindHandler]) -> None:
        self._handlers = handlers

    def pending(self, principal: Principal) -> list[dict[str, Any]]:
        items: list[ActionEnvelope] = []
        for handler in self._handlers:
            items.extend(handler.pending(principal))
        return [item.as_dict() for item in items]
