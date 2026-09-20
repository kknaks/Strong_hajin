from __future__ import annotations

from datetime import date, datetime
import asyncio
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_workspace.modules.ax_execution.browser_interactions import (
    BrowserFileRequest,
    BrowserInteractionConflict,
    BrowserInteractionResult,
    BrowserInterruptionInput,
    BrowserRecordingRequest,
    BrowserRecordingStartInput,
)
from ax_workspace.modules.ax_execution.conversation_commands import ConversationRetryResult
from ax_workspace.modules.ax_execution.conversation_results import ConversationView
from ax_workspace.modules.ax_execution.result_contracts import ActionMaterialDraftView, ActionProposalResult
from ax_workspace.modules.actions.results import ActionDetailResult, ActionDiscussionView, ActionEnvelopeResult
from ax_workspace.modules.organization_access.commands import AssistantCharacterResult
from ax_workspace.modules.organization_access.results import (
    InstalledRoleView,
    MemberAccessView,
    MemberAxisHistoryView,
    MemberCandidateView,
    MemberDetailView,
    MemberDirectoryView,
    MyOrganizationProfileView,
    OrganizationActivityView,
    OrganizationUnitView,
    UnitMemberView,
)
from ax_workspace.modules.reports.results import ReportHistoryResult, ReportStatusResult
from ax_workspace.modules.work.folder_commands import FolderArchiveResult, FolderDetachResult, FolderView
from ax_workspace.modules.work.graph_results import GraphNeighborsResult, GraphOverviewResult, GraphSearchResult
from ax_workspace.modules.work.material_query_results import FolderMaterialView, MaterialMetadataResult, MaterialSearchResult
from ax_workspace.modules.work.material_results import TaskMaterialResult, TaskMaterialView
from ax_workspace.modules.work.project_results import ProjectAssignmentView, ProjectDetailResult, ProjectParticipationView, ProjectView
from ax_workspace.modules.work.request_results import WorkRequestReadReceiptResult, WorkRequestDetailResult, WorkRequestEvidenceResult, WorkRequestHistoryResult
from ax_workspace.modules.work.task_results import (
    TaskCompletionResult,
    TaskDetailResult,
    TaskHistoryDiffResult,
    TaskHistoryResult,
    TaskListEntry,
    TaskDateMutationResult,
    TaskReferenceReleaseResult,
    TaskReferenceResult,
    TaskScheduleView,
)

from ax_workspace.modules.work.request_results import (
    WorkRequestInboxEntry,
    WorkRequestMaterialResult,
    WorkRequestMaterialView,
    WorkRequestMutationResult,
)
from ax_workspace.modules.reports.commands import ReportEditInput as EditDailyReportRequest, ReportSubmitInput as SubmitDailyReportRequest
from ax_workspace.modules.reports.results import ReportDraftResult, ReportSubmissionResult
from ax_workspace.modules.work.task_creation import TaskCreateInput as CreateTaskRequest, TaskAssignmentInput as AssignTaskRequest
from ax_workspace.modules.work.material_commands import ActionMaterialLinkInput as ActionMaterialLinkDraftRequest
from ax_workspace.modules.work.material_commands import TaskMaterialLinkInput as TaskMaterialLinkRequest, TaskMaterialReferenceInput as TaskMaterialReferenceRequest
from ax_workspace.modules.work.checklist_commands import ChecklistAddInput as ChecklistItemRequest, ChecklistUpdateInput as ChecklistItemPatch, ChecklistOrderInput as ChecklistOrderRequest
from ax_workspace.modules.work.task_results import TaskAssignmentResult, ChecklistMutationResult, ChecklistOrderResult, TaskMutationResult
from ax_workspace.modules.work.task_commands import TaskVersionInput as TaskTransitionRequest, TaskBlockInput as BlockTaskRequest, TaskCancelInput as CancelTaskRequest
from ax_workspace.modules.work.task_commands import TaskReassignInput as ReassignTaskRequest, TaskUpdateInput as UpdateTaskRequest
from ax_workspace.modules.work.task_commands import (
    TaskScheduleCreateInput as CreateTaskScheduleRequest,
    TaskScheduleRetimeInput as RetimeTaskScheduleRequest,
)
from ax_workspace.modules.work.task_commands import TaskCompletionInput as TaskCompletionReportRequest, TaskReferenceInput as TaskReferenceRequest
from ax_workspace.modules.work.folder_commands import FolderCreateInput as MaterialFolderCreateRequest
from ax_workspace.modules.work.project_commands import (
    ProjectCreateInput as CreateProjectRequest,
    ProjectMemberInput as AssignToProjectRequest,
    ProjectWorkInput as PlanProjectWorkRequest,
    ProjectReleaseInput as ReleaseFromProjectRequest,
)

from ax_workspace.modules.meetings.commands import (
    MeetingAgendaDraftInput as CreateAgendaRequest,
    MeetingAgendaPatch as UpdateAgendaRequest,
    MeetingInfoPatch as UpdateMeetingRequest,
    MeetingMemoInput as WriteMemoRequest,
    MeetingReservationInput as CreateMeetingRequest,
    MeetingShareManyInput as ShareMeetingRequest,
    MeetingTodoPromotionInput as PromoteTodoRequest,
)

from ax_workspace.modules.errors import ResourceNotFound, RESOURCE_NOT_FOUND_MESSAGE
from ax_workspace.modules.reports.application import DailyReportNotFound
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
    connection_principal,
    cookie_secure,
    developer_principal,
    session_id_from,
    session_principal,
)
from ax_workspace.bootstrap.application import create_auth_session_store, create_workflow_application
from ax_workspace.modules.work.application import InvalidTaskTransition, TaskAccessDenied, TaskError, TaskNotFound, TaskState
from ax_workspace.modules.work.errors import (
    TaskApproverLocked,
    TaskAssignmentProposalExists,
    TaskAssignmentResponderOnly,
    TaskCancelRequiresAgreement,
    TaskChildrenUnfinished,
    TaskDirectNesting,
    TaskIdempotencyConflict,
    TaskIdempotencyKeyRequired,
    TaskParentClosed,
    TaskParentCycle,
    TaskParentUnassigned,
    TaskPredecessorsUnfinished,
    TaskProjectLockedByPredecessors,
    TaskProposalNotPending,
    TaskProposalResponderOnly,
    TaskRecipientNotAllowed,
    TaskReopenForbidden,
    TaskReopenParentDone,
    TaskScheduleDayTaken,
    TaskScheduleTaskClosed,
    TaskScheduleVersionConflict,
)
from ax_workspace.modules.work.graph import GraphAccessDenied, GraphError, GraphNotFound
from ax_workspace.modules.work.materials import MaterialNotFound
from ax_workspace.modules.actions.domain import ActionError as ActionCenterError, ActionNotFound
from ax_workspace.modules.work.request_errors import WorkRejectReasonRequired, WorkRequestLockedAfterAccept, WorkRequestNotPending
from ax_workspace.modules.work.requests import WorkRequestAccessDenied, WorkRequestError, WorkRequestIdempotencyConflict
from ax_workspace.modules.reports.application import DailyReportAccessDenied
from ax_workspace.modules.meetings.materials import inline_media_type
from ax_workspace.modules.meetings.rooms import (
    RoomBookingRefused,
    RoomCreationIdempotencyConflict,
    RoomCreationIdempotencyRequired,
)
from ax_workspace.modules.meetings.domain import (
    MeetingAccessDenied,
    MeetingError,
    MeetingNotFound,
    MeetingStaleWrite,
    MeetingStateConflict,
    MeetingVersionConflict,
)
from ax_workspace.modules.meetings.materials import MaterialUpload, MeetingMaterialsRejected
from ax_workspace.modules.meetings.stream import (
    AUTH_TIMEOUT_SECONDS,
    CLOSE_UNAUTHORIZED,
    ERROR_CODE_DISCONNECTED,
    REASON_UNAUTHORIZED,
    ROLE_SUBSCRIBE,
    ROLE_UPSTREAM,
    AgendaAddedFrame,
    AiBatchFrame,
    MemoLineFrame,
    MemoLineRemovedFrame,
    MemoLineUpdatedFrame,
    AgendaRemovedFrame,
    AgendaUpdatedFrame,
    AudioDeclaration,
    AudioFrame,
    ClientGone,
    IgnoredFrame,
    InboundFrame,
    OutboundFrame,
    ReadyFrame,
    StreamClientClosed,
    StreamErrorFrame,
    TranscriptFinalFrame,
    TranscriptPartialFrame,
)
from ax_workspace.modules.work.projects import ProjectAccessDenied, ProjectError, ProjectNotFound
from ax_workspace.modules.ax_execution.conversations import DEFAULT_CONVERSATION_LIST_LIMIT, ConversationError, ConversationQueueOverflow
from ax_workspace.modules.ax_execution.actions import ActionAccessDenied, ActionCapabilityDenied, ActionError
from ax_workspace.bootstrap.seed import DEMO_PASSWORD, SEEDED_MEMBERS
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.ai import AiProvider
from ax_workspace.modules.ax_execution.conversation_commands import (ConversationCreateInput as CreateConversationRequest, ConversationMessageInput as SendConversationMessageRequest, ConversationCancelInput as ConversationCancelRequest)
from ax_workspace.modules.notifications import NotificationNotFound
from ax_workspace.modules.organization_access.commands import AssistantCharacterInput as SetAssistantCharacterRequest
from ax_workspace.modules.work.assignment_commands import AssignmentDeclineInput as DeclineTaskAssignmentRequest
from ax_workspace.modules.work.request_commands import TaskVersionInput as TaskVersionRequest, TaskProposalInput as TaskProposalRequest, TaskProposalResponseInput as TaskProposalResponseRequest, TaskReopenInput as TaskReopenRequest, WorkRequestVersionInput as WorkRequestVersionRequest, WorkRequestDecisionInput as WorkRequestDecisionRequest, WorkRequestNegotiationInput as WorkRequestNegotiationRequest, WorkRequestRevisionInput as WorkRequestAmendRequest, WorkRequestRevisionInput as WorkRequestResubmitRequest, WorkRequestCreateInput as CreateWorkRequestRequest, WorkRequestCommentInput as CommentRequest, WorkRequestMaterialLinkInput as WorkRequestMaterialLinkRequest


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














class StreamAudioDeclaration(BaseModel):
    """`auth.audio` — 업스트림이 받는 형식 중 클라이언트가 고른 값. 서버가 provider config 에 그대로 옮긴다."""

    model_config = ConfigDict(extra="forbid")
    format: str = Field(min_length=1, max_length=40)
    sample_rate: int = Field(default=16_000, ge=8_000, le=48_000, alias="sampleRate")
    channels: int = Field(default=1, ge=1, le=2)


class StreamAuthFrame(BaseModel):
    """연결 후 **첫 프레임**, 5초 안에. 토큰을 싣지 않는다 — 인증은 핸드셰이크의 세션 쿠키가 한다.

    `accessToken` 이 와도 무시한다(`extra` 를 막지 않는 유일한 프레임인 이유다).
    """

    type: Literal["auth"]
    role: Literal["upstream", "subscribe"]
    audio: StreamAudioDeclaration | None = None


def _stream_message(frame: OutboundFrame) -> dict[str, object]:
    """내부 프레임 → 계약 JSON. **dto 와 wire 의 유일한 접점**이고 키는 camelCase 다."""
    if isinstance(frame, ReadyFrame):
        return {
            "type": "ready",
            "meetingStartedAt": frame.meeting_started_at.isoformat(),
            "latestBatchSeq": frame.latest_batch_seq,
            "speakerCount": frame.speaker_count,
        }
    if isinstance(frame, TranscriptPartialFrame):
        return {
            "type": "transcript.partial",
            "segments": [
                {"speakerLabel": segment.speaker_label, "atMs": segment.at_ms, "text": segment.text}
                for segment in frame.segments
            ],
        }
    if isinstance(frame, TranscriptFinalFrame):
        item = frame.item
        return {
            "type": "transcript.final",
            "item": {
                "id": item.id,
                "speakerLabel": item.speaker_label,
                "atMs": item.at_ms,
                "endMs": item.end_ms,
                "content": item.content,
            },
        }
    if isinstance(frame, AiBatchFrame):
        return {"type": "ai.batch", "seq": frame.seq, "agendas": list(frame.agendas)}
    if isinstance(frame, MemoLineFrame):
        return {"type": "memo.line", "agendaId": frame.agenda_id, "line": dict(frame.line)}
    # 아래 넷은 **id 로 제자리를 짚는다** — 같은 프레임을 두 번 적용해도 결과가 같아야 한다 (에코 처리).
    if isinstance(frame, MemoLineUpdatedFrame):
        return {"type": "memo.line.updated", "agendaId": frame.agenda_id, "line": dict(frame.line)}
    if isinstance(frame, MemoLineRemovedFrame):
        return {"type": "memo.line.removed", "agendaId": frame.agenda_id, "lineId": frame.line_id}
    if isinstance(frame, AgendaAddedFrame):
        return {"type": "agenda.added", "agenda": dict(frame.agenda)}
    if isinstance(frame, AgendaUpdatedFrame):
        return {"type": "agenda.updated", "agenda": dict(frame.agenda)}
    if isinstance(frame, AgendaRemovedFrame):
        return {"type": "agenda.removed", "agendaId": frame.agenda_id}
    if isinstance(frame, StreamErrorFrame):
        return {"type": "error", "code": ERROR_CODE_DISCONNECTED, "reason": frame.reason}
    raise TypeError(f"알 수 없는 프레임: {type(frame).__name__}")


class _WebSocketStreamClient:
    """`StreamClient` 포트를 Starlette WebSocket 으로 구현한다 — JSON 변환은 여기서만 일어난다."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def receive(self) -> InboundFrame:
        try:
            message = await self._websocket.receive()
        except (WebSocketDisconnect, RuntimeError):
            return ClientGone()
        if message["type"] == "websocket.disconnect":
            return ClientGone()
        if message.get("bytes") is not None:
            return AudioFrame(chunk=message["bytes"])
        # 계약에 없는 텍스트 프레임(`pause`·`resume` 포함)은 버리고 닫지 않는다.
        return IgnoredFrame()

    async def send(self, frame: OutboundFrame) -> None:
        if self._websocket.application_state != WebSocketState.CONNECTED:
            raise StreamClientClosed()
        try:
            await self._websocket.send_json(_stream_message(frame))
        except (WebSocketDisconnect, RuntimeError) as error:
            raise StreamClientClosed() from error

    async def close(self, code: int, reason: str) -> None:
        if self._websocket.application_state != WebSocketState.CONNECTED:
            return
        try:
            await self._websocket.close(code=code, reason=reason)
        except RuntimeError:
            pass



































class ConversationContextReferenceRequest(BaseModel):
    resource_type: Literal["task", "work_request"]
    resource_id: UUID
    resource_version: int = Field(ge=1)
    included: bool




















class ChecklistArchiveRequest(BaseModel):
    expected_version: int | None = None
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








class GenerateDailyReportDraftRequest(BaseModel):
    report_date: str


class ActionDecisionRequest(BaseModel):
    expected_version: int
    decision: Literal["approve", "reject"]


def _runtime_error(error: Exception) -> HTTPException:
    if isinstance(error, BrowserInteractionConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValidationError):
        return HTTPException(status_code=422, detail=error.errors(include_input=False, include_context=False))
    if isinstance(error, ResourceNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RESOURCE_NOT_FOUND_MESSAGE)
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
    if isinstance(error, RoomBookingRefused):
        # 자리를 못 잡아 **회의를 만들지 않았다** (SCAX-WP-007 D36-2). 사람이 이어서 고를 수 있게
        # 그 시간에 가능한 방을 함께 낸다 — 사유만 주고 창을 닫게 하지 않는다.
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": error.reason,
                "message": str(error),
                "available_rooms": error.available_rooms,
            },
        )
    if isinstance(error, RoomCreationIdempotencyRequired):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": error.reason, "message": str(error)},
        )
    if isinstance(error, RoomCreationIdempotencyConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.reason, "message": str(error)},
        )
    # 회의는 권한 밖도 없는 것처럼 응답한다 — 참석자가 아닌 사람에게 존재를 알리지 않는다 (SPEC-004 §3.2-1).
    if isinstance(error, (TaskNotFound, MaterialNotFound, MeetingNotFound, MeetingAccessDenied)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, (MeetingStateConflict, MeetingVersionConflict)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, (TaskAccessDenied, WorkRequestAccessDenied, DailyReportAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    # SPEC-003 § Case Matrix — **`TaskError` 보다 먼저** 봐야 각자의 상태로 나간다. 전부 409 인 이유는
    # 하나다: 요청 자체는 말이 되는데 **지금 그 자원의 상태가 그 명령을 받지 않는다**.
    if isinstance(
        error,
        (
            TaskCancelRequiresAgreement,
            TaskChildrenUnfinished,
            TaskDirectNesting,
            TaskParentClosed,
            TaskParentUnassigned,
            TaskProposalNotPending,
            TaskReopenParentDone,
            TaskAssignmentProposalExists,
            # SPEC-001 § Case Matrix — WORK-003 이 더하는 셋. **같은 기준으로 같은 자리에 선다**:
            # 명령 자체는 말이 되는데 지금 그 업무의 상태가 받지 않는다.
            TaskPredecessorsUnfinished,
            TaskProjectLockedByPredecessors,
            TaskApproverLocked,
            # SPEC-004 § Case Matrix — 시간 배정이 더하는 셋. 같은 기준이다: 명령 자체는 말이 되는데
            # 지금 그 업무·그 날·그 회차가 받지 않는다. **배정 회차는 업무 회차와 다른 예외**이므로
            # 기존 업무 표면의 422 계약이 그대로 남는다 (증보 K8).
            TaskScheduleTaskClosed,
            TaskScheduleDayTaken,
            TaskScheduleVersionConflict,
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    # 값 자체가 틀렸다 — 자기 자신을 상위로 둘 수 있는 상태란 없다.
    if isinstance(error, TaskParentCycle):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    # 생성 계약의 세 줄 (SPEC-001 § Case Matrix). `TaskError` 보다 먼저 봐야 각자의 상태로 나간다.
    if isinstance(error, TaskRecipientNotAllowed):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, TaskIdempotencyConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, TaskIdempotencyKeyRequired):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, (TaskError, InvalidTaskTransition, MeetingError)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, ActionNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ActionCenterError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, (WorkRequestIdempotencyConflict, WorkRequestNotPending, WorkRequestLockedAfterAccept)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, WorkRejectReasonRequired):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
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
        ) -> AssistantCharacterResult:
            try:
                return app.state.workflow_application.set_assistant_character(
                    principal, request.character_key, request.expected_version
                )
            except UnsupportedAssistantCharacter as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
            except AssistantCharacterPreferenceConflict as error:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

        @app.get("/api/my-work")
        def my_work(include_closed: bool = False, principal: Principal = Depends(developer_principal)) -> list[TaskListEntry]:
            try:
                return app.state.workflow_application.my_work(principal, include_closed=include_closed)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/meetings")
        def list_meetings(
            cursor: str | None = None,
            from_: date | None = Query(default=None, alias="from"),
            to: date | None = Query(default=None, alias="to"),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object] | list[dict[str, object]]:
            """예정·지난 두 구획. 「지난」은 20건씩 커서로 잇는다.

            **`from`·`to` 가 둘 다 오면 구획도 커서도 쓰지 않는다** — 그 기간과 겹치는 회의의 한
            배열이다 (SPEC-004 §4 · 증보 K2). 둘 다 없으면 **지금 그대로**이므로 회의 화면은 영향을
            받지 않는다. 한쪽만 오면 거절한다.
            """
            try:
                return app.state.workflow_application.meeting_board(
                    principal, cursor=cursor, span_from=from_, span_to=to
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings", status_code=status.HTTP_201_CREATED)
        def create_meeting(
            request: CreateMeetingRequest,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.create_meeting(
                    principal,
                    title=request.title,
                    purpose=request.purpose,
                    starts_at=request.starts_at,
                    ends_at=request.ends_at,
                    location=request.location,
                    attendee_ids=request.attendee_ids,
                    external_attendees=request.external_attendees,
                    agendas=[{"title": row.title} for row in request.agendas],
                    carried_from_meeting_id=request.carried_from_meeting_id,
                    room_id=request.room_id,
                    idempotency_key=idempotency_key,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/meetings/rooms")
        def list_meeting_rooms(
            starts_at: datetime | None = None,
            ends_at: datetime | None = None,
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            """고를 수 있는 사옥 회의실. 예약 시스템이 없거나 닿지 않으면 **빈 목록**이다 —
            화면은 「회의실 선택 안 함」만 세우고, 잡을 수 없는 방을 보여 주지 않는다.

            `starts_at`·`ends_at` 을 함께 주면 **그 시간에 비어 있는 방만** 내고 `available: true` 가 붙는다
            (SCAX-WP-007 D36-3) — 예약이 거절된 뒤 모달이 회의실 칸만 다시 그리는 자리다.
            둘 중 하나만 오면 시간을 모르는 것과 같으므로 전체 목록이다.
            """
            del principal
            try:
                return app.state.workflow_application.meeting_rooms(starts_at=starts_at, ends_at=ends_at)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/quick-start", status_code=status.HTTP_201_CREATED)
        def quick_start_meeting(principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            """값을 묻지 않고 세우고 곧장 연다. 참석자는 켠 사람 하나다."""
            try:
                return app.state.workflow_application.quick_start_meeting(principal)
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
            changes = request.model_dump(exclude_unset=True)
            try:
                return app.state.workflow_application.update_meeting(principal, meeting_id, changes)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_meeting(
            meeting_id: UUID,
            scope: Literal["meeting", "note"] = "meeting",
            principal: Principal = Depends(developer_principal),
        ) -> Response:
            """두 갈래다 — 회의 자체를 취소하거나, 회의록만 지우고 예약은 남긴다."""
            try:
                if scope == "meeting":
                    app.state.workflow_application.cancel_meeting(principal, meeting_id)
                else:
                    app.state.workflow_application.delete_meeting_note(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.post("/api/meetings/{meeting_id}/start")
        def start_meeting(meeting_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.start_meeting(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/end")
        async def end_meeting(meeting_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            """상태를 「정리 중」으로 옮기고 서버가 스트림을 닫는다 — 오류가 아니므로 오류 프레임을 앞세우지 않는다."""
            try:
                result = app.state.workflow_application.end_meeting(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error
            await app.state.workflow_application.close_meeting_stream(meeting_id)
            return result

        @app.post("/api/meetings/{meeting_id}/finalize")
        def retry_meeting_finalize(
            meeting_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            """[다시 시도] — 「실패」에서만. 합성만 다시 걸고 받은 발화와 메모는 건드리지 않는다."""
            try:
                return app.state.workflow_application.retry_meeting_finalize(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/todos/{todo_id}/promote", status_code=status.HTTP_201_CREATED)
        def promote_meeting_todo(
            meeting_id: UUID,
            todo_id: UUID,
            request: PromoteTodoRequest,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            """후보를 업무 요청으로 보낸다 — 출처 두 id 를 열로 싣는다. 갈래는 하나다."""
            try:
                return app.state.workflow_application.promote_meeting_todo(
                    principal,
                    meeting_id,
                    todo_id,
                    idempotency_key=idempotency_key,
                    assignee_id=request.assignee_id,
                    title=request.title,
                    description=request.description,
                    due_date=request.due_date,
                    checklist=request.checklist,
                    # 공통 생성 프레임의 일곱 — 회의에서 연 창과 업무 화면의 창이 **같은 값을 싣는다**.
                    start_date=request.start_date,
                    cc_member_ids=request.cc_member_ids,
                    approver_id=request.approver_id,
                    reference_task_ids=request.reference_task_ids,
                    project_id=request.project_id,
                    parent_task_id=request.parent_task_id,
                    preceding_task_ids=request.preceding_task_ids,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/meetings/{meeting_id}/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
        def remove_meeting_todo(
            meeting_id: UUID, todo_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> Response:
            """안 만들 후보는 확인 없이 지운다 — 아직 업무가 아니다. 승격된 것은 목록에 남는다."""
            try:
                app.state.workflow_application.remove_meeting_todo(principal, meeting_id, todo_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.get("/api/meetings/{meeting_id}/promotion-candidates", response_model=list[CandidateResponse])
        def meeting_promotion_candidates(
            meeting_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> list[MemberCandidateView]:
            """승격 모달의 담당 후보 — **참석자 먼저, 그다음 조직도 전체** (SCAX-SPEC-004 §9-5 · D40).

            `/api/task-assignment-candidates` 를 쓰지 않는다. 그쪽은 「내가 남에게 배정할 수 있는
            범위」라 누른 사람의 배정 권한으로 좁히는데, **승격의 요청 주체는 회의(시스템)**라 그 권한을
            타지 않는다 — 그 목록을 쓰면 6명 중 2명만 뜬다 (사용자 결정 2026-09-14 §조사 근거 5).
            """
            try:
                return [
                    CandidateResponse(**candidate)
                    for candidate in app.state.workflow_application.meeting_promotion_candidates(
                        principal, meeting_id
                    )
                ]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/meetings/{meeting_id}/export")
        def export_meeting(
            meeting_id: UUID,
            format: Literal["html"] = "html",
            principal: Principal = Depends(developer_principal),
        ) -> Response:
            """마지막 저장분을 내보낸다 — 판이 없으므로 그것이 그 회의록이다. **형식은 HTML 하나다** (§2.2)."""
            try:
                document = app.state.workflow_application.export_meeting(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error
            filename = f"{document['filename']}.html"
            return Response(
                content=document["html"],
                media_type="text/html; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
            )

        @app.post("/api/meetings/{meeting_id}/agendas", status_code=status.HTTP_201_CREATED)
        def add_meeting_agenda(
            meeting_id: UUID,
            request: CreateAgendaRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.add_meeting_agenda(principal, meeting_id, request.title)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/meetings/{meeting_id}/agendas/{agenda_id}")
        def update_meeting_agenda(
            meeting_id: UUID,
            agenda_id: UUID,
            request: UpdateAgendaRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.update_meeting_agenda(
                    principal, meeting_id, agenda_id, request.changes()
                )
            except MeetingStaleWrite as error:
                # 덮어쓰지 않고 지금 있는 것을 함께 낸다 — 사람이 차이를 보고 정한다 (SPEC-004 §8-9).
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "meeting_agenda_stale", "current": error.current},
                ) from error
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/agendas/{agenda_id}/lines", status_code=status.HTTP_201_CREATED)
        def write_meeting_memo(
            meeting_id: UUID,
            agenda_id: UUID,
            request: WriteMemoRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            """메모 한 줄 — **안건이 경로에 있다**. 줄이 어느 안건에 매달리는지가 주소로 읽힌다."""
            try:
                return app.state.workflow_application.write_meeting_memo(
                    principal, meeting_id, agenda_id, request.text
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/meetings/{meeting_id}/agendas/{agenda_id}/lines/{line_id}")
        def edit_meeting_memo_line(
            meeting_id: UUID,
            agenda_id: UUID,
            line_id: UUID,
            request: WriteMemoRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            """메모 한 줄을 고친다 — **줄 하나가 한 요청이다** (사용자 결정 2026-09-14 §바뀌는 것 1).

            최종 벌처럼 안건의 줄 목록을 통째로 보내지 않는다: 메모는 한 줄씩 자동 저장으로 쌓이는
            기록이라(§6-7) 고치는 단위도 한 줄이고, 그래야 회의가 도는 동안 그 사이에 들어온 남의 줄을
            덮어쓰지 않는다. **사람 벌의 줄만 이 자리로 온다** — 최종 벌은 `PATCH …/agendas/{id}` 의
            한 덩어리 저장이고 AI 벌은 사람이 손대지 않는다.
            """
            try:
                return app.state.workflow_application.edit_meeting_memo_line(
                    principal, meeting_id, agenda_id, line_id, request.text
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete(
            "/api/meetings/{meeting_id}/agendas/{agenda_id}/lines/{line_id}",
            status_code=status.HTTP_204_NO_CONTENT,
        )
        def remove_meeting_memo_line(
            meeting_id: UUID,
            agenda_id: UUID,
            line_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> Response:
            """메모 한 줄을 지운다 — 확인을 받지 않는다. 사람이 자기가 적은 임시 재료를 걷는 것이다."""
            try:
                app.state.workflow_application.remove_meeting_memo_line(
                    principal, meeting_id, agenda_id, line_id
                )
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.get("/api/meetings/{meeting_id}/transcript")
        def meeting_transcript(
            meeting_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            """확정 발화 원문과 회의 중 메모. 진행 중이면 지금까지, 끝난 뒤면 그대로 그 원문이다."""
            try:
                return app.state.workflow_application.meeting_transcript(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/meetings/{meeting_id}/agendas/{agenda_id}", status_code=status.HTTP_204_NO_CONTENT)
        def remove_meeting_agenda(
            meeting_id: UUID,
            agenda_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> Response:
            try:
                app.state.workflow_application.remove_meeting_agenda(principal, meeting_id, agenda_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.websocket("/api/meetings/{meeting_id}/stream")
        async def meeting_stream(websocket: WebSocket, meeting_id: UUID) -> None:
            """오디오 업 · 발화 다운 · AI 증분 다운을 겸하는 통로 하나 (SCAX-SPEC-004 §5.3).

            여기서 하는 것은 **인증뿐**이다 — 핸드셰이크의 세션 쿠키로 사람을 풀고, 첫 프레임 5초 안에 역할을 받는다.
            참석 판정(4404) · 상태(4409) · 단일 업스트림(4409) · 중계 · 적재는 전부 스트림 세션이 한다.
            """
            await websocket.accept()
            principal = connection_principal(websocket)
            if principal is None:
                await websocket.close(code=CLOSE_UNAUTHORIZED, reason=REASON_UNAUTHORIZED)
                return
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
                frame = StreamAuthFrame.model_validate_json(raw)
            except (TimeoutError, ValidationError, WebSocketDisconnect):
                # 사유를 흘리지 않는다 — 첫 프레임이 없거나 무효면 같은 답이다.
                if websocket.application_state == WebSocketState.CONNECTED:
                    await websocket.close(code=CLOSE_UNAUTHORIZED, reason=REASON_UNAUTHORIZED)
                return
            audio = frame.audio
            await app.state.workflow_application.meeting_stream.serve(
                _WebSocketStreamClient(websocket),
                member_id=str(principal.id),
                meeting_id=str(meeting_id),
                role=ROLE_UPSTREAM if frame.role == ROLE_UPSTREAM else ROLE_SUBSCRIBE,
                audio=(
                    AudioDeclaration(format=audio.format, sample_rate=audio.sample_rate, channels=audio.channels)
                    if audio is not None
                    else None
                ),
            )

        @app.get("/api/meetings/{meeting_id}/materials")
        def list_meeting_materials(
            meeting_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> list[dict[str, object]]:
            """회의를 보며 열어 놓을 파일들. 공유받은 사람도 읽는다."""
            try:
                return app.state.workflow_application.list_meeting_materials(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/materials", status_code=status.HTTP_201_CREATED)
        async def attach_meeting_materials(
            meeting_id: UUID,
            files: list[UploadFile] = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, object]:
            """되는 것만 붙고 안 되는 것은 **사유와 함께** 돌아온다 — 하나가 막혔다고 나머지를 버리지 않는다."""
            uploads = [
                MaterialUpload(
                    name=item.filename or "material",
                    content_type=item.content_type or "",
                    data=await item.read(),
                )
                for item in files
            ]
            try:
                return app.state.workflow_application.attach_meeting_materials(principal, meeting_id, uploads)
            except MeetingMaterialsRejected as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "meeting_materials_rejected",
                        "failed": [{"name": row.name, "reason": row.reason} for row in error.failed],
                    },
                ) from error
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/meetings/{meeting_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
        def detach_meeting_material(
            meeting_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> Response:
            """떼는 것은 올린 사람이다 — 회의를 만든 사람도 남의 자료를 못 뗀다."""
            try:
                app.state.workflow_application.detach_meeting_material(principal, meeting_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.get("/api/meetings/{meeting_id}/materials/{material_id}/content")
        def meeting_material_content(
            meeting_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> Response:
            try:
                view, data = app.state.workflow_application.open_meeting_material(principal, meeting_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            # 자료는 **새 탭에서 열린다** — 미리보기를 걷은 자리다 (D37). `attachment` 면 탭이 뜨자마자
            # 내려받기로 떨어진다. 이름은 그대로 `filename*=` 로 실어 내려받을 때의 이름을 지킨다.
            inline = inline_media_type(str(view["name"]), str(view["content_type"]))
            disposition = "inline" if inline else "attachment"
            return Response(
                content=data,
                media_type=inline or str(view["content_type"]),
                headers={
                    "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(str(view['name']))}"
                },
            )

        @app.get("/api/meetings/{meeting_id}/shares")
        def meeting_viewers(
            meeting_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> list[dict[str, object]]:
            """「볼 수 있는 사람」 — 참석과 공유를 한 목록으로. `basis` 가 둘을 가른다."""
            try:
                return app.state.workflow_application.meeting_viewers(principal, meeting_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/meetings/{meeting_id}/shares")
        def share_meeting(
            meeting_id: UUID,
            request: ShareMeetingRequest,
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            """공유는 열람만 연다. 알림은 가지 않는다 — 목록에 담기는 것이 유일한 도달 경로다."""
            try:
                return app.state.workflow_application.share_meeting_with(principal, meeting_id, request.member_ids)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/meetings/{meeting_id}/shares/{member_id}")
        def revoke_meeting_share(
            meeting_id: UUID,
            member_id: str,
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            """거두고 나서 남은 「볼 수 있는 사람」을 낸다 — `GET /shares` 와 같은 목록이다."""
            try:
                return app.state.workflow_application.revoke_meeting_share(principal, meeting_id, member_id)
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
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/conversations")
        def conversations(
            principal: Principal = Depends(developer_principal),
            limit: int = Query(default=DEFAULT_CONVERSATION_LIST_LIMIT, ge=1, le=100),
        ) -> list[ConversationView]:
            return app.state.workflow_application.conversations(principal, limit=limit)

        @app.post("/api/conversations", status_code=status.HTTP_201_CREATED)
        def create_conversation(request: CreateConversationRequest, principal: Principal = Depends(developer_principal)) -> ConversationView:
            return app.state.workflow_application.create_conversation(principal, request.title)

        @app.get("/api/conversations/{conversation_id}")
        def conversation(
            conversation_id: UUID,
            principal: Principal = Depends(developer_principal),
            before_sequence: int | None = Query(default=None, ge=0),
        ) -> ConversationView:
            try:
                return app.state.workflow_application.conversation(principal, conversation_id, before_sequence=before_sequence)
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
        ) -> ConversationView:
            try:
                return app.state.workflow_application.cancel_conversation_turn(
                    principal, conversation_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/conversations/{conversation_id}/turns/{turn_id}/retry", status_code=status.HTTP_202_ACCEPTED)
        def retry_conversation_turn(conversation_id: UUID, turn_id: UUID, principal: Principal = Depends(developer_principal)) -> ConversationRetryResult:
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
        ) -> ActionProposalResult:
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
        def organization_members(principal: Principal = Depends(developer_principal)) -> list[MemberDirectoryView]:
            return app.state.workflow_application.member_directory(principal)

        @app.get("/api/organization/members/{member_id}")
        def organization_member_detail(
            member_id: str, principal: Principal = Depends(developer_principal)
        ) -> MemberDetailView:
            try:
                return app.state.workflow_application.organization_member_detail(principal, member_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/organization/members/{member_id}/history")
        def organization_member_history(
            member_id: str,
            axis: str = Query(description="membership · appointment · grade · job · grant"),
            principal: Principal = Depends(developer_principal),
        ) -> list[MemberAxisHistoryView]:
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
        ) -> list[OrganizationActivityView]:
            try:
                return app.state.workflow_application.organization_activity(
                    principal, unit_id=unit_id, limit=limit, cursor=cursor
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/access/roles")
        def installed_access_roles(principal: Principal = Depends(developer_principal)) -> list[InstalledRoleView]:
            try:
                return app.state.workflow_application.installed_access_roles(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/access/members/{member_id}")
        def member_access(member_id: str, principal: Principal = Depends(developer_principal)) -> MemberAccessView:
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
        def organization_tree(principal: Principal = Depends(developer_principal)) -> list[OrganizationUnitView]:
            return app.state.workflow_application.organization_tree(principal)

        @app.get("/api/organization/units/{unit_id}/members")
        def organization_unit_members(unit_id: str, principal: Principal = Depends(developer_principal)) -> list[UnitMemberView]:
            try:
                return app.state.workflow_application.organization_unit_members(principal, unit_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/organization/me")
        def my_organization_profile(principal: Principal = Depends(developer_principal)) -> MyOrganizationProfileView:
            return app.state.workflow_application.my_organization_profile(principal)

        @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
        def create_task(
            request: CreateTaskRequest,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            principal: Principal = Depends(developer_principal),
        ) -> TaskMutationResult:
            """업무 생성 — 본인 또는 수신자 지정. 담당이 남이어도 **수락 없이** 그 사람의 업무가 된다.

            멱등 키는 `Idempotency-Key` **헤더**로 받는다. 본문의 `idempotency_key` 는 알 수 없는 필드로 거부된다.
            """
            try:
                return app.state.workflow_application.create_task(
                    principal,
                    request.title,
                    idempotency_key=idempotency_key,
                    assignee_id=request.assignee_id,
                    description=request.description,
                    start_date=request.start_date,
                    due_date=request.due_date,
                    checklist=request.checklist,
                    reference_task_ids=request.reference_task_ids,
                    parent_task_id=request.parent_task_id,
                    project_id=request.project_id,
                    cc_member_ids=request.cc_member_ids,
                    preceding_task_ids=request.preceding_task_ids,
                    approver_id=request.approver_id,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        # ---- 프로젝트: 조직 단위와 나란한 두 번째 축 ----

        @app.get("/api/projects")
        def list_projects(principal: Principal = Depends(developer_principal)) -> list[ProjectView]:
            try:
                return app.state.workflow_application.list_projects(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/projects", status_code=status.HTTP_201_CREATED)
        def create_project(request: CreateProjectRequest, principal: Principal = Depends(developer_principal)) -> ProjectView:
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
        def get_project(project_id: UUID, principal: Principal = Depends(developer_principal)) -> ProjectDetailResult:
            try:
                return app.state.workflow_application.get_project(principal, project_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/projects/{project_id}/participation-history")
        def project_participation_history(
            project_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> list[ProjectParticipationView]:
            try:
                return app.state.workflow_application.project_participation_history(principal, project_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
        def plan_project_work(
            project_id: UUID,
            request: PlanProjectWorkRequest,
            principal: Principal = Depends(developer_principal),
        ) -> TaskMutationResult:
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
        ) -> ProjectAssignmentView:
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
            request: ReleaseFromProjectRequest | None = None,
            principal: Principal = Depends(developer_principal),
        ) -> Response:
            try:
                app.state.workflow_application.release_from_project(
                    principal,
                    project_id,
                    member_id,
                    assignment_id=request.assignment_id if request else None,
                    reason=request.reason if request else None,
                )
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.post("/api/tasks/assign", status_code=status.HTTP_201_CREATED)
        def assign_task(
            request: AssignTaskRequest,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            principal: Principal = Depends(developer_principal),
        ) -> TaskAssignmentResult:
            try:
                return app.state.workflow_application.assign_task(
                    principal, idempotency_key=idempotency_key, **request.model_dump()
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/task-assignment-candidates", response_model=list[CandidateResponse])
        def task_assignment_candidates(principal: Principal = Depends(developer_principal)) -> list[MemberCandidateView]:
            try:
                return [CandidateResponse(**candidate) for candidate in app.state.workflow_application.task_assignment_candidates(principal)]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/task-assignments/sent")
        def sent_task_assignments(principal: Principal = Depends(developer_principal)) -> list[TaskAssignmentResult]:
            try:
                return app.state.workflow_application.sent_task_assignments(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/task-assignments/{assignment_id}/accept")
        def accept_task_assignment(assignment_id: UUID, principal: Principal = Depends(developer_principal)) -> TaskAssignmentResult:
            try:
                return app.state.workflow_application.accept_task_assignment(principal, assignment_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/task-assignments/{assignment_id}/decline")
        def decline_task_assignment(
            assignment_id: UUID, request: DeclineTaskAssignmentRequest, principal: Principal = Depends(developer_principal)
        ) -> TaskAssignmentResult:
            try:
                return app.state.workflow_application.decline_task_assignment(principal, assignment_id, request.reason)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/tasks/{task_id}")
        def update_task(task_id: UUID, request: UpdateTaskRequest, principal: Principal = Depends(developer_principal)) -> TaskDateMutationResult:
            try:
                return app.state.workflow_application.update_task(principal, task_id, request.expected_version, request.changes())
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/daily-reports/{report_id}/materials/{material_id}/content")
        def open_report_material(report_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                view, data = app.state.workflow_application.open_report_material(principal, report_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            return Response(content=data, media_type=str(view["content_type"]),
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(str(view['name']))}"})

        @app.post("/api/browser-interactions/files", status_code=201)
        def request_file_attachment(request: BrowserFileRequest, principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.request_file_attachment(principal, request)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/browser-interactions/{interaction_id}")
        def browser_interaction(interaction_id: UUID, principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.browser_interaction(principal, interaction_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/browser-interactions/{interaction_id}/file")
        async def upload_browser_file(interaction_id: UUID, file: UploadFile = File(...), principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.upload_browser_file(principal, interaction_id, name=file.filename or 'material', content_type=file.content_type or 'application/octet-stream', data=await file.read())
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post('/api/browser-interactions/recordings', status_code=201)
        def request_recording(request: BrowserRecordingRequest, principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.request_recording(principal, request)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post('/api/browser-interactions/{interaction_id}/recording/start')
        def start_browser_recording(interaction_id: UUID, request: BrowserRecordingStartInput, principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.start_browser_recording(principal, interaction_id, request.capture_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post('/api/browser-interactions/{interaction_id}/recording/stop')
        async def stop_browser_recording(interaction_id: UUID, capture_id: UUID = Form(...), file: UploadFile = File(...), principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.stop_browser_recording(principal, interaction_id, capture_id, name=file.filename or 'recording.webm', content_type=file.content_type or 'audio/webm', data=await file.read())
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/browser-interactions/{interaction_id}")
        def interrupt_browser_interaction(interaction_id: UUID, request: BrowserInterruptionInput, principal: Principal = Depends(developer_principal)) -> BrowserInteractionResult:
            try:
                return app.state.workflow_application.interrupt_browser_interaction(principal, interaction_id, request.status)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/material-folders")
        def list_material_folders(principal: Principal = Depends(developer_principal)) -> list[FolderView]:
            try:
                return app.state.workflow_application.list_material_folders(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/material-folders", status_code=status.HTTP_201_CREATED)
        def create_material_folder(request: MaterialFolderCreateRequest, principal: Principal = Depends(developer_principal)) -> FolderView:
            try:
                return app.state.workflow_application.create_material_folder(principal, **request.model_dump())
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/material-folders/{folder_id}/materials")
        def list_folder_materials(folder_id: UUID, principal: Principal = Depends(developer_principal)) -> list[FolderMaterialView]:
            try:
                return app.state.workflow_application.list_folder_materials(principal, folder_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/material-folders/{folder_id}/materials", status_code=status.HTTP_201_CREATED)
        async def upload_folder_material(folder_id: UUID, file: UploadFile = File(...), principal: Principal = Depends(developer_principal)) -> FolderMaterialView:
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
        def detach_folder_material(folder_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> FolderDetachResult:
            try:
                return app.state.workflow_application.detach_folder_material(principal, folder_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/material-folders/{folder_id}/archive")
        def archive_material_folder(folder_id: UUID, principal: Principal = Depends(developer_principal)) -> FolderArchiveResult:
            try:
                return app.state.workflow_application.archive_material_folder(principal, folder_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/materials")
        def list_task_materials(task_id: UUID, principal: Principal = Depends(developer_principal)) -> list[TaskMaterialView]:
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
        ) -> TaskMaterialResult:
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
        ) -> TaskCompletionResult:
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
        ) -> TaskReferenceResult:
            try:
                return app.state.workflow_application.add_task_reference(principal, task_id, request.referenced_task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/tasks/{task_id}/references/{reference_id}")
        def release_task_reference(
            task_id: UUID, reference_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> TaskReferenceReleaseResult:
            try:
                return app.state.workflow_application.release_task_reference(principal, task_id, reference_id)
            except Exception as error:
                raise _runtime_error(error) from error

        # ---- 시간 배정: 날짜 단위로 사는 업무 위에 얹히는 시간 축 (SPEC-004) ----

        @app.post("/api/tasks/{task_id}/schedules", status_code=status.HTTP_201_CREATED)
        def create_task_schedule(
            task_id: UUID,
            request: CreateTaskScheduleRequest,
            response: Response,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            principal: Principal = Depends(developer_principal),
        ) -> TaskScheduleView:
            """배정 생성 — **생성 전용**이다. 그 날이 이미 차 있으면 `409` 이고 덮어쓰지 않는다.

            **같은 멱등 키의 재전송은 그 `409` 에 걸리지 않는다** — 영수증이 먼저다 (증보 K12).
            **영수증은 `200`** 이다: 새로 만들어진 것이 없기 때문이다.
            멱등 키는 `Idempotency-Key` **헤더**로 받는다.
            """
            try:
                view, created = app.state.workflow_application.create_task_schedule(
                    principal,
                    task_id,
                    idempotency_key=idempotency_key,
                    on_date=request.on_date,
                    starts_at=request.starts_at,
                    ends_at=request.ends_at,
                )
            except Exception as error:
                raise _runtime_error(error) from error
            response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            return view

        @app.patch("/api/task-schedules/{schedule_id}")
        def retime_task_schedule(
            schedule_id: UUID,
            request: RetimeTaskScheduleRequest,
            principal: Principal = Depends(developer_principal),
        ) -> TaskScheduleView:
            """시각 변경 — 같은 날 재배정도 여기로 온다. **날짜는 못 바꾼다** (§2.3 R6)."""
            try:
                return app.state.workflow_application.retime_task_schedule(
                    principal,
                    schedule_id,
                    expected_version=request.expected_version,
                    starts_at=request.starts_at,
                    ends_at=request.ends_at,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/calendar")
        def calendar(
            from_: date = Query(alias="from"),
            to: date = Query(alias="to"),
            principal: Principal = Depends(developer_principal),
        ) -> list[dict[str, object]]:
            """업무 + 회의 **합본** — 한 배열이고 `kind` 로 가른다. 한 화면 = 한 요청이다 (증보 K2)."""
            try:
                return app.state.workflow_application.calendar(principal, from_, to)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/graph/overview")
        def graph_overview(
            view: Literal["member", "team", "project"] = "member",
            limit: int = 120,
            principal: Principal = Depends(developer_principal),
        ) -> GraphOverviewResult:
            try:
                return app.state.workflow_application.graph_overview(principal, view=view, limit=limit)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/graph/search")
        def graph_search(q: str, limit: int = 20, principal: Principal = Depends(developer_principal)) -> GraphSearchResult:
            try:
                return app.state.workflow_application.graph_search(principal, q, limit)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/graph/neighbors")
        def graph_neighbors(node: str, limit: int = 20, principal: Principal = Depends(developer_principal)) -> GraphNeighborsResult:
            try:
                return app.state.workflow_application.graph_neighbors(principal, node, limit)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/history")
        def task_history(task_id: UUID, principal: Principal = Depends(developer_principal)) -> TaskHistoryResult:
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
        ) -> TaskHistoryDiffResult:
            try:
                return app.state.workflow_application.task_history_diff(principal, task_id, from_version, to_version)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/children")
        def task_children(
            task_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            """**직속 하위만** 낸다 (SPEC-003 §4 · 정책 L-11). 저장 깊이와 무관하다.

            읽을 수 없는 하위는 목록에도 **건수에도** 없다 — 상세의 `children` 과 같은 판정이다.
            """
            try:
                return app.state.workflow_application.task_children(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/assignments")
        def task_assignments(
            task_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            """**현재 담당과 대기 제안을 각각** 낸다 — 담당 변경 대기 중에는 둘 다 있다 (정책 V-18)."""
            try:
                return app.state.workflow_application.task_assignments(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/reopen")
        def reopen_task(
            task_id: UUID,
            request: TaskReopenRequest,
            principal: Principal = Depends(developer_principal),
        ) -> TaskMutationResult:
            """끝난 일을 다시 연다. **완료된 상위가 있으면 거부**하고 상위를 먼저 열라고 낸다."""
            try:
                return app.state.workflow_application.reopen_task(
                    principal, task_id, request.expected_version, request.reason
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}/proposals")
        def list_task_proposals(
            task_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            try:
                return app.state.workflow_application.task_proposals(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/proposals", status_code=status.HTTP_201_CREATED)
        def propose_task_change(
            task_id: UUID,
            request: TaskProposalRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            """수락 뒤의 취소·조건 변경 제안. **제안만으로는 아무것도 바뀌지 않는다.**"""
            try:
                return app.state.workflow_application.propose_task_change(
                    principal, task_id, request.kind, request.expected_version,
                    reason=request.reason, payload=request.payload,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/proposals/{proposal_id}/respond")
        def respond_to_task_proposal(
            task_id: UUID,
            proposal_id: UUID,
            request: TaskProposalResponseRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            """동의 / 동의하지 않음 — **담당자만**. 동의해야 비로소 바뀐다."""
            try:
                return app.state.workflow_application.respond_to_task_proposal(
                    principal, task_id, proposal_id, request.expected_version,
                    agree=request.agree, reason=request.reason,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/proposals/{proposal_id}/withdraw")
        def withdraw_task_proposal(
            task_id: UUID,
            proposal_id: UUID,
            request: TaskVersionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            """제안한 사람이 거둔다. **회차가 필수다** — 상태를 바꾸는 모든 명령이 그렇다 (K-4)."""
            try:
                return app.state.workflow_application.withdraw_task_proposal(
                    principal, task_id, proposal_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/reassign")
        def reassign_task(
            task_id: UUID,
            request: ReassignTaskRequest,
            principal: Principal = Depends(developer_principal),
        ) -> TaskAssignmentResult:
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
        ) -> TaskMaterialResult:
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
        ) -> TaskMaterialResult:
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
        ) -> MaterialSearchResult:
            try:
                return app.state.workflow_application.search_materials(principal, q, limit=limit, resource_types=resource_types,
                    resource_type=resource_type, resource_id=resource_id, material_id=material_id,
                    registered_from=registered_from, registered_until=registered_until)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/materials/{material_id}")
        def material_metadata(material_id: UUID, principal: Principal = Depends(developer_principal)) -> MaterialMetadataResult:
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
        def detach_task_material(task_id: UUID, binding_id: UUID, principal: Principal = Depends(developer_principal)) -> TaskMaterialResult:
            try:
                return app.state.workflow_application.detach_task_material(principal, task_id, binding_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks")
        def list_tasks(
            include_closed: bool = False,
            principal: Principal = Depends(developer_principal),
        ) -> list[TaskListEntry]:
            try:
                return app.state.workflow_application.list_tasks(principal, include_closed=include_closed)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/tasks/{task_id}")
        def get_task(task_id: UUID, principal: Principal = Depends(developer_principal)) -> TaskDetailResult:
            try:
                return app.state.workflow_application.get_task(principal, task_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests", status_code=status.HTTP_201_CREATED)
        def create_work_request(
            request: CreateWorkRequestRequest,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            principal: Principal = Depends(developer_principal),
        ) -> WorkRequestMutationResult:
            try:
                return app.state.workflow_application.create_work_request(
                    principal, request.title, request.assignee_id,
                    idempotency_key=idempotency_key,
                    description=request.description, start_date=request.start_date, due_date=request.due_date,
                    project_id=request.project_id, approver_id=request.approver_id,
                    cc_member_ids=request.cc_member_ids,
                    preceding_task_ids=request.preceding_task_ids,
                    checklist=request.checklist, reference_task_ids=request.reference_task_ids,
                    parent_task_id=request.parent_task_id, supersedes_request_id=request.supersedes_request_id,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-requests")
        def list_work_requests(
            include_removed: bool = Query(default=False),
            principal: Principal = Depends(developer_principal),
        ) -> list[WorkRequestMutationResult]:
            """내 요청 목록.

            `include_removed=true` 면 **내가 정리한 항목까지** 돌려주고, 각 행의 `list_entry_hidden` 이
            어느 쪽인지 말한다. 숨김 여부를 화면이 기억하지 않는다 — 브라우저를 새로 열면 그 기억은
            사라지는데 정리했다는 사실은 남아야 한다 (SPEC-003 §4 목록 정리).
            """
            try:
                return app.state.workflow_application.list_work_requests(principal, include_removed=include_removed)
            except Exception as error:
                raise _runtime_error(error) from error

        # **`/{request_id}` 보다 먼저 선언한다** — 뒤에 두면 `inbox` 가 요청 id 로 읽혀
        # UUID 파싱에서 422 가 난다. FastAPI 는 선언 순서대로 맞춰 본다.
        @app.get("/api/work-requests/inbox")
        def work_request_inbox(
            principal: Principal = Depends(developer_principal),
        ) -> list[WorkRequestInboxEntry]:
            """답할 업무(category=work)와 CC 참조(category=reference)의 합집합."""
            try:
                return app.state.workflow_application.work_request_inbox(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-requests/{request_id}")
        def get_work_request(
            request_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> WorkRequestDetailResult:
            try:
                return app.state.workflow_application.get_work_request(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/withdraw")
        def withdraw_work_request(
            request_id: UUID,
            request: WorkRequestVersionRequest,
            principal: Principal = Depends(developer_principal),
        ) -> WorkRequestMutationResult:
            """수락 전 철회 — **요청자의 일반 명령이다.**

            예전에는 판단함 명령으로만 열려 있어서, 요청 화면에서 거둘 길이 없었다. 판단함 경유도
            같은 결과를 그대로 낸다 (K-10).
            """
            try:
                return app.state.workflow_application.withdraw_work_request(
                    principal, request_id, request.expected_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/read")
        def mark_work_request_reference_read(
            request_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> WorkRequestReadReceiptResult:
            """참고 항목 읽음 — **본문도 회차도 멱등 키도 없다** (SPEC-001 §4 Validation).

            두 번째 호출도 `200` 이고 `read_at` 은 처음 값 그대로다. 이미 읽었다는 것은 충돌이 아니다.
            참조자가 아니면 403, 없거나 못 읽는 요청은 404(같은 말)다.
            """
            try:
                return app.state.workflow_application.mark_work_request_reference_read(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.delete("/api/work-requests/{request_id}/list-entry")
        def remove_work_request_list_entry(
            request_id: UUID,
            principal: Principal = Depends(developer_principal),
        ) -> dict[str, Any]:
            """요청자 목록에서만 뺀다 — **로그는 남는다** (정책 P-12). 전역 삭제가 아니다."""
            try:
                return app.state.workflow_application.remove_work_request_list_entry(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/resubmit")
        def resubmit_work_request(
            request_id: UUID,
            request: WorkRequestResubmitRequest,
            principal: Principal = Depends(developer_principal),
        ) -> WorkRequestMutationResult:
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
        ) -> WorkRequestMutationResult:
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
        ) -> ActionDiscussionView:
            try:
                return app.state.workflow_application.add_work_request_comment(principal, request_id, request.body, idempotency_key)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/checklist", status_code=status.HTTP_201_CREATED)
        def add_task_checklist_item(task_id: UUID, request: ChecklistItemRequest, principal: Principal = Depends(developer_principal)) -> ChecklistMutationResult:
            try:
                return app.state.workflow_application.add_task_checklist_item(
                    principal, task_id, request.text, request.expected_task_version
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.patch("/api/tasks/{task_id}/checklist/{item_id}")
        def update_task_checklist_item(
            task_id: UUID, item_id: UUID, request: ChecklistItemPatch, principal: Principal = Depends(developer_principal)
        ) -> ChecklistMutationResult:
            try:
                return app.state.workflow_application.update_task_checklist_item(
                    principal, task_id, item_id, **request.model_dump(exclude_none=True)
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/checklist/order")
        def reorder_task_checklist(
            task_id: UUID, request: ChecklistOrderRequest, principal: Principal = Depends(developer_principal)
        ) -> ChecklistOrderResult:
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
        ) -> ChecklistMutationResult:
            # Taking a step off the list moves the Task, so the answer carries the version it moved to.
            try:
                return app.state.workflow_application.archive_task_checklist_item(
                    principal, task_id, item_id,
                    expected_version=expected_version, expected_task_version=expected_task_version,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/action-items")
        def pending_action_items(principal: Principal = Depends(developer_principal)) -> list[ActionEnvelopeResult]:
            try:
                return app.state.workflow_application.pending_action_items(principal)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/action-items/{action_item_id}")
        def action_item_detail(action_item_id: str, principal: Principal = Depends(developer_principal)) -> ActionDetailResult:
            try:
                return app.state.workflow_application.action_item_detail(principal, action_item_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/action-items/{action_item_id}/material-drafts/links", status_code=status.HTTP_201_CREATED)
        def stage_action_material_link(
            action_item_id: UUID,
            request: ActionMaterialLinkDraftRequest,
            principal: Principal = Depends(developer_principal),
        ) -> ActionMaterialDraftView:
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
        ) -> ActionMaterialDraftView:
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
        ) -> ActionMaterialDraftView:
            try:
                return app.state.workflow_application.discard_action_material_draft(
                    principal, action_item_id, material_draft_id
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/action-items/{action_item_id}/commands/{command}")
        def run_action_command(
            action_item_id: str, command: str, request: ActionCommandRequest, principal: Principal = Depends(developer_principal)
        ) -> ActionEnvelopeResult:
            try:
                return app.state.workflow_application.run_action_command(principal, action_item_id, command, request.model_dump(exclude_none=True))
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-request-cc-candidates", response_model=list[CandidateResponse])
        def work_request_cc_candidates(principal: Principal = Depends(developer_principal)) -> list[MemberCandidateView]:
            try:
                return [CandidateResponse(**candidate) for candidate in app.state.workflow_application.work_request_cc_candidates(principal)]
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/comments/{comment_id}/attachments", status_code=status.HTTP_201_CREATED)
        async def attach_to_work_request_comment(
            request_id: UUID, comment_id: UUID, file: UploadFile = File(...), principal: Principal = Depends(developer_principal)
        ) -> ActionDiscussionView:
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
        ) -> WorkRequestEvidenceResult:
            data = await file.read()
            try:
                return app.state.workflow_application.add_work_request_evidence(
                    principal, request_id,
                    name=file.filename or "evidence", content_type=file.content_type or "application/octet-stream", data=data,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        # ---- 요청 자료 — 발송한 요청에 **2단계로** 붙는다 (WORK-003) -------------------------
        #
        # 생성 payload 에 자료 칸을 더하지 않는다. 내 업무가 지나는 길과 같은 모양이다: 만들고 →
        # 돌아온 `request_id` 로 붙인다. 댓글 첨부·판단 근거와는 **다른 자리**이며 그 둘을 대용으로
        # 쓰지 않는다 — 논의에 붙인 파일과 요청이 실어 보낸 자료는 뜻이 다르다.

        @app.get("/api/work-requests/{request_id}/materials")
        def list_work_request_materials(
            request_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> list[WorkRequestMaterialView]:
            try:
                return app.state.workflow_application.list_work_request_materials(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/materials", status_code=status.HTTP_201_CREATED)
        async def attach_work_request_material(
            request_id: UUID,
            kind: str = Form("input"),
            file: UploadFile = File(...),
            principal: Principal = Depends(developer_principal),
        ) -> WorkRequestMaterialResult:
            data = await file.read()
            try:
                return app.state.workflow_application.attach_work_request_material(
                    principal,
                    request_id,
                    kind=kind,
                    name=file.filename or "material",
                    content_type=file.content_type or "application/octet-stream",
                    data=data,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/work-requests/{request_id}/materials/links", status_code=status.HTTP_201_CREATED)
        def attach_work_request_material_link(
            request_id: UUID,
            request: WorkRequestMaterialLinkRequest,
            principal: Principal = Depends(developer_principal),
        ) -> WorkRequestMaterialResult:
            try:
                return app.state.workflow_application.attach_work_request_material_link(
                    principal, request_id, kind=request.kind, url=request.url, label=request.label
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-requests/{request_id}/materials/{material_id}/content")
        def work_request_material_content(
            request_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> Response:
            try:
                view, data = app.state.workflow_application.open_work_request_material(principal, request_id, material_id)
            except Exception as error:
                raise _runtime_error(error) from error
            from urllib.parse import quote

            return Response(
                content=data,
                media_type=str(view["content_type"]),
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(str(view['name']))}"},
            )

        @app.delete("/api/work-requests/{request_id}/materials/{material_id}")
        def detach_work_request_material(
            request_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)
        ) -> WorkRequestMaterialResult:
            try:
                return app.state.workflow_application.detach_work_request_material(principal, request_id, material_id)
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
        def work_request_timeline(request_id: UUID, principal: Principal = Depends(developer_principal)) -> WorkRequestHistoryResult:
            try:
                return app.state.workflow_application.work_request_timeline(principal, request_id)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-request-assignee-candidates", response_model=list[CandidateResponse])
        def work_request_assignee_candidates(
            principal: Principal = Depends(developer_principal),
        ) -> list[MemberCandidateView]:
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
        ) -> WorkRequestMutationResult:
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
        ) -> WorkRequestMutationResult:
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
        ) -> WorkRequestMutationResult:
            try:
                return app.state.workflow_application.negotiate_work_request(
                    principal, request_id, request.expected_version, request.conditions
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/daily-reports/generate-draft", status_code=status.HTTP_202_ACCEPTED)
        def generate_daily_report_draft(request: GenerateDailyReportDraftRequest, principal: Principal = Depends(developer_principal)) -> ReportStatusResult:
            try:
                return app.state.workflow_application.request_daily_report_draft(principal, request.report_date)
            except (DailyReportAccessDenied, DailyReportNotFound) as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.get("/api/daily-reports/status")
        def daily_report_status(
            report_date: str = Query(),
            principal: Principal = Depends(developer_principal),
        ) -> ReportStatusResult:
            try:
                return app.state.workflow_application.daily_report_status(principal, report_date)
            except (DailyReportAccessDenied, DailyReportNotFound) as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.post("/api/daily-reports/{report_id}/edit")
        def edit_daily_report(
            report_id: str,
            request: EditDailyReportRequest,
            principal: Principal = Depends(developer_principal),
        ) -> ReportDraftResult:
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
            except (DailyReportAccessDenied, DailyReportNotFound) as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.post("/api/daily-reports/{report_id}/submit", status_code=status.HTTP_201_CREATED)
        def submit_daily_report(
            report_id: str,
            request: SubmitDailyReportRequest,
            principal: Principal = Depends(developer_principal),
        ) -> ReportSubmissionResult:
            try:
                return app.state.workflow_application.submit_daily_report(
                    principal,
                    report_id,
                    request.draft_id,
                    request.expected_version,
                    request.reason,
                )
            except (DailyReportAccessDenied, DailyReportNotFound) as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        @app.get("/api/daily-reports/{report_id}/history")
        def daily_report_history(
            report_id: str,
            principal: Principal = Depends(developer_principal),
        ) -> ReportHistoryResult:
            try:
                return app.state.workflow_application.daily_report_history(principal, report_id)
            except (DailyReportAccessDenied, DailyReportNotFound) as error:
                raise _runtime_error(error) from error
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

        def task_transition(task_id: UUID, target: TaskState, principal: Principal, reason: str | None = None, expected_version: int = 0) -> TaskDateMutationResult:
            try:
                return app.state.workflow_application.transition_task(task_id, principal, target, reason, expected_version)
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/{task_id}/start")
        def start_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> TaskDateMutationResult:
            """시작 — **비어 있던 시작일을 오늘로 채운다**. 날짜가 바뀌는 자리라 `schedule_release` 를 싣는다.

            **막힘·재개·완료·취소는 날짜를 바꾸지 않으므로 그 묶음을 내지 않는다** (증보 K3 의 세 자리).
            같은 operation 을 지나지만 **표면이 약속하는 것**은 여기서 갈린다.
            """
            return task_transition(task_id, TaskState.IN_PROGRESS, principal, expected_version=request.expected_version)

        @app.post("/api/tasks/{task_id}/block")
        def block_task(task_id: UUID, request: BlockTaskRequest, principal: Principal = Depends(developer_principal)) -> TaskMutationResult:
            return task_transition(task_id, TaskState.BLOCKED, principal, request.reason, request.expected_version)

        @app.post("/api/tasks/{task_id}/resume")
        def resume_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> TaskMutationResult:
            return task_transition(task_id, TaskState.IN_PROGRESS, principal, expected_version=request.expected_version)

        @app.post("/api/tasks/{task_id}/complete")
        def complete_task(task_id: UUID, request: TaskTransitionRequest, principal: Principal = Depends(developer_principal)) -> TaskMutationResult:
            return task_transition(task_id, TaskState.DONE, principal, expected_version=request.expected_version)

        @app.post("/api/tasks/{task_id}/cancel")
        def cancel_task(task_id: UUID, request: CancelTaskRequest, principal: Principal = Depends(developer_principal)) -> TaskMutationResult:
            """직접 취소 — **사유 필수** (SPEC-003 §4). 사유는 진행 기록에 그대로 남는다.

            수락된 요청 Task 에서는 이 명령이 거부되고 제안–동의로 안내한다.
            """
            return task_transition(task_id, TaskState.CANCELLED, principal, request.reason, request.expected_version)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "profile": settings.profile}

    return app


app = create_app()
