"""Direct WorkRequest lifecycle; it never creates WorkflowRun records."""
from __future__ import annotations

from ax_workspace.modules.work.request_results import WorkRequestDetailResult, WorkRequestEvidenceResult
from ax_workspace.modules.actions.results import ActionDiscussionView

from ax_workspace.modules.work.request_results import (
    WorkRequestInboxEntry,
    WorkRequestMaterialResult,
    WorkRequestMaterialView,
    WorkRequestMutationResult,
    WorkRequestReadReceiptResult,
)
from ax_workspace.modules.work.request_commands import (
    WorkRequestCommentInput,
    WorkRequestMaterialLinkInput,
    WorkRequestNegotiationInput,
    WorkRequestRejectInput,
    WorkRequestVersionInput,
)


from collections.abc import Iterable
from datetime import UTC, date, datetime
import hashlib
import json
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from ax_workspace.modules.organization_access.domain import (
    Principal,
    TASK_SELF_MANAGE,
    WORK_REQUEST_CREATE,
    WORK_REQUEST_DECIDE,
    WORK_REQUEST_READ,
)
from ax_workspace.modules.work.request_errors import (
    WorkRejectReasonRequired,
    WorkRequestReferenceReadForbidden,
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
from ax_workspace.modules.work.parties import may_read, party_of
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionQueue, MaterialExtractionRepository
from ax_workspace.modules.work.materials import AttachmentRepository, MaterialNotFound, MaterialStorage, material_view, store_file
from ax_workspace.modules.work.material_values import MaterialError, MaterialLink


def _utc_iso(value: datetime) -> str:
    """시각 하나를 **표면에 나가는 한 모양**으로 맞춘다 — 시간대가 없으면 UTC 로 읽는다.

    저장이 시간대를 잃어버리는 데이터베이스가 있어서, 갓 만든 값(aware)과 다시 읽은 값(naive)이
    같은 순간인데 다른 글자로 나가는 자리가 생긴다. 밖으로 나가는 계약에서는 그 차이를 없앤다.
    """
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


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
        start_date: date | None = None,
        due_date: date | None = None,
        project_id: UUID | None = None,
        approver_id: str | None = None,
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
    def read_receipt(self, request_id: UUID, member_id: str) -> Any | None: ...
    def mark_read(self, request_id: UUID, member_id: str) -> Any: ...
    def read_request_ids(self, member_id: str) -> set[UUID]: ...
    def derived_task_ids(self, requests: list[Any]) -> dict[UUID, UUID]: ...
    #: 그 업무의 **활성 선행**. 요청 행은 순서를 갖지 않으므로(발송이 세운 업무가 그 자리다) 조회는
    #: 파생 업무에서 읽어 온다 — 생성 입력으로 받은 값이 조회로 돌아오지 않던 자리를 잇는다.
    def predecessor_task_ids(self, task_id: UUID | None) -> list[UUID]: ...
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
        preceding_task_ids: list[UUID] | None = None,
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
    #: 어느 프로젝트의 일로 둘 수 있는가. **업무 모듈이 판정한다** — 하위는 상위를 따르고, 읽을 수 없는
    #: 프로젝트에는 일을 밀어 넣을 수 없다는 규칙이 거기 한 곳에 있다.
    def project_for(self, principal: Principal, project_id: UUID | None, parent: Any = None) -> UUID | None: ...
    #: 선행으로 설 수 있는 배열인가. **업무 모듈이 판정한다** — 프로젝트 일치·자기 자신·중복·순환이
    #: 거기 한 곳에 있다. 발송 단계에는 업무가 아직 없어 `task_id` 를 넘기지 않는다.
    def resolve_predecessors(self, principal: Principal, wanted: list[UUID], *, project_id: Any) -> list[UUID]: ...
    #: 결재자로 설 수 있는 사람인가 — **업무 모듈이 판정한다.** 재직 여부와 「담당자 본인 불가」가
    #: 거기 한 곳에 있고, 두 갈래가 같은 규칙을 지나야 한다. 갈래가 정하는 것은 **누가 담당인가**
    #: 하나뿐이다: 내 업무는 만드는 사람, 요청은 받는 사람이다.
    def valid_approver(self, principal: Principal, approver_id: str | None, *, assignee_id: str | None) -> str | None: ...


class WorkRequestAssigneeDirectory(Protocol):
    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool: ...
    def member_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_active_member(self, principal: Principal, member_id: str) -> bool: ...


#: 요청에 붙은 자료가 서는 binding context. 논의(`comment`)·판단 근거(`submission`)와 **다른 자리**다:
#: 세 가지는 뜻이 다르고, 한 자리에 섞으면 요청 타임라인이 자료로 오염된다 (WORK-003 요청 자료 계약).
WORK_REQUEST_MATERIAL_CONTEXT = "work_request"
#: 요청 자료의 역할은 **참고 자료 하나**다. 요청에는 아직 결과가 없으므로 산출물이 설 자리가 없고,
#: 수락이 이 자료를 그대로 업무에 이어 붙이므로 여기서 산출물을 허용하면 아무도 만들지 않은 산출물이
#: 업무의 완료 보고 후보로 선다.
WORK_REQUEST_MATERIAL_ROLE = "input"
#: 자료를 **붙이고 뗄 수 있는** 요청 상태. 답이 온 뒤의 요청은 기록이고, 거기서는 읽기만 남는다 —
#: 거절·철회된 요청의 자료도 그대로 읽힌다 (목록·내려받기는 상태를 묻지 않는다).
_MATERIAL_WRITABLE_STATES = frozenset({"pending", "negotiating"})

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
        start_date: date | None = None,
        due_date: date | None = None,
        project_id: UUID | None = None,
        approver_id: str | None = None,
        cc_member_ids: list[str] | None = None,
        preceding_task_ids: list[UUID] | None = None,
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
        # 프로젝트도 **업무 모듈이 판정한다** — 하위 요청은 묻지 않고 상위를 따르고, 읽을 수 없는
        # 프로젝트로는 보낼 수 없다. 내 업무 생성이 지나는 것과 같은 한 자리다.
        project = (
            self._parents.project_for(principal, project_id, parent)
            if self._parents is not None
            else project_id
        )
        # **선행도 업무 모듈이 판정한다** — 프로젝트 일치·읽기 권한이 거기 한 곳에 있다. 발송이
        # 세우는 Task 가 아직 없으므로 자기 자신도 순환도 성립하지 않는다 (`task_id=None`).
        # 판정을 여기서 다시 쓰면 두 벌이 조용히 갈린다.
        predecessors = (
            self._parents.resolve_predecessors(principal, list(preceding_task_ids or ()), project_id=project)
            if self._parents is not None
            else list(preceding_task_ids or ())
        )
        # **결재자도 업무 모듈이 판정한다.** 재직 여부와 「담당자 본인 불가」가 거기 한 곳에 있고,
        # 요청 갈래의 담당은 **받는 사람**이다 — 자기에게 온 일을 자기가 확인하는 자리를 만들지 않는다.
        approver = (
            self._parents.valid_approver(principal, approver_id, assignee_id=assignee_id)
            if self._parents is not None
            else (approver_id or None)
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
            start_date=start_date,
            due_date=due_date,
            project_id=project,
            approver_id=approver,
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
            # 요청에 실린 선행이 **그 요청이 세우는 업무의 선행**이 된다 — 요청 행은 순서를 따로 갖지
            # 않는다(업무가 발송 시점에 이미 서므로 그것이 관계의 자리다).
            preceding_task_ids=predecessors,
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
        # **요청에 실린 자료가 그 업무의 자료로 함께 선다** (WORK-003). 요청 binding 은 그대로 두고
        # 같은 Attachment 에 업무 binding 을 더한다 — 복사본을 만들지 않으므로 파일은 하나다.
        self._adopt_materials_into_task(request, task, str(principal.id))
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
        if not self._is_requester(principal, request) and not self._is_assignee(principal, request):
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

    # ---- 요청 자료 — 발송한 요청에 **2단계로** 붙는 참고 자료 (WORK-003) ------------------
    #
    # 생성 payload 에 자료 칸을 더하지 않는다: 요청은 보내는 순간 아직 id 가 없고, 부분 실패한
    # 업로드가 요청 자체를 못 만든 것으로 읽히기 때문이다. 내 업무가 지나는 길과 **같은 모양**으로
    # 만들고(요청 id 를 받고) 그 id 로 붙인다 — 화면이 두 갈래를 다르게 그리지 않아도 된다.

    def list_materials(self, principal: Principal, request_id: UUID) -> list[WorkRequestMaterialView]:
        """요청에 붙어 있는 자료들. **읽기는 참여자 전부**에게 열린다 — 요청 조회와 같은 문이다."""
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        return self._material_views(request)

    def attach_material(
        self, principal: Principal, request_id: UUID, *, kind: str = WORK_REQUEST_MATERIAL_ROLE,
        name: str, content_type: str, data: bytes,
    ) -> WorkRequestMaterialResult:
        """파일 하나를 요청에 붙인다. **보낸 사람만** 붙일 수 있고, 답이 오기 전까지다."""
        request = self._material_writer(principal, request_id)
        self._require_material_role(kind)
        attachment = store_file(
            self._attachments, self._storage,
            key_prefix=f"work_requests/{request.id}/materials",
            name=name, content_type=content_type, data=data,
            provenance=f"upload by {principal.id} to work request {request.id}", uploaded_by=str(principal.id),
        )
        binding = self._bind_material(request, attachment, str(principal.id))
        # 파일은 읽을 내용이 있다 — 업무 자료와 같은 자리에서 추출을 건다.
        self._request_extraction(attachment)
        return self._material_moved(request, binding, attachment, self._material_extraction(attachment))

    def attach_material_link(
        self, principal: Principal, request_id: UUID, *, kind: str = WORK_REQUEST_MATERIAL_ROLE,
        url: str, label: str,
    ) -> WorkRequestMaterialResult:
        """다른 곳에 있는 것을 요청이 가리킨다. 내용을 가져오지 않고 판본을 못 박지 않는다."""
        request = self._material_writer(principal, request_id)
        self._require_material_role(kind)
        link = MaterialLink.create(url, label)
        attachment = self._attachments.add_link(
            url=link.url, name=link.label,
            provenance=f"link by {principal.id} on work request {request.id}", uploaded_by=str(principal.id),
        )
        binding = self._bind_material(request, attachment, str(principal.id))
        # 링크는 읽을 내용이 없으므로 추출을 걸지 않는다 — 검색은 그것을 「읽을 수 없음」으로 말한다.
        return self._material_moved(request, binding, attachment, None)

    def open_material(self, principal: Principal, request_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        """내려받기. **읽을 수 있는 사람이면 내려받을 수 있다** — 「보이는데 못 받는다」를 만들지 않는다."""
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        binding, attachment = self._material_binding(request, material_id)
        if attachment.source_kind != "file":
            raise MaterialError("only file attachments have downloadable content")
        if getattr(attachment, "lifecycle", "available") == "purged":
            raise MaterialNotFound("이 자료는 완전히 삭제되어 더 이상 내려받을 수 없습니다")
        try:
            data = self._storage.get(attachment.source_ref)
        except FileNotFoundError as error:
            # 행은 파일이 있다고 말하는데 저장소가 아니라고 한다: 버그로 터지지 않고 그대로 말한다.
            raise MaterialNotFound("자료 원본을 찾을 수 없습니다") from error
        view = self._material_view(
            request, binding, attachment, self._material_extraction(attachment), task_id=self._derived_task_id(request)
        )
        return view, data

    def detach_material(self, principal: Principal, request_id: UUID, material_id: UUID) -> WorkRequestMaterialResult:
        """요청에서 뗀다. **Attachment 와 바이트는 남는다** — binding 이 언제 떠났는지를 적는다."""
        request = self._material_writer(principal, request_id)
        binding, attachment = self._material_binding(request, material_id)
        self._attachments.unbind(binding)
        self._repository.append_audit(
            request.id, str(principal.id), "work_request.material_detached",
            {"attachment_id": str(attachment.id), "name": attachment.name},
        )
        return self._material_moved(request, binding, attachment, self._material_extraction(attachment))

    def _material_writer(self, principal: Principal, request_id: UUID) -> Any:
        """붙이고 떼는 자리 — **보낸 사람 하나**이고, 아직 답이 오지 않은 요청에서만이다.

        판정을 여기 한 곳에 모은다. 참여자 판정(`_participant_request`)이 먼저 서서 **못 읽는 사람에게는
        존재를 숨기고**(404), 읽을 수는 있지만 보낸 사람이 아닌 참여자만 403 으로 갈린다.
        """
        self._require(principal, WORK_REQUEST_READ)
        if self._attachments is None or self._storage is None:
            raise WorkRequestError("attachments are not available")
        request = self._participant_request(principal, request_id)
        if not self._is_requester(principal, request):
            raise WorkRequestAccessDenied("요청 자료는 보낸 사람만 붙이고 뗄 수 있습니다")
        if str(request.state) not in _MATERIAL_WRITABLE_STATES:
            raise WorkRequestNotPending("이미 답한 요청의 자료는 바꿀 수 없습니다")
        return request

    @staticmethod
    def _require_material_role(kind: str) -> None:
        if str(kind) != WORK_REQUEST_MATERIAL_ROLE:
            raise MaterialError("요청 자료는 참고 자료(input)로만 붙일 수 있습니다")

    def _bind_material(self, request: Any, attachment: Any, actor_id: str) -> Any:
        binding = self._attachments.bind(
            attachment_id=attachment.id,
            context_type=WORK_REQUEST_MATERIAL_CONTEXT,
            context_id=str(request.id),
            role=WORK_REQUEST_MATERIAL_ROLE,
            bound_by=actor_id,
        )
        self._repository.append_audit(
            request.id, actor_id, "work_request.material_attached",
            {"attachment_id": str(attachment.id), "name": attachment.name},
        )
        return binding

    def _material_bindings_for(self, request: Any) -> list[tuple[Any, Any]]:
        if self._attachments is None:
            return []
        return [
            (binding, attachment)
            for binding, attachment in self._attachments.bindings_for(WORK_REQUEST_MATERIAL_CONTEXT, str(request.id))
            if binding.unbound_at is None
        ]

    def _material_binding(self, request: Any, material_id: UUID) -> tuple[Any, Any]:
        found = next(
            ((binding, attachment) for binding, attachment in self._material_bindings_for(request)
             if attachment.id == material_id),
            None,
        )
        if found is None:
            raise MaterialNotFound("material was not found")
        return found

    def _material_extraction(self, attachment: Any) -> Any | None:
        if self._extractions is None:
            return None
        return self._extractions.for_attachments([attachment.id]).get(attachment.id)

    def _material_views(self, request: Any, *, extractions: bool = True) -> list[WorkRequestMaterialView]:
        active = self._material_bindings_for(request)
        if not active:
            return []
        found = (
            self._extractions.for_attachments([attachment.id for _, attachment in active])
            if extractions and self._extractions is not None
            else {}
        )
        # 파생 업무는 요청당 하나다 — 자료마다 다시 묻지 않는다.
        task_id = self._derived_task_id(request)
        return [
            self._material_view(request, binding, attachment, found.get(attachment.id), task_id=task_id)
            for binding, attachment in active
        ]

    def _derived_task_id(self, request: Any) -> Any | None:
        return self._repository.derived_task_ids([request]).get(request.id)

    def _material_view(
        self, request: Any, binding: Any, attachment: Any, extraction: Any | None, *, task_id: Any | None
    ) -> WorkRequestMaterialView:
        """업무 자료와 **같은 투영**에 자리 둘을 얹는다 — 어느 요청이고 어느 업무로 이어졌는가."""
        return {
            **material_view(binding, attachment, extraction),
            "request_id": str(request.id),
            "task_id": str(task_id) if task_id else None,
        }

    def _material_moved(self, request: Any, binding: Any, attachment: Any, extraction: Any | None) -> WorkRequestMaterialResult:
        """붙이고 뗀 것은 요청의 **내용이 달라진 것**이다 — 업무 자료가 업무 회차를 올리는 것과 같다.

        회차가 오르므로, 보던 화면에서 그대로 누른 수락은 stale 로 갈린다. 그것이 맞는 답이다:
        받는 사람이 본 요청과 지금의 요청이 다르다.
        """
        request.version += 1
        view = self._material_view(request, binding, attachment, extraction, task_id=self._derived_task_id(request))
        return {**view, "request_version": int(request.version)}

    def _adopt_materials_into_task(self, request: Any, task: Any, actor_id: str) -> None:
        """수락이 요청 자료를 **파생 업무에도** 세운다 — 원본 요청 binding 은 그대로 남는다 (이중 바인딩).

        복사본을 만들지 않는다. 같은 Attachment 가 두 자리에 서므로 파일도 무결성 해시도 하나이고,
        요청 기록에서 읽던 것과 업무에서 읽는 것이 **같은 자료**임이 구조로 남는다.
        """
        if task is None or self._attachments is None:
            return
        already = {
            attachment.id
            for binding, attachment in self._attachments.bindings_for("task", str(task.id))
            if binding.unbound_at is None
        }
        for _, attachment in self._material_bindings_for(request):
            if attachment.id in already:
                continue
            self._attachments.bind(
                attachment_id=attachment.id,
                context_type="task",
                context_id=str(task.id),
                role=WORK_REQUEST_MATERIAL_ROLE,
                bound_by=actor_id,
            )

    def material_bindings(self, principal: Principal, request_id: UUID) -> list[tuple[Any, Any]]:
        """요청에 붙은 자료 · 논의에 붙은 파일 · 채택된 판단 근거 — 지금 참여자가 읽을 수 있는 전부.

        **자료 검색이 요청을 소유자로 읽는 한 자리다.** 요청 자료를 여기 세우지 않으면 화면에서는
        보이는 파일이 검색에는 없는 자리가 생긴다.
        """
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        if self._attachments is None:
            return []
        bindings = list(self._material_bindings_for(request))
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
        # **내려받을 내용이 없는 자료는 여기서 걸린다** — 요청 자료가 이 목록에 서면서 링크도 닿게 됐고,
        # 링크의 `source_ref` 는 주소이지 storage key 가 아니다. 그대로 저장소에 넘기면 키 검사가
        # `ValueError` 로 터져 **500** 이 나간다: 말이 되는 요청에 서버 잘못이라고 답하는 자리다.
        # 새 요청 자료 내려받기(`open_material`)가 쓰는 것과 **같은 가드**이고 같은 422 로 나간다.
        if attachment.source_kind != "file":
            raise MaterialError("only file attachments have downloadable content")
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

    @staticmethod
    def _is_assignee(principal: Principal, request: Any) -> bool:
        """받는 사람인가 — **판단(수락·거절·협의)을 여는 유일한 자리다.**"""
        return str(principal.id) == str(request.assignee_id)

    def _is_cc(self, principal: Principal, request: Any) -> bool:
        """참조자인가 — 읽기와 논의만 열린다 (`modules/work/parties.py`)."""
        return str(principal.id) in self._repository.cc_member_ids(request)

    def _party_of(self, principal: Principal, request: Any) -> Any:
        """이 사람이 선 **자리 하나**. 셋을 한 판정으로 묶지 않는 이유가 `parties.py` 에 있다."""
        return party_of(
            str(principal.id),
            requester_ids=[
                str(request.requester_id),
                str(getattr(request, "promoted_by_member_id", None) or ""),
            ],
            assignee_ids=[str(request.assignee_id)],
            cc_member_ids=self._repository.cc_member_ids(request),
        )

    def _is_participant(self, principal: Principal, request: Any) -> bool:
        """읽고 논의할 수 있는가. **세 자리 전부**가 여기 서고, 그 이상은 각자의 판정이 따로 묻는다."""
        return may_read(self._party_of(principal, request))

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

    def inbox(self, principal: Principal) -> list[WorkRequestInboxEntry]:
        """답할 업무와 CC로 받은 참조의 합집합. TaskReference는 수신 관계가 아니다.

        담당자 업무(`work`)는 기존대로 pending/negotiating 만이고 **이 절이 손대지 않는다** —
        읽음이 수락을 대신하지 않는다 (SPEC-001 U-12).

        **참고 갈래(`reference`)에만 미읽음 필터가 걸린다** (SPEC-001 §4 「수신함 조회 — 필터가
        어디에 걸리나」). 기준은 **조회하는 사람**이다: 다른 참조자의 읽음은 내 결과를 바꾸지 않는다.

        이 필터가 **여기 하나에만** 있는 것이 계약이다. 요청 목록(`list()`)에 걸면 읽은 참고 업무가
        `참조 업무` 탭에서도 사라지고, 업무 조회에 걸면 읽음이 업무의 사실인 것처럼 번진다.
        화면·REST·MCP·AX 가 전부 이 함수를 지나므로 표면마다 다른 규칙이 서지 않는다.
        """
        can_decide = WORK_REQUEST_DECIDE in principal.capabilities
        can_read = WORK_REQUEST_READ in principal.capabilities
        if not can_decide and not can_read:
            self._require(principal, WORK_REQUEST_READ)
        member_id = str(principal.id)
        entries: list[WorkRequestInboxEntry] = []
        requests = self._repository.list_for(member_id)
        derived = self._repository.derived_task_ids(requests)
        already_read = self._repository.read_request_ids(member_id) if can_read else set()
        for request in requests:
            view = self._view(request, task_id=derived.get(request.id))
            if can_decide and request.assignee_id == member_id and request.state in {"pending", "negotiating"}:
                entries.append({**view, "category": "work"})
            elif can_read and member_id in view["cc_member_ids"] and request.id not in already_read:
                entries.append({**view, "category": "reference"})
        return entries

    def mark_reference_read(self, principal: Principal, request_id: UUID) -> WorkRequestReadReceiptResult:
        """그 참고 항목을 **내 수신함에서만** 접는다 (SPEC-001 §4 · DEC-001 D-19).

        판정 순서가 계약이다 (WORK-003 § Internal Interface Contract):

        1. **그 요청을 읽을 수 있는가** — 아니면 없는 것과 **같은 말**로 답한다(404). 참조자가
           아닌 것과 존재하지 않는 것을 여기서 가르면 존재 여부가 샌다.
        2. **그 요청의 참조자인가** — 읽을 수는 있는데 참조자가 아닌 사람만 403 이다. 요청자·담당자·
           관리자에게는 이 명령이 없다 (§5 권한).
        3. **이미 있으면 그것을 그대로 돌려준다** — 멱등이다. 회차도 멱등 키도 요구하지 않는다.

        **요청 행을 수정하지 않는다** — 회차도 상태도 CC 관계도 그대로다. 올리면 읽음 하나가 남이
        들고 있던 낙관적 잠금을 깨뜨린다 (§5 동시성).
        """
        self._require(principal, WORK_REQUEST_READ)
        request = self._participant_request(principal, request_id)
        if not self._is_cc(principal, request):
            raise WorkRequestReferenceReadForbidden("이 항목을 읽음 처리할 권한이 없습니다")
        receipt = self._repository.mark_read(request.id, str(principal.id))
        return {
            "request_id": str(request.id),
            "read": True,
            # **UTC 로 못 박아 낸다.** 방금 쓴 값과 다시 읽은 값이 같은 글자여야 두 번째 호출이
            # 「처음 값 그대로」임을 호출자가 문자열로 확인할 수 있다 — SQLite 는 시간대를 저장하지
            # 않아 다시 읽은 값이 naive 로 돌아오고, 그대로 내면 같은 순간이 두 모양으로 나간다.
            "read_at": _utc_iso(receipt.read_at),
        }

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
        """참조자·결재자로 **고를 수 있는 사람들**. 요청 갈래만의 물음이 아니다 (WORK-003 gap D).

        `POST /api/tasks` 의 본인 갈래가 이미 `cc_member_ids` 와 `approver_id` 를 받는다. 그런데 이
        후보 조회만 `work_request.create` 를 요구해서, 업무만 만들 수 있는 사람(`task.self_manage`)의
        생성 창에서는 **고를 명단을 읽지 못해 두 칸이 통째로 사라졌다** — 받는 값과 고를 목록이
        어긋나 있었다. 그래서 「무엇이든 만들 수 있는 사람」으로 한 칸만 넓힌다.

        **넓어지는 것은 여기까지다**: 후보는 재직 중인 사람의 id 와 이름뿐이고(`member_candidates`),
        담당 후보(`assignee_candidates`)는 요청 생성 권한 그대로다.
        """
        self._require_any(principal, WORK_REQUEST_CREATE, TASK_SELF_MANAGE)
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
        # **받는 사람 자리를 직접 묻는다** — 자리 우선순위(`party_of`)로 묻지 않는다. 요청자와
        # 받는 사람이 같은 사람인 요청(회의 승격의 자기 배정)에서 그 사람은 요청자 자리로 읽히고,
        # 그러면 자기에게 온 요청에 답할 수 없게 된다. 판단은 **그 자리에 있는가** 하나로 연다.
        if not self._is_assignee(principal, request):
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

    @staticmethod
    def _require_any(principal: Principal, *capabilities: str) -> None:
        """둘 중 하나면 된다. 없는 것을 여기서 만들지 않는다 — 이름은 그대로 거절에 실린다."""
        if not any(capability in principal.capabilities for capability in capabilities):
            raise WorkRequestAccessDenied(f"one of {', '.join(capabilities)} capabilities is required")

    def _view(self, request: Any, task: Any | None = None, *, task_id: Any | None = None, hidden: bool = False) -> WorkRequestMutationResult:
        submission = self._repository.current_submission(request)
        derived = task.id if task is not None else task_id
        return {
            "request_id": str(request.id),
            "request_thread_id": str(request.request_thread_id) if getattr(request, "request_thread_id", None) else None,
            "submission_version": submission.submission_version if submission is not None else None,
            "title": request.title,
            "description": getattr(request, "description", None),
            # **내 업무와 같은 공통 payload 를 같은 이름으로 낸다** — 시작일과 프로젝트가 요청에도 있다.
            "start_date": (
                request.start_date.isoformat() if getattr(request, "start_date", None) else None
            ),
            "due_date": request.due_date.isoformat() if getattr(request, "due_date", None) else None,
            "project_id": str(request.project_id) if getattr(request, "project_id", None) else None,
            # 결재자 — 이 요청이 세우는 업무의 `approver_id` 와 **같은 값**이다 (SPEC-001 §7 OQ-N 라벨 「결재자」).
            "approver_id": getattr(request, "approver_id", None),
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
            # **생성이 받던 셋이 조회로 돌아온다** (WORK-003 gap C). 셋 다 「요청이 업무가 될 때 함께
            # 넘어가는 값」이라, 상세에서 보이지 않으면 보낸 사람도 받은 사람도 무엇이 함께 갔는지
            # 확인할 길이 없고, 「다시 요청」이 이전 요청을 미리 채울 수도 없다.
            #
            # 선행은 **요청이 세운 업무**에서 읽는다 — 요청 행은 순서를 따로 갖지 않는다.
            "preceding_task_ids": [str(item) for item in self._repository.predecessor_task_ids(derived)],
            "reference_task_ids": [
                str(row.referenced_task_id) for row in self._repository.request_references(request.id)
            ],
            # 붙어 있는 자료. **추출 상태는 여기서 묻지 않는다** — 목록 한 줄마다 추출 표를 뒤지지 않기
            # 위해서다. 자료 탭이 읽는 `GET …/materials` 가 그 값을 싣는다.
            "materials": self._material_views(request, extractions=False),
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
