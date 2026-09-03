"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from datetime import date
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
        *,
        description: str | None = None,
        due_date: date | None = None,
    ) -> tuple[Any, bool]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def create_accepted_task(self, request: Any) -> Any: ...
    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None: ...
    def inbox_for(self, assignee_id: str) -> list[Any]: ...
    def list_for(self, principal_id: str) -> list[Any]: ...
    def record_decision(self, request: Any, actor_id: str, decision: str, *, reason: str | None = None, conditions: dict[str, Any] | None = None) -> Any: ...
    def resubmit(self, request: Any, actor_id: str, snapshot: dict[str, Any]) -> Any: ...
    def current_submission(self, request: Any) -> Any: ...
    def timeline(self, request: Any) -> dict[str, Any]: ...


class CommentRepository(Protocol):
    def add(self, request_thread_id: UUID, author_id: str, body: str) -> Any: ...
    def list_for(self, request_thread_id: UUID) -> list[Any]: ...


class WorkRequestAssigneeDirectory(Protocol):
    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool: ...


class WorkRequestApplication:
    def __init__(
        self,
        repository: WorkRequestRepository,
        assignee_directory: WorkRequestAssigneeDirectory,
        comments: CommentRepository | None = None,
    ) -> None:
        self._repository = repository
        self._assignee_directory = assignee_directory
        self._comments = comments

    def create(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        due_date: date | None = None,
    ) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_CREATE)
        if not title.strip():
            raise WorkRequestError("title is required")
        if not self._assignee_directory.is_work_request_assignee(principal, assignee_id):
            raise WorkRequestError("assignee is not an eligible assignee")
        cleaned_description = (description or "").strip() or None
        request, created = self._repository.create_request(
            str(principal.id),
            assignee_id,
            title.strip(),
            causation_key,
            description=cleaned_description,
            due_date=due_date,
        )
        if created:
            self._repository.append_audit(request.id, str(principal.id), "work_request.created", {})
        return self._view(request)

    def accept(self, principal: Principal, request_id: UUID, expected_version: int) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_DECIDE)
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "accepted"
        request.version += 1
        self._repository.record_decision(request, str(principal.id), "accept")
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
        self._repository.record_decision(request, str(principal.id), "reject", reason=reason.strip())
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
        self._repository.record_decision(request, str(principal.id), "negotiate", reason=str(conditions.get("note") or "") or None, conditions=conditions)
        self._repository.append_audit(
            request.id, str(principal.id), "work_request.negotiated", {"conditions": conditions}
        )
        return self._view(request)

    def resubmit(
        self,
        principal: Principal,
        request_id: UUID,
        expected_version: int,
        *,
        title: str | None = None,
        description: str | None = None,
        due_date: date | None = None,
        clear_due_date: bool = False,
    ) -> dict[str, Any]:
        """Requester revises a negotiated request: new SubjectVersion + Submission with diff; the earlier decision stays."""
        self._require(principal, WORK_REQUEST_CREATE)
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if request.requester_id != str(principal.id):
            raise WorkRequestError("only the requester may resubmit")
        if request.version != expected_version:
            raise WorkRequestError("work request version is stale")
        if request.state != "negotiating":
            raise WorkRequestError("only a negotiating work request can be resubmitted")
        if title is not None:
            if not title.strip():
                raise WorkRequestError("title is required")
            request.title = title.strip()
        if description is not None:
            request.description = description.strip() or None
        if clear_due_date:
            request.due_date = None
        elif due_date is not None:
            request.due_date = due_date
        snapshot = {
            "title": request.title,
            "description": request.description,
            "due_date": request.due_date.isoformat() if request.due_date else None,
            "assignee_id": request.assignee_id,
        }
        submission = self._repository.resubmit(request, str(principal.id), snapshot)
        request.state = "pending"
        request.conditions = None
        request.version += 1
        self._repository.append_audit(
            request.id, str(principal.id), "work_request.resubmitted", {"submission_version": submission.submission_version}
        )
        return self._view(request)

    def timeline(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        comments = (
            [self._comment_view(item) for item in self._comments.list_for(request.request_thread_id)]
            if self._comments is not None and request.request_thread_id is not None
            else []
        )
        return {"request": self._view(request), "comments": comments, **self._repository.timeline(request)}

    def add_comment(self, principal: Principal, request_id: UUID, body: str) -> dict[str, Any]:
        """Discussion only: a comment never changes the request state or counts as a decision."""
        self._require(principal, WORK_REQUEST_READ)
        if self._comments is None:
            raise WorkRequestError("comments are not available")
        request = self._participant_request(principal, request_id)
        if request.request_thread_id is None:
            raise WorkRequestError("work request has no thread")
        text = body.strip()
        if not text:
            raise WorkRequestError("comment body is required")
        return self._comment_view(self._comments.add(request.request_thread_id, str(principal.id), text))

    def _participant_request(self, principal: Principal, request_id: UUID) -> Any:
        request = self._repository.request(request_id)
        if request is None:
            raise WorkRequestError("work request was not found")
        if str(principal.id) not in {request.requester_id, request.assignee_id}:
            raise WorkRequestError("principal cannot read this work request")
        return request

    @staticmethod
    def _comment_view(comment: Any) -> dict[str, Any]:
        return {
            "comment_id": str(comment.id),
            "author_member_id": comment.author_member_id,
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
            "edited_at": comment.edited_at.isoformat() if comment.edited_at else None,
        }

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

    def _view(self, request: Any, task: Any | None = None) -> dict[str, Any]:
        submission = self._repository.current_submission(request)
        return {
            "request_id": str(request.id),
            "request_thread_id": str(request.request_thread_id) if getattr(request, "request_thread_id", None) else None,
            "submission_version": submission.submission_version if submission is not None else None,
            "title": request.title,
            "description": getattr(request, "description", None),
            "due_date": request.due_date.isoformat() if getattr(request, "due_date", None) else None,
            "requester_id": request.requester_id,
            "assignee_id": request.assignee_id,
            "state": request.state,
            "version": request.version,
            "task_id": str(task.id) if task else None,
            "assignment_state": "active" if task else None,
            "conditions": request.conditions,
        }
