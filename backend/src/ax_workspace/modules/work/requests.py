"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
import hashlib
import json
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from ax_workspace.modules.organization_access.domain import (
    Principal,
    WORK_REQUEST_CREATE,
    WORK_REQUEST_DECIDE,
    WORK_REQUEST_READ,
)
from ax_workspace.modules.work.application import clean_checklist
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionQueue, MaterialExtractionRepository
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
    def request_references(self, request_id: UUID) -> list[Any]: ...
    def source_meeting_title(self, request: Any) -> str | None: ...
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
        checklist: list[str] | None = None,
        source_meeting_id: UUID | None = None,
        source_agenda_id: UUID | None = None,
        promoted_by_member_id: str | None = None,
    ) -> tuple[Any, bool]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def cc_member_ids(self, request: Any) -> list[str]: ...
    def derived_task_ids(self, requests: list[Any]) -> dict[UUID, UUID]: ...
    def adopt_evidence(self, submission: Any, attachment: Any, *, role: str, adopted_by: str) -> Any: ...
    def amend(self, request: Any, actor_id: str, snapshot: dict[str, Any]) -> Any: ...
    def audit_payloads(self, request_id: UUID, event_type: str) -> list[dict[str, Any]]: ...
    def evidence_count_for(self, submission: Any) -> int: ...
    def evidence_for(self, request: Any) -> list[tuple[Any, Any, Any]]: ...
    def create_accepted_task(self, request: Any) -> Any: ...
    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None: ...
    def inbox_for(self, assignee_id: str) -> list[Any]: ...
    def list_for(self, principal_id: str) -> list[Any]: ...
    def record_decision(
        self, request: Any, actor_id: str, decision: str, *,
        reason: str | None = None, conditions: dict[str, Any] | None = None, expected_version: int | None = None,
    ) -> Any: ...
    def resubmit(self, request: Any, actor_id: str, snapshot: dict[str, Any]) -> Any: ...
    def withdraw(self, request: Any, actor_id: str) -> None: ...
    def current_submission(self, request: Any) -> Any: ...
    def active_assignment(self, submission: Any) -> Any: ...
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


#: 회의 승격이 만든 요청을 **보낸 쪽**. 사람이 아니라 시스템이다 (사용자 결정 D40, 2026-09-11).
#:
#: 승격은 「내가 너에게 부탁한다」가 아니라 「회의에서 이 일이 나왔다」이다. 요청자 자리에 누른 사람을
#: 앉히면 회의에서 나온 일이 그 사람의 부탁으로 읽히고, 조직 경계(누가 누구에게 요청할 수 있는가)도
#: 그 사람에게 걸린다. 그래서 보낸 쪽은 시스템이고, **누른 사람은 `promoted_by_member_id` 로 남는다.**
#: 이 글자는 `members` 에 없는 id 다 — 사람 관계 표(resource_relationships)에는 이 줄이 서지 않는다.
SYSTEM_MEETING_REQUESTER = "system:meeting"
#: 사람이 아닌 행위자의 접두. 이 글자로 시작하는 id 는 **사람 명부에 없다** — 사람으로 그리면 안 된다.
SYSTEM_ACTOR_PREFIX = "system:"
#: 요청자 표시 종류. 화면이 「회의 · {회의명}」으로 그릴지 사람 이름으로 그릴지 가르는 값이다.
REQUESTER_KIND_SYSTEM = "system"
REQUESTER_KIND_MEMBER = "member"


#: One reserved key inside a decision's conditions for everything the server froze, so it never mixes with the note
#: and structured changes a person wrote there. Callers keep reading their own conditions where they always were.
DECISION_FACTS = "_decision"
#: The facts kept under it: the version this answer consumed, and the basis it was made on.
DECISION_VERSION = "expected_version"
EVIDENCE_HASH = "evidence_hash"
EVIDENCE_MANIFEST = "evidence_manifest"


def decision_facts(conditions: Any) -> dict[str, Any]:
    """What the server froze on a decision, whatever else its conditions carry."""
    if not isinstance(conditions, dict):
        return {}
    facts = conditions.get(DECISION_FACTS)
    return dict(facts) if isinstance(facts, dict) else {}


def _amend_command(*, title: str | None, description: str | None, due_date: date | None, clear_due_date: bool) -> dict[str, Any]:
    """The amendment as it was asked for, in one canonical form so a re-send compares to what was stored.

    Only the fields the caller named are in it: naming a field and leaving it empty is an intent of its own.
    """
    command: dict[str, Any] = {}
    if title is not None:
        command["title"] = title.strip()
    if description is not None:
        command["description"] = description.strip()
    if clear_due_date:
        command["clear_due_date"] = True
    elif due_date is not None:
        command["due_date"] = due_date.isoformat()
    return command


def evidence_manifest_entry(attachment_id: Any, evidence_role: str, fixed_snapshot_ref: str) -> dict[str, str]:
    """One line of a basis. Identity is the attachment and its integrity, never the Evidence row that adopted it."""
    return {
        "attachment_id": str(attachment_id),
        "evidence_role": str(evidence_role),
        "fixed_snapshot_ref": str(fixed_snapshot_ref),
    }


def evidence_manifest(entries: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """The canonical form of a basis: a set, ordered so the same set always reads the same."""
    return sorted(
        (dict(entry) for entry in entries),
        key=lambda entry: (entry["attachment_id"], entry["evidence_role"], entry["fixed_snapshot_ref"]),
    )


def evidence_manifest_hash(entries: Iterable[dict[str, str]]) -> str:
    """The identity of a basis, independent of the order its rows were written in. An empty basis has one too."""
    canonical = json.dumps(evidence_manifest(entries), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


#: The only fields a reviewer may propose changing, which is exactly what a revision is allowed to answer with.
PROPOSABLE_FIELDS = ("title", "description", "due_date")
#: A revision may additionally drop the date entirely.
REVISABLE_FIELDS = (*PROPOSABLE_FIELDS, "clear_due_date")


def normalize_proposed_changes(value: Any) -> dict[str, str]:
    """One allow-list and one date contract for every path that writes a structured proposal.

    Silently dropping a field would let a caller believe it asked for something it did not, and would let a legacy
    endpoint store conditions the canonical reader refuses — which is how one bad write can make a whole ledger
    unreadable. So an unknown field is an error at the point of writing.
    """
    if not value:
        return {}
    if not isinstance(value, dict):
        raise WorkRequestError("변경 제안은 필드별로 적어 주세요")
    unknown = sorted(set(value) - set(PROPOSABLE_FIELDS))
    if unknown:
        raise WorkRequestError(f"변경 제안할 수 없는 항목입니다: {', '.join(unknown)}")
    proposed = {field: str(value[field]).strip() for field in PROPOSABLE_FIELDS if str(value.get(field) or "").strip()}
    if "due_date" in proposed:
        try:
            date.fromisoformat(proposed["due_date"])
        except ValueError as error:
            raise WorkRequestError("제안 기한은 YYYY-MM-DD 형식이어야 합니다") from error
    return proposed


class TaskReferenceViewPort(Protocol):
    """Reading a referenced Task through the Work module's own authorization: None when this person may not."""

    def view(self, principal: Principal, task_id: UUID) -> dict[str, Any] | None: ...


class WorkRequestApplication:
    def __init__(
        self,
        repository: WorkRequestRepository,
        assignee_directory: WorkRequestAssigneeDirectory,
        comments: CommentRepository | None = None,
        attachments: AttachmentRepository | None = None,
        storage: MaterialStorage | None = None,
        references: "TaskReferenceViewPort | None" = None,
        extractions: MaterialExtractionRepository | None = None,
        extraction_queue: MaterialExtractionQueue | None = None,
    ) -> None:
        self._repository = repository
        self._assignee_directory = assignee_directory
        self._comments = comments
        self._attachments = attachments
        self._storage = storage
        self._references = references
        self._extractions, self._extraction_queue = extractions, extraction_queue

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
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        source_meeting_id: UUID | None = None,
        source_agenda_id: UUID | None = None,
        allow_self_assignment: bool = False,
        promoted_by_member_id: str | None = None,
    ) -> dict[str, Any]:
        """업무 요청 하나. `promoted_by_member_id` 가 오면 **회의 승격**이다 (D40).

        그때 보낸 쪽은 시스템이고 누른 사람은 참조로 남는다. 시스템이 보내므로 **담당 후보의 조직 경계를
        묻지 않는다** — 회의에 누가 앉아 있었는지 따로 실어 보내던 예외(`eligible_member_ids`)가 이 규칙에
        흡수됐다. 사람이 보내는 기존 경로는 한 글자도 달라지지 않는다.
        """
        self._require(principal, WORK_REQUEST_CREATE)
        if not title.strip():
            raise WorkRequestError("title is required")
        promoted = promoted_by_member_id is not None
        requester_id = SYSTEM_MEETING_REQUESTER if promoted else str(principal.id)
        # 회의에서 나온 일을 자기가 맡겠다고 고르는 것은 승격의 정상 경로다 — 갈래를 두지 않으므로 그때도 요청이다
        # (SCAX-SPEC-004 §9-5 「누르는 사람 자신이어도 된다」). 그 밖의 자리에서는 자기 자신이 후보가 아니다.
        oneself = allow_self_assignment and assignee_id == str(principal.id)
        if promoted:
            # 조직 경계는 **보내는 사람**에게 걸리는 규칙이다. 보낸 쪽이 시스템이면 걸 자리가 없다 (D40).
            # 그래도 아무 글자나 담당이 되지는 않는다 — 지금 일하고 있는 사람인지는 그대로 본다.
            if not oneself and not self._assignee_directory.is_active_member(principal, assignee_id):
                raise WorkRequestError("assignee is not an eligible assignee")
        elif not oneself and not self._assignee_directory.is_work_request_assignee(principal, assignee_id):
            raise WorkRequestError("assignee is not an eligible assignee")
        cc: list[str] = []
        for member_id in cc_member_ids or []:
            if member_id in {requester_id, str(principal.id), assignee_id} or member_id in cc:
                continue
            if not self._assignee_directory.is_active_member(principal, member_id):
                raise WorkRequestError(f"cc member {member_id} is not an active member")
            cc.append(member_id)
        # 누른 사람은 cc 로 들어간다 — 시스템이 보낸 요청이라도 **그 사람은 자기가 만든 것을 읽어야 한다** (D40).
        # 이 한 사람에게는 「일하고 있는 사람인가」를 묻지 않는다: 지금 이 요청을 만들고 있는 당사자이고,
        # 후보 명부는 본인을 빼고 답하므로 물으면 언제나 아니라고 한다.
        if promoted and promoted_by_member_id not in {assignee_id, *cc}:
            cc.append(str(promoted_by_member_id))
        cleaned_description = (description or "").strip() or None
        request, created = self._repository.create_request(
            requester_id,
            assignee_id,
            title.strip(),
            causation_key,
            description=cleaned_description,
            due_date=due_date,
            cc_member_ids=cc,
            # The steps travel with the request and become the accepted Task's own checklist.
            checklist=clean_checklist(checklist),
            # So does the earlier work pointed at — but only work this person may actually read right now.
            reference_task_ids=self._readable_references(principal, reference_task_ids),
            # 회의에서 넘어온 요청이면 출처 두 id 가 함께 간다 (SCAX-SPEC-004 §9-5).
            source_meeting_id=source_meeting_id,
            source_agenda_id=source_agenda_id,
            promoted_by_member_id=promoted_by_member_id,
        )
        if created:
            self._repository.append_audit(request.id, str(principal.id), "work_request.created", {})
        return self._view(request)

    def accept(self, principal: Principal, request_id: UUID, expected_version: int) -> dict[str, Any]:
        self._require(principal, WORK_REQUEST_DECIDE)
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "accepted"
        request.version += 1
        self._repository.record_decision(request, str(principal.id), "accept", expected_version=expected_version)
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
        self._repository.record_decision(request, str(principal.id), "reject", reason=reason.strip(), expected_version=expected_version)
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
        # Whatever wrote this — the canonical command or the compatibility endpoint — the ledger must stay readable.
        normalize_proposed_changes(conditions.get("changes"))
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "negotiating"
        request.conditions = conditions
        request.version += 1
        self._repository.record_decision(
            request, str(principal.id), "negotiate",
            reason=str(conditions.get("note") or "") or None, conditions=conditions, expected_version=expected_version,
        )
        self._repository.append_audit(
            request.id, str(principal.id), "work_request.negotiated", {"conditions": conditions}
        )
        return self._view(request)

    def amend(
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
        """The requester improving their own request before anyone has judged it.

        Nobody asked for this, so it is not a ReviewDecision and never puts the question back on the requester: it adds
        a round to the same WorkRequest and replaces what the assignee is looking at.
        """
        self._require(principal, WORK_REQUEST_CREATE)
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if not self._is_requester(principal, request):
            raise WorkRequestAccessDenied("only the requester may amend their own request")
        if request.state != "pending":
            raise WorkRequestError("담당자가 판단하고 있는 요청만 수정할 수 있습니다")
        command = _amend_command(title=title, description=description, due_date=due_date, clear_due_date=clear_due_date)
        if request.version != expected_version:
            # The same amendment coming back is that answer, not a stale one. This is judged on the command itself:
            # once it has been applied the request no longer differs from it, so asking whether it still changes
            # anything would refuse the very re-send it is meant to recognise.
            if self._amendment_produced(request, str(principal.id), expected_version, command):
                return self._view(request)
            raise WorkRequestError("work request version is stale")
        revised = self._revised_content(request, title=title, description=description, due_date=due_date, clear_due_date=clear_due_date)
        submission = self._apply_round(request, str(principal.id), revised, self._repository.amend)
        self._repository.append_audit(
            request.id,
            str(principal.id),
            "work_request.amended",
            {
                "submission_version": submission.submission_version,
                "submission_id": str(submission.id),
                "expected_version": expected_version,
                "command": command,
            },
        )
        self._record_inheritance(request, str(principal.id), submission)
        return self._view(request)

    def _amendment_produced(self, request: Any, actor_id: str, expected_version: int, command: dict[str, Any]) -> bool:
        """Did this exact command already produce the round that now stands?"""
        current = self._repository.current_submission(request)
        if current is None or current.submitted_by != actor_id:
            return False
        return any(
            entry.get("submission_id") == str(current.id)
            and entry.get("expected_version") == expected_version
            and entry.get("command") == command
            for entry in self._repository.audit_payloads(request.id, "work_request.amended")
        )

    def _revised_content(
        self, request: Any, *, title: str | None, description: str | None, due_date: date | None, clear_due_date: bool
    ) -> dict[str, Any]:
        """What the request would become, refused when it names a field it cannot change or changes nothing."""
        if title is not None and not title.strip():
            raise WorkRequestError("title is required")
        revised = {
            "title": title.strip() if title is not None else request.title,
            "description": (description.strip() or None) if description is not None else request.description,
            "due_date": None if clear_due_date else (due_date if due_date is not None else request.due_date),
        }
        if revised == {"title": request.title, "description": request.description, "due_date": request.due_date}:
            raise WorkRequestError("a revision must change something")
        return revised

    def _apply_round(self, request: Any, actor_id: str, revised: dict[str, Any], open_round: Any) -> Any:
        request.title = revised["title"]
        request.description = revised["description"]
        request.due_date = revised["due_date"]
        snapshot = {
            "title": request.title,
            "description": request.description,
            "due_date": request.due_date.isoformat() if request.due_date else None,
            "assignee_id": request.assignee_id,
        }
        submission = open_round(request, actor_id, snapshot)
        request.version += 1
        return submission

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
        if not self._is_requester(principal, request):
            raise WorkRequestError("only the requester may resubmit")
        if request.version != expected_version:
            raise WorkRequestError("work request version is stale")
        if request.state != "negotiating":
            raise WorkRequestError("only a negotiating work request can be resubmitted")
        # Compare against the round being revised before touching it: a field repeated at its current value is not a
        # change, however the caller wrote it, and an empty round would give the reviewer nothing to answer.
        revised = self._revised_content(request, title=title, description=description, due_date=due_date, clear_due_date=clear_due_date)
        submission = self._apply_round(request, str(principal.id), revised, self._repository.resubmit)
        request.state = "pending"
        request.conditions = None
        self._repository.append_audit(
            request.id, str(principal.id), "work_request.resubmitted", {"submission_version": submission.submission_version}
        )
        self._record_inheritance(request, str(principal.id), submission)
        return self._view(request)

    def _record_inheritance(self, request: Any, actor_id: str, submission: Any) -> None:
        """The basis a new round carried forward, recorded after the act that carried it."""
        inherited = self._repository.evidence_count_for(submission)
        if not inherited:
            return
        self._repository.append_audit(
            request.id,
            actor_id,
            "work_request.evidence_inherited",
            {
                "inherited_count": inherited,
                "previous_submission_id": str(submission.revises_id),
                "new_submission_id": str(submission.id),
            },
        )

    def withdraw(self, principal: Principal, request_id: UUID, expected_version: int) -> dict[str, Any]:
        """The requester retracts their own request; no Task is created and the question leaves every ledger."""
        self._require(principal, WORK_REQUEST_CREATE)
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if not self._is_requester(principal, request):
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
        self._request_extraction(attachment)
        return self._comment_view(comment, [_attachment_view(item) for _, item in self._attachments.bindings_for("comment", str(comment.id))])

    def add_evidence(self, principal: Principal, request_id: UUID, *, name: str, content_type: str, data: bytes) -> dict[str, Any]:
        """Adopt a file as Evidence for the current Submission: the reviewer adopts decision basis, the requester supplies support."""
        self._require(principal, WORK_REQUEST_READ)
        if self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        if not self._is_requester(principal, request) and str(principal.id) != request.assignee_id:
            raise WorkRequestError("only the requester or the assignee may adopt evidence")
        submission = self._repository.current_submission(request)
        if submission is None:
            raise WorkRequestError("work request has no submission to attach evidence to")
        if self._repository.active_assignment(submission) is None:
            # The basis of a round a person already judged is history. A new round is how the basis grows.
            raise WorkRequestError("이 회차는 이미 판단이 끝났습니다. 수정안을 재상신한 뒤 새 회차에 근거를 추가하세요")
        attachment = store_file(
            self._attachments, self._storage,
            key_prefix=f"work_requests/{request.id}/evidence", name=name, content_type=content_type, data=data,
            provenance=f"work_request:{request.id}:submission:{submission.id}", uploaded_by=str(principal.id),
        )
        self._attachments.bind(attachment_id=attachment.id, context_type="submission", context_id=str(submission.id), role="supplemental", bound_by=str(principal.id))
        role = "decision_basis" if str(principal.id) == request.assignee_id else "supporting"
        evidence = self._repository.adopt_evidence(submission, attachment, role=role, adopted_by=str(principal.id))
        self._request_extraction(attachment)
        # The basis a reviewer is looking at just changed, so the version they opened must no longer answer it.
        request.version += 1
        request.updated_at = datetime.now(UTC)
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
            "request_version": int(request.version),
            "adopted_by": evidence.adopted_by,
            "adopted_at": evidence.adopted_at.isoformat(),
        }

    def _request_extraction(self, attachment: Any) -> None:
        if self._extractions is not None:
            extraction = self._extractions.request(attachment)
            if extraction.status == "queued" and self._extraction_queue is not None:
                self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id))

    def material_bindings(self, principal: Principal, request_id: UUID) -> list[tuple[Any, Any]]:
        """Live discussion bindings and adopted, immutable submission files for a current participant."""
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        if self._attachments is None:
            return []
        bindings = []
        if self._comments is not None and request.request_thread_id is not None:
            comments = self._comments.list_for(request.request_thread_id)
            bindings.extend(self._attachments.bindings_for_many("comment", [str(comment.id) for comment in comments]))
        adoptions = self._repository.evidence_for(request)
        adopted = {(str(submission.id), attachment.id, evidence.fixed_snapshot_ref)
                   for evidence, attachment, submission in adoptions if not evidence.mutable_source}
        for binding, attachment in self._attachments.bindings_for_many("submission", sorted({item[0] for item in adopted})):
            if (binding.context_id, attachment.id, attachment.integrity_ref) in adopted:
                bindings.append((binding, attachment))
        return [(binding, attachment) for binding, attachment in bindings
                if binding.unbound_at is None and attachment.lifecycle != "purged"]

    def open_attachment(self, principal: Principal, request_id: UUID, attachment_id: UUID) -> tuple[dict[str, Any], bytes]:
        """Any participant (requester, assignee, cc) may read files that belong to this request's thread."""
        self._require(principal, WORK_REQUEST_READ)
        if self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        attachment = next((attachment for _, attachment in self.material_bindings(principal, request_id)
                           if attachment.id == attachment_id), None)
        if attachment is None:
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

    @staticmethod
    def _is_requester(principal: Principal, request: Any) -> bool:
        """이 사람이 **요청자 자리에 선 사람**인가 — 요청자 전용 조작의 유일한 판정이다.

        회의 승격이면 요청자는 시스템이고 누른 사람이 그 자리를 대신 선다 (D40): 그 사람이 만든 요청이므로
        고치고 거두는 것도 그 사람이다. 시스템은 로그인하지 않으므로 이 판정을 양보하지 않으면
        승격된 요청은 **아무도 수정할 수 없는 요청**이 된다.
        """
        member_id = str(principal.id)
        return member_id == request.requester_id or member_id == getattr(request, "promoted_by_member_id", None)

    def _is_participant(self, principal: Principal, request: Any) -> bool:
        member_id = str(principal.id)
        return (
            self._is_requester(principal, request)
            or member_id == request.assignee_id
            or member_id in self._repository.cc_member_ids(request)
        )

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
        return {
            **self._view(request, task_id=self._repository.derived_task_ids([request]).get(request.id)),
            "references": self._references_view(principal, request),
            # 「회의 · {회의명}」으로 그릴 이름. 회의에서 오지 않은 요청은 `null` 이다 (D40).
            # 상세에서만 읽는다 — 목록 한 줄마다 회의를 찾아가는 값이 아니다.
            "source_meeting_title": self._repository.source_meeting_title(request),
        }

    def _readable_references(self, principal: Principal, task_ids: list[UUID] | None) -> list[UUID]:
        """You may point at work you can open. Anything else is refused rather than silently dropped."""
        wanted: list[UUID] = []
        for task_id in task_ids or []:
            identifier = task_id if isinstance(task_id, UUID) else UUID(str(task_id))
            if identifier in wanted:
                continue
            if self._references is None or self._references.view(principal, identifier) is None:
                raise WorkRequestError("참고 업무로 연결할 수 없는 업무입니다")
            wanted.append(identifier)
        return wanted

    def _references_view(self, principal: Principal, request: Any) -> list[dict[str, Any]]:
        """Earlier work the requester pointed at, each resolved now through that work's own authorization.

        Being copied into a request is not permission to read what it points at: someone who may not open the
        referenced work sees that a pointer exists and nothing about the work itself.
        """
        rows = self._repository.request_references(request.id)
        return [
            {
                "reference_id": str(row.id),
                "created_by": row.created_by,
                "task": self._references.view(principal, row.referenced_task_id) if self._references else None,
            }
            for row in rows
        ]

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
            "checklist": list(getattr(request, "initial_checklist", None) or []),
            "requester_id": request.requester_id,
            # 회의에서 온 요청이면 그 회의. 관계 그래프가 요청을 회의에 잇는 근거다 (D40 미결 ④).
            "source_meeting_id": str(request.source_meeting_id) if getattr(request, "source_meeting_id", None) else None,
            # 요청자를 화면이 어떻게 그릴지 — 사람 이름인가, 「회의 · {회의명}」인가 (D40).
            "requester_kind": (
                REQUESTER_KIND_SYSTEM if request.requester_id == SYSTEM_MEETING_REQUESTER else REQUESTER_KIND_MEMBER
            ),
            "promoted_by_member_id": getattr(request, "promoted_by_member_id", None),
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
