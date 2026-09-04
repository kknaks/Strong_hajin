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


class ActionError(Exception):
    """The command cannot be run as asked."""


class ActionNotFound(ActionError):
    pass


class ActionKindHandler(Protocol):
    """Each origin keeps its own business rules; only the judgement shape is shared."""

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        """ActionItems of this kind that `principal` must answer now."""
        ...

    def find(self, action_item_id: str) -> Any | None:
        """The stored item behind this id, or None when another kind owns it."""
        ...

    def envelope(self, item: Any, principal: Principal) -> ActionEnvelope:
        """How this item looks to `principal` right now, pending or not."""
        ...

    def rounds(self, item: Any, principal: Principal) -> list[dict[str, Any]]:
        """Every immutable Submission with its frozen content, diff and decisions, oldest first."""
        ...

    def discussion(self, item: Any, principal: Principal) -> list[dict[str, Any]]:
        """Comments on this question, in the order they were written. Talking never moves the item."""
        ...

    def execute(self, principal: Principal, item: Any, command: str, payload: dict[str, Any]) -> None:
        """Run the owning module's operation for this command."""
        ...

    def is_replay(self, item: Any, principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """True when *this exact payload* already produced the item's current outcome, so a re-send is a receipt.

        The payload matters: a command of the same shape from an earlier round is a stale request, not a receipt.
        """
        ...


class ActionCenterApplication:
    """The one query and the one command path behind `판단할 일`.

    Authorization is the envelope itself: a command runs only if the server offered it to this principal on this item,
    so a kind cannot be talked into an operation its policy did not allow.
    """

    def __init__(self, handlers: list[ActionKindHandler]) -> None:
        self._handlers = handlers

    def pending(self, principal: Principal) -> list[dict[str, Any]]:
        items: list[ActionEnvelope] = []
        for handler in self._handlers:
            items.extend(handler.pending(principal))
        return [item.as_dict() for item in items]

    def detail(self, principal: Principal, action_item_id: str) -> dict[str, Any]:
        handler, item = self._locate(action_item_id)
        envelope = handler.envelope(item, principal)
        return {
            **envelope.as_dict(),
            "rounds": handler.rounds(item, principal),
            "discussion": handler.discussion(item, principal),
        }

    def execute(self, principal: Principal, action_item_id: str, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler, item = self._locate(action_item_id)
        offered = {entry.id: entry for entry in handler.envelope(item, principal).allowed_commands}
        if command not in offered:
            # A lost response must not force the caller to choose between a duplicate effect and a stale error.
            if handler.is_replay(item, principal, command, payload):
                return handler.envelope(item, principal).as_dict()
            raise ActionError(f"'{command}' is not available on this action item right now")
        if offered[command].requires_reason and not str(payload.get("reason") or "").strip():
            raise ActionError(f"'{command}' requires a reason")
        handler.execute(principal, item, command, payload)
        return handler.envelope(handler.find(action_item_id), principal).as_dict()

    def _locate(self, action_item_id: str) -> tuple[ActionKindHandler, Any]:
        for handler in self._handlers:
            item = handler.find(action_item_id)
            if item is not None:
                return handler, item
        raise ActionNotFound("action item was not found")
