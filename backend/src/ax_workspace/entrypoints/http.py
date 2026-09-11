from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from ax_workspace.modules.work.material_search import MaterialResourceType
from ax_workspace.modules.organization_access.administration import (
    AccessAdministrationDenied,
    AccessAdministrationError,
    AccessNotFound,
    AccessVersionConflict,
)
from ax_workspace.modules.organization_access.catalog import UnknownCapability
from ax_workspace.modules.organization_access.credentials import AuthenticationFailed
from ax_workspace.modules.organization_access.application import (
    AssistantCharacterPreferenceConflict,
    UnsupportedAssistantCharacter,
)
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.entrypoints.http_auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    DeveloperAuthAdapter,
    cookie_secure,
    developer_principal,
    session_id_from,
    session_principal,
)
from ax_workspace.bootstrap.application import create_auth_session_store, create_workflow_application
from ax_workspace.modules.work.application import InvalidTaskTransition, TaskAccessDenied, TaskError, TaskNotFound, TaskState
from ax_workspace.modules.work.graph import GraphAccessDenied, GraphError, GraphNotFound
from ax_workspace.modules.work.materials import MaterialNotFound
from ax_workspace.modules.actions.domain import ActionError as ActionCenterError, ActionNotFound
from ax_workspace.modules.work.requests import WorkRequestAccessDenied, WorkRequestError, WorkRequestIdempotencyConflict
from ax_workspace.modules.reports.application import DailyReportAccessDenied
from ax_workspace.modules.meetings.domain import MeetingAccessDenied, MeetingError, MeetingNotFound
from ax_workspace.modules.work.projects import ProjectAccessDenied, ProjectError, ProjectNotFound
from ax_workspace.modules.meetings.transcription import TranscriptionFailure, FinalTranscriptSegment
from ax_workspace.modules.ax_execution.conversations import ConversationError, ConversationQueueOverflow
from ax_workspace.modules.ax_execution.actions import ActionAccessDenied, ActionCapabilityDenied, ActionError
from ax_workspace.bootstrap.seed import DEMO_PASSWORD, SEEDED_MEMBERS
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure
from ax_workspace.modules.notifications import NotificationNotFound


class MemberResponse(BaseModel):
    id: str
    display_name: str
    #: 인사 정보. 조직 관리 권한(`organization.manage`)이 없는 사람에게는 자리만 오고 값은 비어 있다 —
    #: 응답의 모양으로 권한을 추측하게 만들지 않는다.
    phone: str | None = None
    birth_date: date | None = None
    #: 들어올 문이 있는가. 권한이 아니라 로그인 계정의 유무다.
    has_account: bool = False


class CandidateResponse(BaseModel):
    """고를 수 있는 사람 하나 — 이름과 id까지다.

    명부와 응답 모델을 나눈다. 후보 목록은 사람을 고르라고 있는 것이라 재직·계정·연락처를 말할 자리가 아니고,
    명부의 모델을 돌려쓰면 명부에 열이 하나 늘 때마다 여기에도 따라붙는다 — 값이 없으면 기본값이 거짓말을 하고
    (계정이 있는 사람을 `has_account=false`로), 값이 있으면 마스킹을 지나지 않은 채로 나간다. 둘 다 실제로
    일어났다(PR #2 F1·F2).
    """

    id: str
    display_name: str


class GrantAccessRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: str = Field(min_length=1, max_length=100)
    role_id: str = Field(min_length=1, max_length=100)
    scope_kind: Literal["unit", "organization"] = "unit"
    #: 비워 두면 그 조직 전체다. 회사 이름은 고객마다 다르므로 client가 미리 알고 있지 않는다.
    scope_ref: str | None = Field(default=None, min_length=1, max_length=100)
    include_descendants: bool = True
    reason: str = Field(min_length=1, max_length=300)


class RevokeAccessGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=300)


class SetRoleCapabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    capabilities: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class SetAssistantCharacterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_key: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=0)


class CreateTaskRequest(BaseModel):
    title: str
    description: str | None = None
    start_date: date | None = None
    due_date: date | None = None
    #: Steps someone already knows about, in the order they wrote them.
    checklist: list[str] = []
    #: Earlier work this task points at as context.
    reference_task_ids: list[UUID] = []
    #: The work this one is a part of. One level only: a subtask cannot have subtasks of its own.
    parent_task_id: UUID | None = None
    #: 어느 프로젝트의 일인가. 비어 있는 것이 정상이며, 하위 업무는 상위 업무의 프로젝트를 따른다.
    project_id: UUID | None = None


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    #: 기간은 없을 수 있다. 시작만 정해지고 끝은 아직 없는 일이 흔하다.
    starts_on: date | None = None
    ends_on: date | None = None
    external_key: str | None = Field(default=None, max_length=200)


class PlanProjectWorkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    start_date: date | None = None
    due_date: date | None = None


class AssignToProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: str = Field(min_length=1, max_length=100)
    kind: Literal["lead", "member"] = "member"
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class CreateMeetingRequest(BaseModel):
    organization_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    starts_at: datetime
    ends_at: datetime
    visibility: Literal["public", "private"] = "private"
    attendee_ids: list[str] = Field(default_factory=list)


class UpdateMeetingRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    visibility: Literal["public", "private"] | None = None


class MeetingMaterialLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    url: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=300)


class MeetingMaterialMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class MeetingShareRequest(BaseModel):
    member_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)


class MeetingRevokeShareRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CreateMeetingNoteRequest(BaseModel):
    body: str = Field(min_length=1)


class SaveMeetingNoteRequest(BaseModel):
    expected_version: int = Field(ge=1)
    body: str = Field(min_length=1)


class FinalizeMeetingNoteRequest(BaseModel):
    expected_version: int = Field(ge=1)


class StartMeetingRecordingRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=300)


class RealtimeSegmentRequest(BaseModel):
    """One settled span of live transcription. Partials never reach the server."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_label: str | None = Field(default=None, max_length=80)
    source_segment_key: str | None = Field(default=None, max_length=120)


class RealtimeSegmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[RealtimeSegmentRequest] = Field(min_length=1)


class MeetingRealtimeCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_session_duration_seconds: int = Field(default=3_600, ge=1, le=18_000)


class MeetingFollowupPromotionRequest(BaseModel):
    """Acting on a followup candidate: my own work, or a request to someone else."""

    kind: str = "task"
    title: str | None = None
    assignee_id: str | None = None


class AdoptMeetingSummaryRequest(BaseModel):
    expected_version: int = Field(ge=1)


class AssignMeetingSpeakerRequest(BaseModel):
    transcript_revision_id: UUID
    speaker_label: str = Field(min_length=1, max_length=100)
    member_id: str = Field(min_length=1, max_length=100)
    scope: Literal["segment_range", "speaker_track"]
    raw_start_source_key: str = Field(min_length=1, max_length=200)
    raw_end_source_key: str = Field(min_length=1, max_length=200)


class AssignTaskRequest(BaseModel):
    title: str
    assignee_id: str
    description: str | None = None
    start_date: date | None = None
    due_date: date | None = None
    checklist: list[str] = []
    parent_task_id: UUID | None = None


class ReassignTaskRequest(BaseModel):
    """Moving the work to someone else. Its own command, so the Task edit form never carries an assignee field."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int
    assignee_id: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=4000)


class DeclineTaskAssignmentRequest(BaseModel):
    reason: str


class UpdateTaskRequest(BaseModel):
    expected_version: int
    title: str | None = None
    description: str | None = None
    start_date: date | None = None
    due_date: date | None = None
    clear_start_date: bool = False
    clear_due_date: bool = False
    #: 이 업무를 어느 프로젝트의 것으로 둘 것인가. 떼려면 `clear_project`를 쓴다.
    project_id: UUID | None = None
    clear_project: bool = False


class MaterialFolderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["personal", "team"]
    title: str = Field(min_length=1, max_length=300)
    organization_id: str | None = None


class TaskMaterialLinkRequest(BaseModel):
    """Work that lives somewhere else: a URL and the words a person reads, never a file and never a credential."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["input", "output"]
    url: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=300)


class TaskMaterialReferenceRequest(BaseModel):
    """Another thing inside SCAX. What may be referenced is decided by the module that owns it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["input", "output"]
    resource_type: Literal["task", "meeting"]
    resource_id: UUID


class CreateConversationRequest(BaseModel):
    title: str = "새 대화"


class ConversationContextReferenceRequest(BaseModel):
    resource_type: Literal["task", "work_request"]
    resource_id: UUID
    resource_version: int = Field(ge=1)
    included: bool


class SendConversationMessageRequest(BaseModel):
    body: str
    context: list[ConversationContextReferenceRequest] = Field(default_factory=list)
    follow_up_candidate_id: UUID | None = None


class CreateWorkRequestRequest(BaseModel):
    title: str
    assignee_id: str
    description: str | None = None
    due_date: date | None = None
    cc_member_ids: list[str] = []
    #: Steps the requester already knows about. They become the accepted Task's checklist.
    checklist: list[str] = []
    #: Earlier work the requester points at as context. It travels to the Task the acceptance creates.
    reference_task_ids: list[UUID] = []


class WorkRequestDecisionRequest(BaseModel):
    expected_version: int
    reason: str | None = None


class WorkRequestNegotiationRequest(BaseModel):
    expected_version: int
    conditions: dict[str, object]


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class ChecklistItemRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    #: Optional: a caller that is showing a Task may answer the version it showed.
    expected_task_version: int | None = None


class ChecklistItemPatch(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    done: bool | None = None
    #: The step's own version, so two people editing two different steps are never in conflict.
    expected_version: int | None = None
    expected_task_version: int | None = None


class TaskCompletionReportRequest(BaseModel):
    """Handing work over: what was delivered, and which of this Task's outputs it stands on."""

    expected_version: int
    summary: str = Field(min_length=1, max_length=2000)
    output_material_ids: list[UUID] = []


class TaskReferenceRequest(BaseModel):
    """Earlier work this Task points at. One meaning only: `참고`, never a kind of causal relation."""

    referenced_task_id: UUID


class ChecklistArchiveRequest(BaseModel):
    expected_version: int | None = None
    expected_task_version: int | None = None


class ChecklistOrderRequest(BaseModel):
    """The whole order, every step exactly once. Nudging one step would let two claim the same place."""

    item_ids: list[UUID] = Field(min_length=1)
    expected_task_version: int | None = None


class ActionCommandRequest(BaseModel):
    """What a command needs from the caller; the server decides which command is available at all."""

    model_config = ConfigDict(extra="forbid")

    #: Always required: a command answers the version it was shown, so a stale write cannot slip through.
    expected_version: int
    #: Editable AX confirmations also answer the immutable Submission they were opened from.
    base_submission_version: int | None = None
    #: Raw editor values. The owning server operation normalizes and authorizes them again before executing.
    draft: dict[str, object] | None = None
    #: Expiring Action-bound material identities selected for this confirmation, separate from typed Task fields.
    attachment_draft_ids: list[UUID] | None = None
    reason: str | None = Field(default=None, max_length=4000)
    changes: dict[str, object] | None = None


class ActionMaterialLinkDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=300)


class WorkRequestAmendRequest(BaseModel):
    """What a requester may change on their own open request. Assignee and cc are relationships, not content."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int
    title: str | None = None
    description: str | None = None
    due_date: date | None = None
    clear_due_date: bool = False


class WorkRequestResubmitRequest(BaseModel):
    expected_version: int
    title: str | None = None
    description: str | None = None
    due_date: date | None = None
    clear_due_date: bool = False


class GenerateDailyReportDraftRequest(BaseModel):
    report_date: str


class EditDailyReportRequest(BaseModel):
    draft_id: str
    expected_version: int
    body: str
    include_source_refs: list[dict[str, object]] = Field(default_factory=list)
    exclude_source_refs: list[dict[str, object]] = Field(default_factory=list)


class SubmitDailyReportRequest(BaseModel):
    draft_id: str
    expected_version: int
    reason: str | None = None


class BlockTaskRequest(BaseModel):
    reason: str
    expected_version: int


class TaskTransitionRequest(BaseModel):
    expected_version: int


class ActionDecisionRequest(BaseModel):
    expected_version: int
    decision: Literal["approve", "reject"]


class ConversationCancelRequest(BaseModel):
    expected_version: int


def _runtime_error(error: Exception) -> HTTPException:
    if isinstance(error, NotificationNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ConversationQueueOverflow):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conversation_queue_full", "queue_size": error.queue_size, "limit": error.limit},
        )
    if isinstance(error, AccessNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, AccessAdministrationDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, AccessVersionConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, (AccessAdministrationError, UnknownCapability)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, GraphNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, GraphAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, GraphError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, ProjectAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, ProjectNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ProjectError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, (TaskNotFound, MaterialNotFound, MeetingNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, (TaskAccessDenied, WorkRequestAccessDenied, DailyReportAccessDenied, MeetingAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, (TaskError, InvalidTaskTransition, MeetingError)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, TranscriptionFailure):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE if error.code == "provider_unavailable" else status.HTTP_422_UNPROCESSABLE_ENTITY
        return HTTPException(status_code=status_code, detail={"code": error.code, "retryable": error.retryable})
    if isinstance(error, ActionNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ActionCenterError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, WorkRequestIdempotencyConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, WorkRequestError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, ConversationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, ActionCapabilityDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, ActionAccessDenied):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ActionError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    raise error


def create_app(
    settings: Settings | None = None,
    *,
    report_provider: AiProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_environment()
    app = FastAPI(title="SCAX Workflow Catalog API", version="0.1.0")
    if settings.developer_auth_enabled:
        app.state.developer_auth = DeveloperAuthAdapter(settings)
        app.state.workflow_application = create_workflow_application(settings, report_provider)
        app.state.auth_sessions = create_auth_session_store(settings)

        @app.get("/api/auth/providers")
        def auth_providers() -> dict[str, object]:
            """Which ways of proving who you are exist here — and, on a developer machine, the demo's own accounts.

            The shortcut list is the local demo's own accounts and the one password `reset-demo` gave them, which the
            README already prints. It is a way to skip typing, not a way to skip signing in: pressing one still posts
            the same credentials to the same login route and gets an ordinary session. Production returns neither.
            """
            answer: dict[str, object] = {"local": settings.local_login_enabled, "oidc": False}
            if settings.local_login_enabled:
                accounts = app.state.workflow_application.demo_accounts(settings.demo_email_domain)
                # Read as an organization — 대표 first, then the team — rather than in identifier order.
                order = {member.id: index for index, member in enumerate(SEEDED_MEMBERS)}
                answer["demo_accounts"] = sorted(accounts, key=lambda row: (order.get(str(row["member_id"]), len(order)), row["member_id"]))
                answer["demo_password"] = DEMO_PASSWORD
            return answer

        if settings.local_login_enabled:

            @app.post("/api/auth/login")
            def login(request: LoginRequest, response: Response) -> dict[str, object]:
                try:
                    principal = app.state.workflow_application.authenticate_with_password(
                        request.email, request.password
                    )
                except AuthenticationFailed as error:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
                session_id = app.state.auth_sessions.create(str(principal.id), "local")
                response.set_cookie(
                    SESSION_COOKIE,
                    str(session_id),
                    max_age=SESSION_MAX_AGE,
                    httponly=True,
                    samesite="lax",
                    secure=cookie_secure(settings),
                    path="/",
                )
                return app.state.workflow_application.my_organization_profile(principal)

        @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
        def logout(request: Request, response: Response) -> Response:
            session_id = session_id_from(request)
            if session_id is not None:
                app.state.auth_sessions.revoke(session_id)
            response.delete_cookie(SESSION_COOKIE, path="/")
            return Response(status_code=status.HTTP_204_NO_CONTENT, headers=dict(response.headers))

        @app.get("/api/auth/me")
        def auth_me(request: Request) -> dict[str, object]:
            principal = session_principal(request)
            if principal is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
            return app.state.workflow_application.my_organization_profile(principal)

        @app.put("/api/profile/preferences/assistant-character")
        def set_assistant_character(
            request: SetAssistantCharacterRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.set_assistant_character(
                    principal, request.character_key, request.expected_version
                )
            except UnsupportedAssistantCharacter as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
            except AssistantCharacterPreferenceConflict as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        @app.get("/api/my-work")
        def my_work(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.my_work(principal)

        @app.get("/api/meetings")
        def list_meetings(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.list_meetings(principal)

        @app.post("/api/meetings", status_code=status.HTTP_201_CREATED)
        def create_meeting(
            request: CreateMeetingRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_meeting(
                    principal,
                    organization_id=request.organization_id,
                    title=request.title,
                    description=request.description,
                    starts_at=request.starts_at,
                    ends_at=request.ends_at,
                    visibility=request.visibility,
                    attendee_ids=request.attendee_ids,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/meetings/{meeting_id}")
        def get_meeting(meeting_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.get_meeting(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/meetings/{meeting_id}")
        def update_meeting(
            meeting_id: UUID,
            request: UpdateMeetingRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            changes = request.model_dump(exclude={"expected_version"}, exclude_none=True)
            try:
                return app.state.workflow_application.update_meeting(
                    principal, meeting_id, request.expected_version, changes
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/materials/links", status_code=status.HTTP_201_CREATED)
        def attach_meeting_material_link(
            meeting_id: UUID,
            request: MeetingMaterialLinkRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.attach_meeting_material_link(
                    principal,
                    meeting_id,
                    request.expected_version,
                    url=request.url,
                    label=request.label,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/materials", status_code=status.HTTP_201_CREATED)
        async def attach_meeting_material(
            meeting_id: UUID,
            expected_version: int = Form(ge=1),
            file: UploadFile = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.attach_meeting_material(
                    principal,
                    meeting_id,
                    expected_version,
                    name=file.filename or "material",
                    content_type=file.content_type or "application/octet-stream",
                    data=await file.read(),
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/material-bindings/{binding_id}/detach")
        def detach_meeting_material(
            meeting_id: UUID,
            binding_id: UUID,
            request: MeetingMaterialMutationRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.detach_meeting_material(
                    principal, meeting_id, binding_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/material-bindings/{binding_id}/replace")
        async def replace_meeting_material(
            meeting_id: UUID,
            binding_id: UUID,
            expected_version: int = Form(ge=1),
            file: UploadFile = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.replace_meeting_material(
                    principal,
                    meeting_id,
                    binding_id,
                    expected_version,
                    name=file.filename or "material",
                    content_type=file.content_type or "application/octet-stream",
                    data=await file.read(),
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/shares")
        def share_meeting(
            meeting_id: UUID,
            request: MeetingShareRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.share_meeting(
                    principal, meeting_id, request.member_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/meetings/{meeting_id}/shares/{member_id}")
        def revoke_meeting_share(
            meeting_id: UUID,
            member_id: str,
            request: MeetingRevokeShareRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.revoke_meeting_share(
                    principal, meeting_id, member_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/notifications")
        def list_notifications(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.list_notifications(principal)

        @app.post("/api/notifications/{notification_id}/read")
        def mark_notification_read(
            notification_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.mark_notification_read(principal, notification_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/note", status_code=status.HTTP_201_CREATED)
        def create_meeting_note(
            meeting_id: UUID,
            request: CreateMeetingNoteRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_meeting_note(principal, meeting_id, request.body)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/meetings/{meeting_id}/note")
        def save_meeting_note(
            meeting_id: UUID,
            request: SaveMeetingNoteRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.save_meeting_note(
                    principal, meeting_id, request.expected_version, request.body
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/note/finalize")
        def finalize_meeting_note(
            meeting_id: UUID,
            request: FinalizeMeetingNoteRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.finalize_meeting_note(
                    principal, meeting_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/recordings/start", status_code=status.HTTP_201_CREATED)
        def start_meeting_recording(
            meeting_id: UUID,
            request: StartMeetingRecordingRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.start_meeting_recording(
                    principal, meeting_id, request.purpose
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/recordings/{recording_id}/stop")
        async def stop_meeting_recording(
            meeting_id: UUID,
            recording_id: UUID,
            expected_version: int = Form(ge=1),
            audio: UploadFile = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                data = await audio.read()
                return app.state.workflow_application.stop_meeting_recording(
                    principal,
                    meeting_id,
                    recording_id,
                    expected_version,
                    original_name=audio.filename or "recording",
                    content_type=audio.content_type or "application/octet-stream",
                    data=data,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/recordings/{recording_id}/realtime-segments")
        def append_meeting_live_transcript(
            meeting_id: UUID,
            recording_id: UUID,
            request: RealtimeSegmentsRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            segments = [
                FinalTranscriptSegment(
                    segment.source_segment_key or f"realtime:{index}:{segment.start_ms}",
                    segment.start_ms,
                    segment.end_ms,
                    segment.text,
                    segment.speaker_label,
                )
                for index, segment in enumerate(request.segments, start=1)
            ]
            try:
                return app.state.workflow_application.append_meeting_live_transcript(
                    principal, meeting_id, recording_id, segments
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/recordings/{recording_id}/realtime-credential")
        def meeting_realtime_credential(
            meeting_id: UUID,
            recording_id: UUID,
            request: MeetingRealtimeCredentialRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.meeting_realtime_credential(
                    principal,
                    meeting_id,
                    recording_id,
                    request.max_session_duration_seconds,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/summaries/{summary_id}/statements/{statement_index}/promote")
        def promote_meeting_followup(
            meeting_id: UUID,
            summary_id: UUID,
            statement_index: int,
            request: MeetingFollowupPromotionRequest,
            response: Response,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                result = app.state.workflow_application.promote_meeting_followup(
                    principal, meeting_id, summary_id, statement_index,
                    kind=request.kind, title=request.title, assignee_id=request.assignee_id,
                )
            except Exception as error:
                raise _runtime_error(error) from error
            # A second press is a receipt for the work that already exists, not a second piece of work.
            response.status_code = status.HTTP_200_OK if result["already_promoted"] else status.HTTP_201_CREATED
            return result

        @app.post("/api/meetings/{meeting_id}/summaries/{summary_id}/adopt")
        def adopt_meeting_summary(
            meeting_id: UUID,
            summary_id: UUID,
            request: AdoptMeetingSummaryRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.adopt_meeting_summary(
                    principal,
                    meeting_id,
                    summary_id,
                    request.expected_version,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/speaker-assignments", status_code=status.HTTP_201_CREATED)
        def assign_meeting_speaker(
            meeting_id: UUID,
            request: AssignMeetingSpeakerRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.assign_meeting_speaker_identity(
                    principal,
                    meeting_id,
                    request.transcript_revision_id,
                    speaker_label=request.speaker_label,
                    member_id=request.member_id,
                    scope=request.scope,
                    raw_start_source_key=request.raw_start_source_key,
                    raw_end_source_key=request.raw_end_source_key,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/conversations")
        def conversations(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.conversations(principal)

        @app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
        def create_conversation(request: CreateConversationRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return app.state.workflow_application.create_conversation(principal, request.title)

        @app.get("/api/conversations/{conversation_id}")
        def conversation(conversation_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.conversation(principal, conversation_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/conversations/{conversation_id}/messages", status_code=status.HTTP_202_ACCEPTED)
        def send_conversation_message(conversation_id: UUID, request: SendConversationMessageRequest, principal: Principal = Depends(developer_principal), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, object]:
            try:
                return app.state.workflow_application.accept_conversation_message(
                    principal,
                    request.body,
                    conversation_id,
                    request.context,
                    idempotency_key,
                    request.follow_up_candidate_id,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/conversations/{conversation_id}/cancel")
        def cancel_conversation(
            conversation_id: UUID,
            request: ConversationCancelRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.cancel_conversation_turn(
                    principal, conversation_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/conversations/{conversation_id}/turns/{turn_id}/retry", status_code=status.HTTP_202_ACCEPTED)
        def retry_conversation_turn(conversation_id: UUID, turn_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.retry_conversation_turn(principal, conversation_id, turn_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/actions")
        def actions(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.actions(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/actions/{action_id}/decide")
        def decide_action(
            action_id: UUID,
            request: ActionDecisionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.decide_action(
                    principal,
                    action_id,
                    request.expected_version,
                    request.decision,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/organization/members", response_model=list[MemberResponse])
        def organization_members(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.member_directory(principal)

        @app.get("/api/organization/members/{member_id}")
        def organization_member_detail(
            member_id: str, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.organization_member_detail(principal, member_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/organization/members/{member_id}/history")
        def organization_member_history(
            member_id: str,
            axis: str = Query(description="membership · appointment · grade · job · grant"),
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.organization_member_history(principal, member_id, axis)
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/organization/activity")
        def organization_activity(
            unit_id: str | None = None,
            limit: int = Query(default=50, ge=1, le=200),
            cursor: str | None = None,
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.organization_activity(
                    principal, unit_id=unit_id, limit=limit, cursor=cursor
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/access/roles")
        def installed_access_roles(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.installed_access_roles(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/access/members/{member_id}")
        def member_access(member_id: str, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.member_access(principal, member_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/access/grants", status_code=status.HTTP_201_CREATED)
        def grant_access_role(
            request: GrantAccessRoleRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.grant_access_role(
                    principal,
                    member_id=request.member_id,
                    role_id=request.role_id,
                    scope_kind=request.scope_kind,
                    scope_ref=request.scope_ref,
                    include_descendants=request.include_descendants,
                    reason=request.reason,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/access/grants/{grant_id}/revoke")
        def revoke_access_grant(
            grant_id: UUID, request: RevokeAccessGrantRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.revoke_access_grant(principal, grant_id, reason=request.reason)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/access/roles/{role_id}")
        def set_role_capabilities(
            role_id: str, request: SetRoleCapabilitiesRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.set_role_capabilities(
                    principal,
                    role_id,
                    request.capabilities,
                    expected_version=request.expected_version,
                    reason=request.reason,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/organization/tree")
        def organization_tree(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.organization_tree(principal)

        @app.get("/api/organization/units/{unit_id}/members")
        def organization_unit_members(unit_id: str, principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            return app.state.workflow_application.organization_unit_members(principal, unit_id)

        @app.get("/api/organization/me")
        def my_organization_profile(principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return app.state.workflow_application.my_organization_profile(principal)

        @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
        def create_self_task(request: CreateTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_self_task(
                    principal,
                    request.title,
                    description=request.description,
                    start_date=request.start_date,
                    due_date=request.due_date,
                    checklist=request.checklist,
                    reference_task_ids=request.reference_task_ids,
                    parent_task_id=request.parent_task_id,
                    project_id=request.project_id,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        # ---- 프로젝트: 조직 단위와 나란한 두 번째 축 ----

        @app.get("/api/projects")
        def list_projects(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.list_projects(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/projects", status_code=status.HTTP_201_CREATED)
        def create_project(request: CreateProjectRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_project(
                    principal,
                    name=request.name,
                    description=request.description,
                    starts_on=request.starts_on,
                    ends_on=request.ends_on,
                    external_key=request.external_key,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/projects/{project_id}")
        def get_project(project_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.get_project(principal, project_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
        def plan_project_work(
            project_id: UUID,
            request: PlanProjectWorkRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            """프로젝트 계획에 일을 올린다. 사람은 아직 정하지 않는다."""
            try:
                return app.state.workflow_application.plan_project_work(
                    principal,
                    project_id,
                    request.title,
                    description=request.description,
                    start_date=request.start_date,
                    due_date=request.due_date,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/projects/{project_id}/members", status_code=status.HTTP_201_CREATED)
        def assign_to_project(
            project_id: UUID,
            request: AssignToProjectRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.assign_to_project(
                    principal,
                    project_id,
                    request.member_id,
                    kind=request.kind,
                    valid_from=request.valid_from,
                    valid_until=request.valid_until,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/projects/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
        def release_from_project(
            project_id: UUID,
            member_id: str,
            principal: Principal = Depends(developer_principal),
        ) -> Response:
            try:
                app.state.workflow_application.release_from_project(principal, project_id, member_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.post("/api/tasks/assign", status_code=status.HTTP_201_CREATED)
        def assign_task(request: AssignTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.assign_task(
                    principal, request.title, request.assignee_id,
                    description=request.description, start_date=request.start_date, due_date=request.due_date,
                    checklist=request.checklist, parent_task_id=request.parent_task_id,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/task-assignment-candidates", response_model=list[CandidateResponse])
        def task_assignment_candidates(principal: Principal = Depends(developer_principal)) -> list[CandidateResponse]:
            try:
                return [CandidateResponse(**candidate) for candidate in app.state.workflow_application.task_assignment_candidates(principal)]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/task-assignments/sent")
        def sent_task_assignments(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.sent_task_assignments(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/task-assignments/{assignment_id}/accept")
        def accept_task_assignment(assignment_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.accept_task_assignment(principal, assignment_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/task-assignments/{assignment_id}/decline")
        def decline_task_assignment(
            assignment_id: UUID, request: DeclineTaskAssignmentRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.decline_task_assignment(principal, assignment_id, request.reason)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/tasks/{task_id}")
        def update_task(task_id: UUID, request: UpdateTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            changes: dict[str, object] = {}
            if request.title is not None:
                changes["title"] = request.title
            if request.description is not None:
                changes["description"] = request.description
            if request.start_date is not None or request.clear_start_date:
                changes["start_date"] = None if request.clear_start_date else request.start_date
            if request.due_date is not None or request.clear_due_date:
                changes["due_date"] = None if request.clear_due_date else request.due_date
            if request.project_id is not None or request.clear_project:
                changes["project_id"] = None if request.clear_project else request.project_id
            try:
                return app.state.workflow_application.update_task(principal, task_id, request.expected_version, changes)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/meetings/{meeting_id}/materials/{material_id}/content")
        def open_meeting_material(meeting_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                view, data = app.state.workflow_application.open_meeting_material(principal, meeting_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(content=data, media_type=str(view["content_type"]),
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(str(view['name']))}"})

        @app.get("/api/daily-reports/{report_id}/materials/{material_id}/content")
        def open_report_material(report_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                view, data = app.state.workflow_application.open_report_material(principal, report_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(content=data, media_type=str(view["content_type"]),
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(str(view['name']))}"})

        @app.get("/api/material-folders")
        def list_material_folders(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.list_material_folders(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/material-folders", status_code=status.HTTP_201_CREATED)
        def create_material_folder(request: MaterialFolderCreateRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_material_folder(principal, **request.model_dump())
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/material-folders/{folder_id}/materials")
        def list_folder_materials(folder_id: UUID, principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.list_folder_materials(principal, folder_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/material-folders/{folder_id}/materials", status_code=status.HTTP_201_CREATED)
        async def upload_folder_material(folder_id: UUID, file: UploadFile = File(...), principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            data = await file.read()
            try:
                return app.state.workflow_application.upload_folder_material(principal, folder_id, name=file.filename or "material",
                                                                            content_type=file.content_type or "application/octet-stream", data=data)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/material-folders/{folder_id}/materials/{material_id}/content")
        def open_folder_material(folder_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                view, data = app.state.workflow_application.open_folder_material(principal, folder_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(content=data, media_type=str(view["content_type"]),
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(str(view['name']))}"})

        @app.post("/api/material-folders/{folder_id}/materials/{material_id}/detach")
        def detach_folder_material(folder_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.detach_folder_material(principal, folder_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/material-folders/{folder_id}/archive")
        def archive_material_folder(folder_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.archive_material_folder(principal, folder_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/materials")
        def list_task_materials(task_id: UUID, principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.list_task_materials(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/materials", status_code=status.HTTP_201_CREATED)
        async def attach_task_material(
            task_id: UUID,
            kind: str = Form(...),
            file: UploadFile = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            data = await file.read()
            try:
                return app.state.workflow_application.attach_task_material(
                    principal,
                    task_id,
                    kind=kind,
                    name=file.filename or "material",
                    content_type=file.content_type or "application/octet-stream",
                    data=data,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/completion-report")
        def submit_task_completion(
            task_id: UUID, request: TaskCompletionReportRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.submit_task_completion(
                    principal, task_id, request.expected_version,
                    summary=request.summary, output_material_ids=request.output_material_ids,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/references", status_code=status.HTTP_201_CREATED)
        def add_task_reference(
            task_id: UUID, request: TaskReferenceRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.add_task_reference(principal, task_id, request.referenced_task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/tasks/{task_id}/references/{reference_id}")
        def release_task_reference(
            task_id: UUID, reference_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.release_task_reference(principal, task_id, reference_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/graph/overview")
        def graph_overview(
            view: Literal["member", "team", "project"] = "member",
            limit: int = 120,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.graph_overview(principal, view=view, limit=limit)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/graph/search")
        def graph_search(q: str, limit: int = 20, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.graph_search(principal, q, limit)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/graph/neighbors")
        def graph_neighbors(node: str, limit: int = 20, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.graph_neighbors(principal, node, limit)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/history")
        def task_history(task_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.task_history(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/history/diff")
        def task_history_diff(
            task_id: UUID,
            from_version: int = Query(alias="from", ge=1),
            to_version: int = Query(alias="to", ge=1),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.task_history_diff(principal, task_id, from_version, to_version)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/reassign")
        def reassign_task(
            task_id: UUID,
            request: ReassignTaskRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.reassign_task(
                    principal, task_id, request.expected_version, request.assignee_id, request.reason
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/materials/links", status_code=status.HTTP_201_CREATED)
        def attach_task_material_link(
            task_id: UUID,
            request: TaskMaterialLinkRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.attach_task_material_link(
                    principal, task_id, kind=request.kind, url=request.url, label=request.label
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/materials/references", status_code=status.HTTP_201_CREATED)
        def attach_task_material_reference(
            task_id: UUID,
            request: TaskMaterialReferenceRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.attach_task_material_reference(
                    principal, task_id, kind=request.kind, resource_type=request.resource_type, resource_id=str(request.resource_id)
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/materials/search")
        def search_materials(
            q: str, limit: int = 5,
            resource_types: list[MaterialResourceType] | None = Query(None),
            resource_type: MaterialResourceType | None = None, resource_id: str | None = None,
            material_id: UUID | None = None, registered_from: date | None = None, registered_until: date | None = None,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.search_materials(principal, q, limit=limit, resource_types=resource_types,
                    resource_type=resource_type, resource_id=resource_id, material_id=material_id,
                    registered_from=registered_from, registered_until=registered_until)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/materials/{material_id}")
        def material_metadata(material_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.material_metadata(principal, material_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/materials/{material_id}/content")
        def task_material_content(task_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                view, data = app.state.workflow_application.open_task_material(principal, task_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            from urllib.parse import quote

            return Response(
                content=data,
                media_type=str(view["content_type"]),
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(str(view['name']))}"},
            )

        @app.post("/api/tasks/{task_id}/material-bindings/{binding_id}/detach")
        def detach_task_material(task_id: UUID, binding_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.detach_task_material(principal, task_id, binding_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks")
        def list_tasks(
            include_closed: bool = False,
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.list_tasks(principal, include_closed=include_closed)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}")
        def get_task(task_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.get_task(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests", status_code=status.HTTP_201_CREATED)
        def create_work_request(
            request: CreateWorkRequestRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_work_request(
                    principal, request.title, request.assignee_id,
                    description=request.description, due_date=request.due_date, cc_member_ids=request.cc_member_ids,
                    checklist=request.checklist, reference_task_ids=request.reference_task_ids,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-requests")
        def list_work_requests(
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.list_work_requests(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-requests/{request_id}")
        def get_work_request(
            request_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.get_work_request(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/resubmit")
        def resubmit_work_request(
            request_id: UUID,
            request: WorkRequestResubmitRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.resubmit_work_request(
                    principal, request_id, request.expected_version,
                    title=request.title, description=request.description, due_date=request.due_date, clear_due_date=request.clear_due_date,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/amend")
        def amend_work_request(
            request_id: UUID,
            request: WorkRequestAmendRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.amend_work_request(
                    principal, request_id, request.expected_version,
                    title=request.title, description=request.description, due_date=request.due_date, clear_due_date=request.clear_due_date,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/comments", status_code=status.HTTP_201_CREATED)
        def add_work_request_comment(
            request_id: UUID,
            request: CommentRequest,
            principal: Principal = Depends(developer_principal),
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.add_work_request_comment(principal, request_id, request.body, idempotency_key)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/checklist", status_code=status.HTTP_201_CREATED)
        def add_task_checklist_item(task_id: UUID, request: ChecklistItemRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.add_task_checklist_item(
                    principal, task_id, request.text, request.expected_task_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/tasks/{task_id}/checklist/{item_id}")
        def update_task_checklist_item(
            task_id: UUID, item_id: UUID, request: ChecklistItemPatch, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.update_task_checklist_item(
                    principal, task_id, item_id, **request.model_dump(exclude_none=True)
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/checklist/order")
        def reorder_task_checklist(
            task_id: UUID, request: ChecklistOrderRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.reorder_task_checklist(
                    principal, task_id, request.item_ids, expected_task_version=request.expected_task_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/tasks/{task_id}/checklist/{item_id}")
        def archive_task_checklist_item(
            task_id: UUID,
            item_id: UUID,
            expected_version: int | None = None,
            expected_task_version: int | None = None,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            # Taking a step off the list moves the Task, so the answer carries the version it moved to.
            try:
                return app.state.workflow_application.archive_task_checklist_item(
                    principal, task_id, item_id,
                    expected_version=expected_version, expected_task_version=expected_task_version,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/action-items")
        def pending_action_items(principal: Principal = Depends(developer_principal)) -> list[dict[str, object]]:
            try:
                return app.state.workflow_application.pending_action_items(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/action-items/{action_item_id}")
        def action_item_detail(action_item_id: str, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.action_item_detail(principal, action_item_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/action-items/{action_item_id}/material-drafts/links", status_code=status.HTTP_201_CREATED)
        def stage_action_material_link(
            action_item_id: UUID,
            request: ActionMaterialLinkDraftRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.stage_action_material_link(
                    principal, action_item_id, url=request.url, label=request.label
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/action-items/{action_item_id}/material-drafts/files", status_code=status.HTTP_201_CREATED)
        async def stage_action_material_file(
            action_item_id: UUID,
            file: UploadFile = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.stage_action_material_file(
                    principal,
                    action_item_id,
                    name=file.filename or "material",
                    content_type=file.content_type or "application/octet-stream",
                    data=await file.read(),
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/action-items/{action_item_id}/material-drafts/{material_draft_id}/discard")
        def discard_action_material_draft(
            action_item_id: UUID,
            material_draft_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.discard_action_material_draft(
                    principal, action_item_id, material_draft_id
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/action-items/{action_item_id}/commands/{command}")
        def run_action_command(
            action_item_id: str, command: str, request: ActionCommandRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.run_action_command(principal, action_item_id, command, request.model_dump(exclude_none=True))
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-request-cc-candidates", response_model=list[CandidateResponse])
        def work_request_cc_candidates(principal: Principal = Depends(developer_principal)) -> list[CandidateResponse]:
            try:
                return [CandidateResponse(**candidate) for candidate in app.state.workflow_application.work_request_cc_candidates(principal)]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/comments/{comment_id}/attachments", status_code=status.HTTP_201_CREATED)
        async def attach_to_work_request_comment(
            request_id: UUID, comment_id: UUID, file: UploadFile = File(...), principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            data = await file.read()
            try:
                return app.state.workflow_application.attach_to_work_request_comment(
                    principal, request_id, comment_id,
                    name=file.filename or "attachment", content_type=file.content_type or "application/octet-stream", data=data,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/evidence", status_code=status.HTTP_201_CREATED)
        async def add_work_request_evidence(
            request_id: UUID, file: UploadFile = File(...), principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            data = await file.read()
            try:
                return app.state.workflow_application.add_work_request_evidence(
                    principal, request_id,
                    name=file.filename or "evidence", content_type=file.content_type or "application/octet-stream", data=data,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-requests/{request_id}/attachments/{attachment_id}/content")
        def work_request_attachment_content(request_id: UUID, attachment_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                view, data = app.state.workflow_application.open_work_request_attachment(principal, request_id, attachment_id)
            except Exception as error:
                raise _runtime_error(error) from error
            filename = quote(str(view["name"]))
            return Response(
                content=data,
                media_type=str(view["content_type"]),
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
            )

        @app.get("/api/work-requests/{request_id}/timeline")
        def work_request_timeline(request_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.work_request_timeline(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-request-assignee-candidates", response_model=list[CandidateResponse])
        def work_request_assignee_candidates(
            principal: Principal = Depends(developer_principal),
        ) -> list[CandidateResponse]:
            try:
                candidates = app.state.workflow_application.work_request_assignee_candidates(principal)
                return [CandidateResponse(**candidate) for candidate in candidates]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/accept")
        def accept_work_request(
            request_id: UUID,
            request: WorkRequestDecisionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.accept_work_request(
                    principal, request_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/reject")
        def reject_work_request(
            request_id: UUID,
            request: WorkRequestDecisionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.reject_work_request(
                    principal, request_id, request.expected_version, request.reason or ""
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/negotiate")
        def negotiate_work_request(
            request_id: UUID,
            request: WorkRequestNegotiationRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.negotiate_work_request(
                    principal, request_id, request.expected_version, request.conditions
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/daily-reports/generate-draft", status_code=status.HTTP_201_CREATED)
        def generate_daily_report_draft(request: GenerateDailyReportDraftRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.generate_daily_report_draft(principal, request.report_date)
            except DailyReportAccessDenied as error:
                raise _runtime_error(error) from error
            except ProviderFailure as error:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.get("/api/daily-reports/status")
        def daily_report_status(
            report_date: str = Query(),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.daily_report_status(principal, report_date)
            except DailyReportAccessDenied as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.post("/api/daily-reports/{report_id}/edit")
        def edit_daily_report(
            report_id: str,
            request: EditDailyReportRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.edit_daily_report(
                    principal,
                    report_id,
                    request.draft_id,
                    request.expected_version,
                    request.body,
                    request.include_source_refs,
                    request.exclude_source_refs,
                )
            except DailyReportAccessDenied as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.post("/api/daily-reports/{report_id}/submit", status_code=status.HTTP_201_CREATED)
        def submit_daily_report(
            report_id: str,
            request: SubmitDailyReportRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.submit_daily_report(
                    principal,
                    report_id,
                    request.draft_id,
                    request.expected_version,
                    request.reason,
                )
            except DailyReportAccessDenied as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.get("/api/daily-reports/{report_id}/history")
        def daily_report_history(
            report_id: str,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.daily_report_history(principal, report_id)
            except DailyReportAccessDenied as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        def task_transition(task_id: UUID, target: TaskState, principal: Principal, reason: str | None = None, expected_version: int = 0) -> dict[str, object]:
            try:
                return app.state.workflow_application.transition_task(task_id, principal, target, reason, expected_version)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/start")
        def start_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.IN_PROGRESS, principal, expected_version=request.expected_version)

        @app.post("/api/tasks/{task_id}/block")
        def block_task(task_id: UUID, request: BlockTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.BLOCKED, principal, request.reason, request.expected_version)

        @app.post("/api/tasks/{task_id}/resume")
        def resume_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.IN_PROGRESS, principal, expected_version=request.expected_version)

        @app.post("/api/tasks/{task_id}/complete")
        def complete_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.DONE, principal, expected_version=request.expected_version)

        @app.post("/api/tasks/{task_id}/cancel")
        def cancel_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            return task_transition(task_id, TaskState.CANCELLED, principal, expected_version=request.expected_version)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "profile": settings.profile}

    return app


app = create_app()
