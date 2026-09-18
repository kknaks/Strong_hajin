"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from ax_workspace.modules.work.request_results import WorkRequestDetailResult, WorkRequestEvidenceResult
from ax_workspace.modules.actions.results import ActionDiscussionView

from ax_workspace.modules.work.request_results import WorkRequestMutationResult
from ax_workspace.modules.work.request_commands import WorkRequestVersionInput, WorkRequestRejectInput, WorkRequestNegotiationInput, WorkRequestCommentInput


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
from ax_workspace.modules.work.request_errors import (
    WorkRejectReasonRequired,
    WorkRequestAccessDenied,
    WorkRequestError,
    WorkRequestIdempotencyConflict,
    WorkRequestLockedAfterAccept,
    WorkRequestNotFound,
    WorkRequestNotPending,
    WorkRequestResponderOnly,
)
from ax_workspace.modules.work.request_lifecycle import (
    RequestCreationContext,
    ReviseWorkRequest,
    SYSTEM_MEETING_REQUESTER,
    WithdrawWorkRequest,
    WorkRequest,
    WorkRequestRevision,
    decide_request_creation,
    revise_work_request,
    withdraw_work_request,
)
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionQueue, MaterialExtractionRepository
from ax_workspace.modules.work.materials import AttachmentRepository, MaterialNotFound, MaterialStorage, store_file


def comment_identity(request_thread_id: UUID, author_id: str, idempotency_key: str) -> UUID:
    """Deterministic Comment id for one logical submit, so a duplicate POST lands on the same primary key."""
    return uuid5(NAMESPACE_URL, f"scax:work-request-comment:{request_thread_id}:{author_id}:{idempotency_key}")


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
        parent_task_id: UUID | None = None,
        supersedes_request_id: UUID | None = None,
    ) -> tuple[Any, bool]: ...
    def request(self, request_id: UUID, *, lock: bool = False) -> Any: ...
    def cc_member_ids(self, request: Any) -> list[str]: ...
    def derived_task_ids(self, requests: list[Any]) -> dict[UUID, UUID]: ...
    def adopt_evidence(self, submission: Any, attachment: Any, *, role: str, adopted_by: str) -> Any: ...
    def amend(self, request: Any, actor_id: str, snapshot: dict[str, Any]) -> Any: ...
    def audit_payloads(self, request_id: UUID, event_type: str) -> list[dict[str, Any]]: ...
    def evidence_count_for(self, submission: Any) -> int: ...
    def evidence_for(self, request: Any) -> list[tuple[Any, Any, Any]]: ...
    def task_for_request(self, request: Any, *, lock: bool = False) -> Any: ...
    def accept_request_assignment(self, request: Any, actor_id: str) -> Any: ...
    def close_request_task(self, request: Any, actor_id: str, *, cancel_reason: str, summary: str) -> Any: ...
    def create_task_for_request(
        self, request: Any, *, actor_id: str,
        source_action_item_id: UUID | None = None, source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None, source_review_decision_id: UUID | None = None,
    ) -> Any: ...
    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict[str, Any]) -> None: ...
    def inbox_for(self, assignee_id: str) -> list[Any]: ...
    def list_for(self, principal_id: str) -> list[Any]: ...
    def record_decision(
        self, request: Any, actor_id: str, decision: str, *,
        reason: str | None = None, conditions: dict[str, Any] | None = None, expected_version: int | None = None,
    ) -> Any: ...
    def resubmit(self, request: Any, actor_id: str, snapshot: dict[str, Any]) -> Any: ...
    def withdraw(self, request: Any, actor_id: str) -> None: ...
    def hidden_request_ids(self, member_id: str) -> set[UUID]: ...
    def remove_list_entry(self, request: Any, member_id: str) -> None: ...
    def request_decisions(self, request: Any) -> list[Any]: ...
    def current_submission(self, request: Any) -> Any: ...
    def active_assignment(self, submission: Any) -> Any: ...
    def timeline(self, request: Any) -> dict[str, Any]: ...


class CommentRepository(Protocol):
    def add(self, request_thread_id: UUID, author_id: str, body: str, *, comment_id: UUID | None = None) -> Any: ...
    def list_for(self, request_thread_id: UUID) -> list[Any]: ...
    def comment(self, request_thread_id: UUID, comment_id: UUID) -> Any: ...
    def lock_thread(self, request_thread_id: UUID) -> None: ...


class RequestParentPort(Protocol):
    """상위 업무를 붙여도 되는지 묻는 자리 — 판정은 업무 모듈이 갖는다 (SPEC-003 §4 Validation)."""

    def parent_for(self, principal: Principal, parent_task_id: UUID | None, *, assignee_id: str | None = None) -> Any: ...


class WorkRequestAssigneeDirectory(Protocol):
    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool: ...
    def member_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_active_member(self, principal: Principal, member_id: str) -> bool: ...


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
        parents: "RequestParentPort | None" = None,
    ) -> None:
        self._repository = repository
        # 상위를 붙여도 되는지는 **업무 모듈이 판정한다** — 중심 업무·순환·수락 전 부모 금지가 거기 한 곳에
        # 있고, 요청이 그 규칙을 다시 쓰면 두 벌이 조용히 갈린다.
        self._parents = parents
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
        parent_task_id: UUID | None = None,
        supersedes_request_id: UUID | None = None,
        allow_self_assignment: bool = False,
        promoted_by_member_id: str | None = None,
        source_action_item_id: UUID | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> dict[str, Any]:
        """업무 요청 하나. `promoted_by_member_id` 가 오면 **회의 승격**이다 (D40).

        그때 보낸 쪽은 시스템이고 누른 사람은 참조로 남는다. 시스템이 보내므로 **담당 후보의 조직 경계를
        묻지 않는다** — 회의에 누가 앉아 있었는지 따로 실어 보내던 예외(`eligible_member_ids`)가 이 규칙에
        흡수됐다. 사람이 보내는 기존 경로는 한 글자도 달라지지 않는다.

        `source_*` 넷은 **AX 확인이 이 요청을 있게 했을 때의 계보**다. 확정된 action 과 그 확인 회차를
        업무가 계속 가리켜야 하므로 요청에서 업무로 그대로 흘려보낸다 — 조용히 버리지 않는다.
        """
        self._require(principal, WORK_REQUEST_CREATE)
        decision = decide_request_creation(
            RequestCreationContext(
                actor_id=str(principal.id),
                title=title,
                description=description,
                assignee_id=assignee_id,
                cc_member_ids=tuple(cc_member_ids or ()),
                allow_self_assignment=allow_self_assignment,
                promoted_by_member_id=promoted_by_member_id,
            )
        )
        if decision.assignee_validation == "active":
            if not self._assignee_directory.is_active_member(principal, assignee_id):
                raise WorkRequestError("assignee is not an eligible assignee")
        elif decision.assignee_validation == "scoped":
            if not self._assignee_directory.is_work_request_assignee(principal, assignee_id):
                raise WorkRequestError("assignee is not an eligible assignee")
        for member_id in decision.active_member_checks:
            if not self._assignee_directory.is_active_member(principal, member_id):
                raise WorkRequestError(f"cc member {member_id} is not an active member")
        # **상위는 발송 단계에서 정해진다** (정책 V-9). 받는 사람이 그 하위를 들 사람이므로 중심 업무
        # 판정에 그 값을 넘긴다 — 수락 전 부모·순환·직접 작업 중첩이 여기서 걸린다.
        parent = (
            self._parents.parent_for(principal, parent_task_id, assignee_id=assignee_id)
            if parent_task_id is not None and self._parents is not None
            else None
        )
        if supersedes_request_id is not None:
            # 재요청은 **새 요청·새 Task** 다 (정책 V-12). 이 값은 두 건을 잇기만 하고 옛것을 되살리지 않는다.
            previous = self._repository.request(supersedes_request_id)
            if previous is None or str(principal.id) not in {
                str(previous.requester_id),
                str(getattr(previous, "promoted_by_member_id", None) or ""),
            }:
                raise WorkRequestNotFound("work request was not found")
        request, created = self._repository.create_request(
            decision.requester_id,
            assignee_id,
            decision.title,
            causation_key,
            description=decision.description,
            due_date=due_date,
            cc_member_ids=list(decision.cc_member_ids),
            # The steps travel with the request and become the accepted Task's own checklist.
            checklist=checklist,
            # So does the earlier work pointed at — but only work this person may actually read right now.
            reference_task_ids=self._readable_references(principal, reference_task_ids),
            # 회의에서 넘어온 요청이면 출처 두 id 가 함께 간다 (SCAX-SPEC-004 §9-5).
            source_meeting_id=source_meeting_id,
            source_agenda_id=source_agenda_id,
            promoted_by_member_id=promoted_by_member_id,
            parent_task_id=parent.id if parent is not None else None,
            supersedes_request_id=supersedes_request_id,
        )
        if not created:
            return self._view(request, task_id=self._repository.derived_task_ids([request]).get(request.id))
        self._repository.append_audit(request.id, str(principal.id), "work_request.created", {})
        # **수락 없이** 업무와 활성 담당이 같은 transaction 에 선다 (WORK-001 Phase 4). 요청 행은 그대로
        # 남아 출처가 되고, 업무가 `source_work_request_id` 로 그것을 가리킨다 — 완료 승인의 확인자가
        # 거기서 나온다. 사람이 하지 않은 수락을 판단 회차로 기록하지 않는다.
        # 업무를 있게 한 명령을 실제로 부른 사람이 행위자다 — 회의 승격이면 누른 사람이고, 요청자 자리에
        # 앉은 시스템도 받는 사람도 아니다.
        task = self._repository.create_task_for_request(
            request,
            actor_id=str(principal.id),
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
        )
        return self._view(request, task)

    def accept(self, principal: Principal, request_id: UUID, expected_version: int) -> WorkRequestMutationResult:
        self._require(principal, WORK_REQUEST_DECIDE)
        try:
            command = WorkRequestVersionInput(expected_version=expected_version)
        except ValueError as error:
            raise WorkRequestError(str(error)) from error
        expected_version = command.expected_version
        receipt = self._decision_receipt(principal, request_id, expected_version, "accept")
        if receipt is not None:
            return receipt
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "accepted"
        request.version += 1
        self._repository.record_decision(request, str(principal.id), "accept", expected_version=expected_version)
        # **같은 업무의 담당이 확정된다 — 새 업무가 생기지 않는다** (SPEC-003 §4 수락 · 인수조건).
        # `task_id` 와 `parent_task_id` 가 발송 때 그대로다.
        task = self._repository.accept_request_assignment(request, str(principal.id))
        self._repository.append_audit(request.id, str(principal.id), "work_request.accepted", {"task_id": str(task.id)})
        return self._view(request, task)

    def reject(self, principal: Principal, request_id: UUID, expected_version: int, reason: str) -> WorkRequestMutationResult:
        self._require(principal, WORK_REQUEST_DECIDE)
        try:
            command = WorkRequestRejectInput(expected_version=expected_version, reason=reason)
        except ValueError as error:
            raise WorkRequestError(str(error)) from error
        expected_version, reason = command.expected_version, command.reason
        if not reason.strip():
            raise WorkRejectReasonRequired("거절에는 사유가 필요합니다")
        receipt = self._decision_receipt(principal, request_id, expected_version, "reject")
        if receipt is not None:
            return receipt
        request = self._decision_target(principal, request_id, expected_version)
        request.state = "rejected"
        request.version += 1
        self._repository.record_decision(request, str(principal.id), "reject", reason=reason.strip(), expected_version=expected_version)
        # 거절은 그 업무를 닫는다 — **상위 연결과 로그는 남는다** (SPEC-003 §4 거절 · 인수조건).
        task = self._repository.close_request_task(
            request, str(principal.id), cancel_reason="request_rejected", summary=f"업무 요청 거절로 취소: {request.title}"
        )
        self._repository.append_audit(request.id, str(principal.id), "work_request.rejected", {"reason": reason.strip()})
        return self._view(request, task)

    def negotiate(
        self,
        principal: Principal,
        request_id: UUID,
        expected_version: int,
        conditions: dict[str, Any],
    ) -> WorkRequestMutationResult:
        self._require(principal, WORK_REQUEST_DECIDE)
        try:
            command = WorkRequestNegotiationInput(expected_version=expected_version, conditions=conditions)
        except ValueError as error:
            raise WorkRequestError(str(error)) from error
        expected_version, conditions = command.expected_version, command.conditions
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
    ) -> WorkRequestMutationResult:
        """The requester improving their own request before anyone has judged it.

        Nobody asked for this, so it is not a ReviewDecision and never puts the question back on the requester: it adds
        a round to the same WorkRequest and replaces what the assignee is looking at.
        """
        self._require(principal, WORK_REQUEST_CREATE)
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        command = _amend_command(title=title, description=description, due_date=due_date, clear_due_date=clear_due_date)
        revision = revise_work_request(
            self._domain_request(request),
            ReviseWorkRequest(
                actor_id=str(principal.id),
                expected_version=expected_version,
                mode="amend",
                title=title,
                description=description,
                due_date=due_date,
                clear_due_date=clear_due_date,
                exact_replay=(
                    request.version != expected_version
                    and self._amendment_produced(request, str(principal.id), expected_version, command)
                ),
            ),
        )
        if revision.replay:
            return self._view(request)
        submission = self._apply_round(request, str(principal.id), revision, self._repository.amend)
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

    def _apply_round(self, request: Any, actor_id: str, revision: WorkRequestRevision, open_round: Any) -> Any:
        effect = revision.open_round
        if effect is None:
            raise WorkRequestError("a new request revision must open a round")
        request.title = revision.request.title
        request.description = revision.request.description
        request.due_date = revision.request.due_date
        request.state = revision.request.state
        if revision.clear_conditions:
            request.conditions = None
        snapshot = {
            "title": effect.title,
            "description": effect.description,
            "due_date": effect.due_date.isoformat() if effect.due_date else None,
            "assignee_id": effect.assignee_id,
        }
        submission = open_round(request, actor_id, snapshot)
        request.version = revision.request.version
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
    ) -> WorkRequestMutationResult:
        """Requester revises a negotiated request: new SubjectVersion + Submission with diff; the earlier decision stays."""
        self._require(principal, WORK_REQUEST_CREATE)
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestError("work request was not found")
        revision = revise_work_request(
            self._domain_request(request),
            ReviseWorkRequest(
                actor_id=str(principal.id),
                expected_version=expected_version,
                mode="resubmit",
                title=title,
                description=description,
                due_date=due_date,
                clear_due_date=clear_due_date,
                exact_replay=False,
            ),
        )
        submission = self._apply_round(request, str(principal.id), revision, self._repository.resubmit)
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
        withdrawal = withdraw_work_request(
            self._domain_request(request),
            WithdrawWorkRequest(actor_id=str(principal.id), expected_version=expected_version),
        )
        request.state = withdrawal.request.state
        if withdrawal.clear_conditions:
            request.conditions = None
        request.version = withdrawal.request.version
        self._repository.withdraw(request, str(principal.id))
        # 철회도 그 업무를 닫는다. 수락 전에만 열려 있는 명령이므로 아무도 들지 않은 일을 거두는 것이다.
        task = self._repository.close_request_task(
            request, str(principal.id), cancel_reason="request_withdrawn", summary=f"업무 요청 철회로 취소: {request.title}"
        )
        self._repository.append_audit(request.id, str(principal.id), "work_request.withdrawn", {})
        return self._view(request, task)

    @staticmethod
    def _domain_request(request: Any) -> WorkRequest:
        return WorkRequest(
            id=str(request.id),
            assignee_id=str(request.assignee_id),
            state=str(request.state),
            version=int(request.version),
            requester_id=str(request.requester_id),
            promoted_by_member_id=getattr(request, "promoted_by_member_id", None),
            title=str(request.title),
            description=request.description,
            due_date=request.due_date,
        )

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

    def discussion(self, principal: Principal, request_id: UUID) -> list[ActionDiscussionView]:
        """The comment thread on one request, for a participant. Reading or writing it never moves the judgement."""
        self._require(principal, WORK_REQUEST_READ)
        return self._comment_views(self._participant_request(principal, request_id))

    def _comment_views(self, request: Any) -> list[ActionDiscussionView]:
        if self._comments is None or request.request_thread_id is None:
            return []
        comments = self._comments.list_for(request.request_thread_id)
        bound: dict[str, list[dict[str, Any]]] = {}
        if self._attachments is not None and comments:
            for binding, attachment in self._attachments.bindings_for_many("comment", [str(item.id) for item in comments]):
                bound.setdefault(binding.context_id, []).append(_attachment_view(attachment))
        return [self._comment_view(item, bound.get(str(item.id), [])) for item in comments]

    def attach_to_comment(self, principal: Principal, request_id: UUID, comment_id: UUID, *, name: str, content_type: str, data: bytes) -> ActionDiscussionView:
        """Bind a file to one's own comment (ERD ATTACHMENT_BINDING context=comment, role=discussion)."""
        request, comment = self._comment_attachment_target(principal, request_id, comment_id)
        attachment = store_file(
            self._attachments, self._storage,
            key_prefix=f"work_requests/{request.id}/comments", name=name, content_type=content_type, data=data,
            provenance=f"work_request:{request.id}:comment:{comment.id}", uploaded_by=str(principal.id),
        )
        self._attachments.bind(attachment_id=attachment.id, context_type="comment", context_id=str(comment.id), role="discussion", bound_by=str(principal.id))
        self._request_extraction(attachment)
        return self._comment_view(comment, [_attachment_view(item) for _, item in self._attachments.bindings_for("comment", str(comment.id))])

    def _comment_attachment_target(self, principal: Principal, request_id: UUID, comment_id: UUID) -> tuple[Any, Any]:
        self._require(principal, WORK_REQUEST_READ)
        if self._comments is None or self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        request = self._participant_request(principal, request_id)
        comment = self._comments.comment(request.request_thread_id, comment_id) if request.request_thread_id else None
        if comment is None:
            raise WorkRequestError("comment was not found")
        if comment.author_member_id != str(principal.id):
            raise WorkRequestError("only the comment author may attach files")
        return request, comment

    def _evidence_target(self, principal: Principal, request_id: UUID) -> tuple[Any, Any]:
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
        return request, submission

    def upload_target(self, principal: Principal, request_id: UUID, comment_id: UUID | None = None) -> tuple[str, int]:
        # Current request visibility precedes the narrower author/reviewer upload role.
        self._participant_request(principal, request_id)
        request, _ = (self._comment_attachment_target(principal, request_id, comment_id)
                      if comment_id is not None else self._evidence_target(principal, request_id))
        return request.title, int(request.version)

    def add_evidence(self, principal: Principal, request_id: UUID, *, name: str, content_type: str, data: bytes) -> WorkRequestEvidenceResult:
        """Adopt a file as Evidence for the current Submission: the reviewer adopts decision basis, the requester supplies support."""
        request, submission = self._evidence_target(principal, request_id)
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
                self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id, attachment.uploaded_by))

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

    def add_comment(self, principal: Principal, request_id: UUID, body: str, *, idempotency_key: str | None = None) -> ActionDiscussionView:
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
        text = WorkRequestCommentInput(body=body).body
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
            raise WorkRequestNotFound("work request was not found")
        if not self._is_participant(principal, request):
            raise WorkRequestNotFound("work request was not found")
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
    def _comment_view(comment: Any, attachments: list[dict[str, Any]]) -> ActionDiscussionView:
        return {
            "comment_id": str(comment.id),
            "author_member_id": comment.author_member_id,
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
            "edited_at": comment.edited_at.isoformat() if comment.edited_at else None,
            "attachments": attachments,
        }

    def inbox(self, principal: Principal) -> list[WorkRequestMutationResult]:
        """**내가 답해야 하는 요청** (SPEC-003 §4 `GET /api/work-requests/inbox`).

        받는 사람으로서 아직 답하지 않은 것(`pending`·`negotiating`)만이다 — 내가 보낸 요청은
        `GET /api/work-requests` 쪽이다. 각 줄이 자기가 세운 업무를 함께 가리키므로, 답하기 전에도
        무엇에 대한 요청인지 열어 볼 수 있다.
        """
        self._require(principal, WORK_REQUEST_DECIDE)
        requests = self._repository.inbox_for(str(principal.id))
        derived = self._repository.derived_task_ids(requests)
        return [self._view(request, task_id=derived.get(request.id)) for request in requests]

    def list(self, principal: Principal, *, include_removed: bool = False) -> list[WorkRequestMutationResult]:
        """내 요청 목록. **내가 정리한 항목은 여기서만 빠진다** (SPEC-003 §4 목록 정리).

        읽기 권한은 그대로다 — 정리는 「내 목록에서 감춘다」이지 「없앤다」가 아니다. 그래서 이 걸러내기는
        목록 표면에만 있고, 권한을 답하는 `list_for()` 에는 없다. 거기 두면 정리한 순간 그 요청의
        상세·자료가 통째로 안 열린다.
        """
        self._require(principal, WORK_REQUEST_READ)
        requests = self._repository.list_for(str(principal.id))
        hidden = self._repository.hidden_request_ids(str(principal.id))
        if not include_removed:
            requests = [request for request in requests if request.id not in hidden]
        derived = self._repository.derived_task_ids(requests)
        # **정리된 항목인지는 서버가 말한다.** 화면이 기억해 두는 것이 아니다 — 브라우저를 새로 열면
        # 그 기억은 사라지는데 정리한 사실은 남아야 한다 (SPEC-003 §4 목록 정리 · 정책 P-12).
        return [
            self._view(request, task_id=derived.get(request.id), hidden=request.id in hidden)
            for request in requests
        ]

    def remove_from_list(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        """요청자 목록에서 그 항목을 뺀다. **행도 로그도 지우지 않는다** (정책 P-12).

        전역 삭제나 상대방 자료 삭제로 넓히지 않는다 — 내 화면에서만 사라진다.
        """
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        # **요청자의 명령이다** (SPEC-003 §3 S-15 · §4 목록 정리). 수신자가 자기 화면에서 남의 요청을
        # 치우는 명령이 아니다 — 받은 쪽의 정리는 계약이 없다. 승격 요청이면 **누른 사람**이 요청자
        # 자리에 선다 (BASE-002 O-31), 요청자 전용 조작이 이미 그 모양으로 판정한다.
        if str(principal.id) not in {
            str(request.requester_id),
            str(getattr(request, "promoted_by_member_id", None) or ""),
        }:
            raise WorkRequestAccessDenied("요청자만 자기 목록에서 정리할 수 있습니다")
        # **「취소 항목」의 정리다.** 아직 답을 기다리거나 진행 중인 요청은 목록에서 치울 것이 아니다 —
        # 치우면 그 사람이 답을 기다린다는 사실이 아무 데도 남지 않는다. 거두려면 철회가 그 명령이다.
        if str(request.state) not in _CLEANABLE_REQUEST_STATES:
            raise WorkRequestNotPending(
                "진행 중인 요청은 정리할 수 없습니다. 아직 수락 전이면 철회하세요"
            )
        self._repository.remove_list_entry(request, str(principal.id))
        return {"request_id": str(request.id), "removed": True}

    def receipt(self, principal: Principal, request_id: UUID) -> WorkRequestMutationResult:
        """이미 선 요청의 영수증 — 생성이 냈던 것과 같은 투영이고, 읽을 권한을 지금 다시 검사한다."""
        self.get(principal, request_id)
        request = self._repository.request(request_id)
        return self._view(request, task_id=self._repository.derived_task_ids([request]).get(request.id))

    def get(self, principal: Principal, request_id: UUID) -> WorkRequestDetailResult:
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

    def _decision_receipt(
        self, principal: Principal, request_id: UUID, expected_version: int, decision: str
    ) -> WorkRequestMutationResult | None:
        """**같은 사람의 같은 답을 다시 보낸 것인가** (SPEC-003 §3 S-16 · §4 「재전송이면 영수증이 먼저」).

        재전송의 신원은 **이미 내려진 판단 행**이 갖고 있다: 누가(actor) · 무엇을(decision) · 어느
        회차에(그 판단이 소비한 `expected_version`). 셋이 모두 같으면 그때의 답이 지금 다시 온 것이고,
        **두 번째 effect 없이** 현재 상태를 돌려준다.

        이 판정이 회차 검사보다 **먼저** 와야 한다. 수락은 요청의 회차를 올리므로, 사람이 보던 화면에서
        그대로 다시 누른 요청은 「그때의 회차」를 싣고 온다 — 그것을 stale 로 거절하면 통신 재시도가
        실패로 읽힌다. 반대로 **다른 회차·다른 답**은 재전송이 아니라 새 명령이고, 아래에서 갈린다.

        돌려주기 전에 **지금의 열람 권한을 다시 검사한다** (K-2) — 잃었으면 존재를 숨긴다.
        """
        request = self._repository.request(request_id)
        if request is None:
            return None
        for row in self._repository.request_decisions(request):
            if str(row.actor_member_id) != str(principal.id) or str(row.decision) != decision:
                continue
            if decision_facts(row.conditions).get(DECISION_VERSION) != expected_version:
                continue
            # 존재를 다시 묻는다: 관계가 끊겼으면 영수증도 주지 않는다.
            self.get(principal, request_id)
            return self._view(request, task_id=self._repository.derived_task_ids([request]).get(request.id))
        return None

    def _decision_target(self, principal: Principal, request_id: UUID, expected_version: int) -> Any:
        request = self._repository.request(request_id, lock=True)
        if request is None:
            raise WorkRequestNotFound("work request was not found")
        if request.assignee_id != str(principal.id):
            raise WorkRequestResponderOnly("이 요청에 답할 수 있는 사람은 받는 사람뿐입니다")
        if request.version != expected_version:
            raise WorkRequestError("work request version is stale")
        if request.state not in {"pending", "negotiating"}:
            raise WorkRequestNotPending("이미 답한 요청입니다")
        return request

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise WorkRequestAccessDenied(f"{capability} capability is required")

    def _view(self, request: Any, task: Any | None = None, *, task_id: Any | None = None, hidden: bool = False) -> WorkRequestMutationResult:
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
            # **업무가 섰다고 담당이 선 것이 아니다.** v2 에서 발송은 업무를 세우고 담당은 수락이 세운다,
            # 그래서 「업무가 있으면 active」였던 옛 규칙이 더는 맞지 않는다 — 수락 대기 중인 요청이
            # 보낸 사람 화면에서 「담당 확정」으로 읽히게 된다. 요청 상태가 그 답을 이미 갖고 있다.
            "assignment_state": _assignment_state(request.state, bool(task or task_id)),
            # 하위 요청이면 발송 때 정해진 상위. 재요청이면 이전 요청.
            "parent_task_id": str(request.parent_task_id) if getattr(request, "parent_task_id", None) else None,
            "supersedes_request_id": (
                str(request.supersedes_request_id) if getattr(request, "supersedes_request_id", None) else None
            ),
            # **내가 이 항목을 목록에서 정리했는가.** 서버가 말한다 — 화면이 기억해 두면 새로 열 때 사라진다.
            "list_entry_hidden": hidden,
            "conditions": request.conditions,
        }


#: 목록에서 정리할 수 있는 요청 상태 — **끝난 것들**이다. 거절·철회·합의 취소가 그 셋이고,
#: 그 업무는 `cancelled` 로 닫혀 있다. 로그는 그대로 남는다 (정책 L-6).
_CLEANABLE_REQUEST_STATES = frozenset({"rejected", "withdrawn", "cancelled_by_agreement"})


#: 요청 상태 → 담당 관계 상태. 요청이 답을 이미 갖고 있으므로 담당 행을 다시 묻지 않는다.
#: `assigned` 는 **W1 이 내던 값**이고 v2 는 발행하지 않는다 — 과거 행에서는 뜻 그대로 읽힌다 (정책 P-8).
_REQUEST_ASSIGNMENT_STATE = {
    "pending": "pending",
    "negotiating": "pending",
    "accepted": "active",
    "assigned": "active",
    "rejected": "ended",
    "withdrawn": "ended",
    "cancelled_by_agreement": "ended",
}


def _assignment_state(request_state: str, has_task: bool) -> str | None:
    if not has_task:
        return None
    return _REQUEST_ASSIGNMENT_STATE.get(str(request_state))


def _attachment_view(attachment: Any) -> dict[str, Any]:
    return {
        "attachment_id": str(attachment.id),
        "name": attachment.name,
        "content_type": attachment.content_type,
        "size_bytes": int(attachment.size_bytes),
        "uploaded_by": attachment.uploaded_by,
        "created_at": attachment.created_at.isoformat(),
    }
