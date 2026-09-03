"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import (
    Principal,
    WORK_REQUEST_CREATE,
    WORK_REQUEST_DECIDE,
    WORK_REQUEST_READ,
)


class WorkRequestError(Exception):
    pass


class WorkRequestAccessDenied(WorkRequestError):
    pass


class WorkRequestRepository(Protocol):
    def create_request(
        self,
        requester_id: str,
        assignee_id: str,
        title: str,
        causation_key: str | None = None,
    ) -> tuple[Any, bool]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def create_accepted_task(self, request: Any) -> Any: ...
    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None: ...
    def inbox_for(self, assignee_id: str) -> list[Any]: ...
    def list_for(self, principal_id: str) -> list[Any]: ...


class WorkRequestAssigneeDirectory(Protocol):
    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool: ...


class WorkRequestApplication:
    def __init__(self, repository: WorkRequestRepository, assignee_directory: WorkRequestAssigneeDirectory) -> None:
        self._repository = repository
        self._assignee_directory = assignee_directory

    def create(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_CREATE)
        if not title.strip():
            raise WorkRequestError("title is required")
        if not self._assignee_directory.is_work_request_assignee(principal, assignee_id):
            raise WorkRequestError("assignee is not an eligible assignee")
        request, created = self._repository.create_request(
            str(principal.id), assignee_id, title.strip(), causation_key
        )
        if created:
            self._repository.append_audit(request.id, str(principal.id), "work_request.created", {})
        return self._view(request)

    def accept(self, principal: Principal, request_id: UUID, expected_version: int) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_DECIDE)
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "accepted"
        request.version += 1
        task = self._repository.create_accepted_task(request)
        self._repository.append_audit(request.id, str(principal.id), "work_request.accepted", {"task_id": str(task.id)})
        return self._view(request, task)

    def reject(self, principal: Principal, request_id: UUID, expected_version: int, reason: str) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_DECIDE)
        if not reason.strip():
            raise WorkRequestError("rejection reason is required")
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "rejected"
        request.version += 1
        self._repository.append_audit(request.id, str(principal.id), "work_request.rejected", {"reason": reason.strip()})
        return self._view(request)

    def negotiate(
        self,
        principal: Principal,
        request_id: UUID,
        expected_version: int,
        conditions: dict[str, Any],
    ) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_DECIDE)
        if not conditions:
            raise WorkRequestError("negotiation conditions are required")
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "negotiating"
        request.conditions = conditions
        request.version += 1
        self._repository.append_audit(
            request.id, str(principal.id), "work_request.negotiated", {"conditions": conditions}
        )
        return self._view(request)

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, WORK_REQUEST_DECIDE)
        return [self._view(request) for request in self._repository.inbox_for(str(principal.id))]

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, WORK_REQUEST_READ)
        return [self._view(request) for request in self._repository.list_for(str(principal.id))]

    def get(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_READ)
        request = self._repository.request(request_id)
        if request is None:
            raise WorkRequestError("work request was not found")
        if str(principal.id) not in {request.requester_id, request.assignee_id}:
            raise WorkRequestError("principal cannot read this work request")
        return self._view(request)

    def assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        self._require(principal, WORK_REQUEST_CREATE)
        return self._assignee_directory.work_request_assignee_candidates(principal)

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
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise WorkRequestAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(request: Any, task: Any | None = None) -> dict[str, Any]:
        return {
            "request_id": str(request.id),
            "title": request.title,
            "requester_id": request.requester_id,
            "assignee_id": request.assignee_id,
            "state": request.state,
            "version": request.version,
            "task_id": str(task.id) if task else None,
            "assignment_state": "active" if task else None,
            "conditions": request.conditions,
        }
