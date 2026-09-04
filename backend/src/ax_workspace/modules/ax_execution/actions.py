"""Human-confirmed AX effects, independent of WorkflowDefinition/WorkflowRun."""
from __future__ import annotations

from typing import Any, Protocol
import hashlib
import json
from uuid import UUID

from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, ACTION_READ, Principal


class ActionError(ValueError):
    pass


class ActionAccessDenied(ActionError):
    pass


class ActionCapabilityDenied(ActionAccessDenied):
    pass


class ActionRepository(Protocol):
    def propose(
        self,
        owner_id: str,
        execution_id: UUID,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> Any: ...

    def action(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> Any | None: ...

    def list_for(self, owner_id: str) -> list[Any]: ...

    def resolve(self, action: Any, actor_id: str, decision: str, result: dict[str, Any] | None) -> None: ...

    def view(self, action: Any, principal: Principal | None = None) -> dict[str, Any]: ...


#: The generic gated wrapper a delegated turn proposes when it wants a judgement made on an ActionItem.
#: The turn prepares the answer; only a person approving this Action applies it through the canonical command path.
ACTION_ITEM_COMMAND = "action_item.command"
#: The stored title of such a wrapper. The work it judges is named only when the reader may still read it, so nothing
#: on the row itself outlives their access to it.
ACTION_ITEM_COMMAND_TITLE = "판단 확인"


def action_payload_hash(payload: dict[str, Any]) -> str:
    """The identity of a proposed effect. Callers compare this, never a display string."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ActionExecutor(Protocol):
    def execute(self, principal: Principal, action: Any) -> dict[str, Any]: ...


class ActionApplication:
    def __init__(self, repository: ActionRepository, executor: ActionExecutor) -> None:
        self._repository = repository
        self._executor = executor

    def propose(
        self,
        principal: Principal,
        execution_id: UUID,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require(principal, ACTION_DECIDE)
        return self._repository.view(
            self._repository.propose(str(principal.id), execution_id, action_type, title, payload), principal
        )

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, ACTION_READ)
        can_decide = ACTION_DECIDE in principal.capabilities
        views = []
        for action in self._repository.list_for(str(principal.id)):
            view = self._repository.view(action, principal)
            view["commands"] = action_commands(view.get("state"), can_decide)
            views.append(view)
        return views

    def decide(
        self,
        principal: Principal,
        action_id: UUID,
        expected_version: int,
        decision: str,
    ) -> dict[str, Any]:
        self._require(principal, ACTION_DECIDE)
        if decision not in {"approve", "reject"}:
            raise ActionError("action decision must be approve or reject")
        action = self._repository.action(action_id, str(principal.id), lock=True)
        if action is None:
            raise ActionAccessDenied("action was not found")
        resolved_state = "approved" if decision == "approve" else "rejected"
        if action.state == resolved_state:
            # A lost HTTP response (or an at-least-once worker replay) must not make the caller choose between a
            # duplicate effect and a stale error. The receipt is for *this* decision though: deciding bumps the Action
            # exactly once, so only the version this outcome actually consumed replays.
            if action.version != expected_version + 1:
                raise ActionError("action version is stale")
            return self._repository.view(action, principal)
        if action.version != expected_version:
            raise ActionError("action version is stale")
        if action.state != "pending":
            raise ActionError("action is no longer pending")
        result = self._executor.execute(principal, action) if decision == "approve" else None
        self._repository.resolve(action, str(principal.id), decision, result)
        return self._repository.view(action, principal)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise ActionCapabilityDenied(f"{capability} capability is required")


def action_commands(state: Any, can_decide: bool) -> list[dict[str, str]]:
    """Server-provided approval commands (id, label, tone). The client repeats them verbatim and never infers
    controls or wording from the action state."""
    if state != "pending" or not can_decide:
        return []
    return [
        {"id": "approve", "label": "승인", "tone": "primary"},
        {"id": "reject", "label": "거절", "tone": "neutral"},
    ]
