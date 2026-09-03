"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal


class WorkRequestError(Exception):
    pass


class WorkRequestRepository(Protocol):
    def create_request(self, requester_id: str, assignee_id: str, title: str) -> Any: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def create_accepted_task(self, request: Any) -> Any: ...
    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None: ...


class WorkRequestApplication:
    def __init__(self, repository: WorkRequestRepository) -> None:
        self._repository = repository

    def create(self, principal: Principal, title: str, assignee_id: str) -> dict[str, Any]:
        if not title.strip():
            raise WorkRequestError("title is required")
        request = self._repository.create_request(str(principal.id), assignee_id, title.strip())
        self._repository.append_audit(request.id, str(principal.id), "work_request.created", {})
        return self._view(request)

    def accept(self, principal: Principal, request_id: UUID, expected_version: int) -> dict[str, Any]:
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "accepted"
        request.version += 1
        task = self._repository.create_accepted_task(request)
        self._repository.append_audit(request.id, str(principal.id), "work_request.accepted", {"task_id": str(task.id)})
        return self._view(request, task)

    def reject(self, principal: Principal, request_id: UUID, expected_version: int, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise WorkRequestError("rejection reason is required")
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "rejected"
        request.version += 1
        self._repository.append_audit(request.id, str(principal.id), "work_request.rejected", {"reason": reason.strip()})
        return self._view(request)

    def _decision_target(self, principal: Principal, request_id: UUID, expected_version: int) -> Any:
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if request.assignee_id != str(principal.id):
            raise WorkRequestError("only the requested assignee may decide")
        if request.version != expected_version:
            raise WorkRequestError("work request version is stale")
        if request.state not in {"pending", "negotiating"}:
            raise WorkRequestError("work request is not awaiting a decision")
        return request

    @staticmethod
    def _view(request: Any, task: Any | None = None) -> dict[str, Any]:
        return {
            "request_id": str(request.id),
            "title": request.title,
            "state": request.state,
            "version": request.version,
            "task_id": str(task.id) if task else None,
            "assignment_state": "active" if task else None,
        }
