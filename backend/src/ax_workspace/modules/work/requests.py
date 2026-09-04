"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from ax_workspace.modules.organization_access.domain import (
    Principal,
    WORK_REQUEST_CREATE,
    WORK_REQUEST_DECIDE,
    WORK_REQUEST_READ,
)
from ax_workspace.modules.work.materials import AttachmentRepository, MaterialNotFound, MaterialStorage, store_file


def comment_identity(request_thread_id: UUID, author_id: str, idempotency_key: str) -> UUID:
    """Deterministic Comment id for one logical submit, so a duplicate POST lands on the same primary key."""
    return uuid5(NAMESPACE_URL, f"scax:work-request-comment:{request_thread_id}:{author_id}:{idempotency_key}")


class WorkRequestError(Exception):
    pass


class WorkRequestIdempotencyConflict(WorkRequestError):
    """A comment idempotency key was reused with different content."""


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
        cc_member_ids: list[str] | None = None,
    ) -> tuple[Any, bool]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def cc_member_ids(self, request: Any) -> list[str]: ...
    def derived_task_ids(self, requests: list[Any]) -> dict[UUID, UUID]: ...
    def adopt_evidence(self, submission: Any, attachment: Any, *, role: str, adopted_by: str) -> Any: ...
    def evidence_for(self, request: Any) -> list[tuple[Any, Any, Any]]: ...
    def create_accepted_task(self, request: Any) -> Any: ...
    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None: ...
    def inbox_for(self, assignee_id: str) -> list[Any]: ...
    def list_for(self, principal_id: str) -> list[Any]: ...
    def record_decision(self, request: Any, actor_id: str, decision: str, *, reason: str | None = None, conditions: dict[str, Any] | None = None) -> Any: ...
    def resubmit(self, request: Any, actor_id: str, snapshot: dict[str, Any]) -> Any: ...
    def withdraw(self, request: Any, actor_id: str) -> None: ...
    def current_submission(self, request: Any) -> Any: ...
    def timeline(self, request: Any) -> dict[str, Any]: ...


class CommentRepository(Protocol):
    def add(self, request_thread_id: UUID, author_id: str, body: str, *, comment_id: UUID | None = None) -> Any: ...
    def list_for(self, request_thread_id: UUID) -> list[Any]: ...
    def comment(self, request_thread_id: UUID, comment_id: UUID) -> Any: ...
    def lock_thread(self, request_thread_id: UUID) -> None: ...


class WorkRequestAssigneeDirectory(Protocol):
    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool: ...
    def member_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_active_member(self, principal: Principal, member_id: str) -> bool: ...


class WorkRequestApplication:
    def __init__(
        self,
        repository: WorkRequestRepository,
        assignee_directory: WorkRequestAssigneeDirectory,
        comments: CommentRepository | None = None,
        attachments: AttachmentRepository | None = None,
        storage: MaterialStorage | None = None,
    ) -> None:
        self._repository = repository
        self._assignee_directory = assignee_directory
        self._comments = comments
        self._attachments = attachments
        self._storage = storage

    def create(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        due_date: date | None = None,
        cc_member_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_CREATE)
        if not title.strip():
            raise WorkRequestError("title is required")
        if not self._assignee_directory.is_work_request_assignee(principal, assignee_id):
            raise WorkRequestError("assignee is not an eligible assignee")
        cc: list[str] = []
        for member_id in cc_member_ids or []:
            if member_id in {str(principal.id), assignee_id} or member_id in cc:
                continue
            if not self._assignee_directory.is_active_member(principal, member_id):
                raise WorkRequestError(f"cc member {member_id} is not an active member")
            cc.append(member_id)
        cleaned_description = (description or "").strip() or None
        request, created = self._repository.create_request(
            str(principal.id),
            assignee_id,
            title.strip(),
            causation_key,
            description=cleaned_description,
            due_date=due_date,
            cc_member_ids=cc,
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

    def withdraw(self, principal: Principal, request_id: UUID, expected_version: int) -> dict[str, Any]:
        """The requester retracts their own request; no Task is created and the question leaves every ledger."""
        self._require(principal, WORK_REQUEST_CREATE)
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if request.requester_id != str(principal.id):
            raise WorkRequestError("only the requester may withdraw")
        if request.version != expected_version:
            raise WorkRequestError("work request version is stale")
        if request.state not in {"pending", "negotiating"}:
            raise WorkRequestError("only an open work request can be withdrawn")
        request.state = "withdrawn"
        request.conditions = None
        request.version += 1
        self._repository.withdraw(request, str(principal.id))
        self._repository.append_audit(request.id, str(principal.id), "work_request.withdrawn", {})
        return self._view(request)

    def timeline(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        comments = self._comment_views(request)
        evidence = [
            {
                "evidence_id": str(item.id),
                "submission_id": str(submission.id),
                "submission_version": submission.submission_version,
                "attachment_id": str(attachment.id),
                "name": attachment.name,
                "content_type": attachment.content_type,
                "size_bytes": int(attachment.size_bytes),
                "evidence_role": item.evidence_role,
                "fixed_snapshot_ref": item.fixed_snapshot_ref,
                "adopted_by": item.adopted_by,
                "adopted_at": item.adopted_at.isoformat(),
            }
            for item, attachment, submission in self._repository.evidence_for(request)
        ]
        return {"request": self._view(request), "comments": comments, "evidence": evidence, **self._repository.timeline(request)}

    def discussion(self, principal: Principal, request_id: UUID) -> list[dict[str, Any]]:
        """The comment thread on one request, for a participant. Reading or writing it never moves the judgement."""
        self._require(principal, WORK_REQUEST_READ)
        return self._comment_views(self._participant_request(principal, request_id))

    def _comment_views(self, request: Any) -> list[dict[str, Any]]:
        if self._comments is None or request.request_thread_id is None:
            return []
        comments = self._comments.list_for(request.request_thread_id)
        bound: dict[str, list[dict[str, Any]]] = {}
        if self._attachments is not None and comments:
            for binding, attachment in self._attachments.bindings_for_many("comment", [str(item.id) for item in comments]):
                bound.setdefault(binding.context_id, []).append(_attachment_view(attachment))
        return [self._comment_view(item, bound.get(str(item.id), [])) for item in comments]

    def attach_to_comment(self, principal: Principal, request_id: UUID, comment_id: UUID, *, name: str, content_type: str, data: bytes) -> dict[str, Any]:
        """Bind a file to one's own comment (ERD ATTACHMENT_BINDING context=comment, role=discussion)."""
        self._require(principal, WORK_REQUEST_READ)
        if self._comments is None or self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        request = self._participant_request(principal, request_id)
        comment = self._comments.comment(request.request_thread_id, comment_id) if request.request_thread_id else None
        if comment is None:
            raise WorkRequestError("comment was not found")
        if comment.author_member_id != str(principal.id):
            raise WorkRequestError("only the comment author may attach files")
        attachment = store_file(
            self._attachments, self._storage,
            key_prefix=f"work_requests/{request.id}/comments", name=name, content_type=content_type, data=data,
            provenance=f"work_request:{request.id}:comment:{comment.id}", uploaded_by=str(principal.id),
        )
        self._attachments.bind(attachment_id=attachment.id, context_type="comment", context_id=str(comment.id), role="discussion", bound_by=str(principal.id))
        return self._comment_view(comment, [_attachment_view(item) for _, item in self._attachments.bindings_for("comment", str(comment.id))])

    def add_evidence(self, principal: Principal, request_id: UUID, *, name: str, content_type: str, data: bytes) -> dict[str, Any]:
        """Adopt a file as Evidence for the current Submission: the reviewer adopts decision basis, the requester supplies support."""
        self._require(principal, WORK_REQUEST_READ)
        if self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if str(principal.id) not in {request.requester_id, request.assignee_id}:
            raise WorkRequestError("only the requester or the assignee may adopt evidence")
        submission = self._repository.current_submission(request)
        if submission is None:
            raise WorkRequestError("work request has no submission to attach evidence to")
        attachment = store_file(
            self._attachments, self._storage,
            key_prefix=f"work_requests/{request.id}/evidence", name=name, content_type=content_type, data=data,
            provenance=f"work_request:{request.id}:submission:{submission.id}", uploaded_by=str(principal.id),
        )
        self._attachments.bind(attachment_id=attachment.id, context_type="submission", context_id=str(submission.id), role="supplemental", bound_by=str(principal.id))
        role = "decision_basis" if str(principal.id) == request.assignee_id else "supporting"
        evidence = self._repository.adopt_evidence(submission, attachment, role=role, adopted_by=str(principal.id))
        self._repository.append_audit(request.id, str(principal.id), "work_request.evidence_adopted", {"evidence_id": str(evidence.id), "name": attachment.name})
        return {
            "evidence_id": str(evidence.id),
            "submission_id": str(submission.id),
            "submission_version": submission.submission_version,
            "attachment_id": str(attachment.id),
            "name": attachment.name,
            "content_type": attachment.content_type,
            "size_bytes": int(attachment.size_bytes),
            "evidence_role": evidence.evidence_role,
            "fixed_snapshot_ref": evidence.fixed_snapshot_ref,
            "adopted_by": evidence.adopted_by,
            "adopted_at": evidence.adopted_at.isoformat(),
        }

    def open_attachment(self, principal: Principal, request_id: UUID, attachment_id: UUID) -> tuple[dict[str, Any], bytes]:
        """Any participant (requester, assignee, cc) may read files that belong to this request's thread."""
        self._require(principal, WORK_REQUEST_READ)
        if self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        request = self._participant_request(principal, request_id)
        attachment = self._attachments.attachment(attachment_id)
        if attachment is None or not str(attachment.provenance).startswith(f"work_request:{request.id}:"):
            raise MaterialNotFound("attachment was not found")
        return _attachment_view(attachment), self._storage.get(attachment.source_ref)

    def add_comment(self, principal: Principal, request_id: UUID, body: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Discussion only: a comment never changes the request state or counts as a decision.

        With an Idempotency-Key the Comment id is derived from thread + author + key, so a retried or double-submitted
        post resolves to the same row instead of a second comment. Concurrent posts of the same key are serialized on
        the RequestThread, and reusing a key for different text is a conflict rather than a silent overwrite.
        """
        self._require(principal, WORK_REQUEST_READ)
        if self._comments is None:
            raise WorkRequestError("comments are not available")
        request = self._participant_request(principal, request_id)
        if request.request_thread_id is None:
            raise WorkRequestError("work request has no thread")
        text = body.strip()
        if not text:
            raise WorkRequestError("comment body is required")
        if not idempotency_key:
            return self._comment_view(self._comments.add(request.request_thread_id, str(principal.id), text), [])
        comment_id = comment_identity(request.request_thread_id, str(principal.id), idempotency_key)
        # Serialize same-key posts that arrive together; the second one then sees the first.
        self._comments.lock_thread(request.request_thread_id)
        existing = self._comments.comment(request.request_thread_id, comment_id)
        if existing is not None:
            if existing.body != text:
                raise WorkRequestIdempotencyConflict("comment idempotency key was reused with different content")
            return self._comment_view(existing, self._comment_attachments(existing))
        return self._comment_view(self._comments.add(request.request_thread_id, str(principal.id), text, comment_id=comment_id), [])

    def _comment_attachments(self, comment: Any) -> list[dict[str, Any]]:
        if self._attachments is None:
            return []
        return [_attachment_view(attachment) for _, attachment in self._attachments.bindings_for_many("comment", [str(comment.id)])]

    def _participant_request(self, principal: Principal, request_id: UUID) -> Any:
        request = self._repository.request(request_id)
        if request is None:
            raise WorkRequestError("work request was not found")
        if not self._is_participant(principal, request):
            raise WorkRequestError("principal cannot read this work request")
        return request

    def _is_participant(self, principal: Principal, request: Any) -> bool:
        member_id = str(principal.id)
        return member_id in {request.requester_id, request.assignee_id} or member_id in self._repository.cc_member_ids(request)

    @staticmethod
    def _comment_view(comment: Any, attachments: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "comment_id": str(comment.id),
            "author_member_id": comment.author_member_id,
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
            "edited_at": comment.edited_at.isoformat() if comment.edited_at else None,
            "attachments": attachments,
        }

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, WORK_REQUEST_DECIDE)
        return [self._view(request) for request in self._repository.inbox_for(str(principal.id))]

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, WORK_REQUEST_READ)
        requests = self._repository.list_for(str(principal.id))
        derived = self._repository.derived_task_ids(requests)
        return [self._view(request, task_id=derived.get(request.id)) for request in requests]

    def get(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        return self._view(request, task_id=self._repository.derived_task_ids([request]).get(request.id))

    def assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        self._require(principal, WORK_REQUEST_CREATE)
        return self._assignee_directory.work_request_assignee_candidates(principal)

    def cc_candidates(self, principal: Principal) -> list[dict[str, str]]:
        self._require(principal, WORK_REQUEST_CREATE)
        return self._assignee_directory.member_candidates(principal)

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

    def _view(self, request: Any, task: Any | None = None, *, task_id: Any | None = None) -> dict[str, Any]:
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
            "cc_member_ids": self._repository.cc_member_ids(request),
            "state": request.state,
            "version": request.version,
            "task_id": str(task.id) if task else (str(task_id) if task_id else None),
            "assignment_state": "active" if task or task_id else None,
            "conditions": request.conditions,
        }


def _attachment_view(attachment: Any) -> dict[str, Any]:
    return {
        "attachment_id": str(attachment.id),
        "name": attachment.name,
        "content_type": attachment.content_type,
        "size_bytes": int(attachment.size_bytes),
        "uploaded_by": attachment.uploaded_by,
        "created_at": attachment.created_at.isoformat(),
    }
