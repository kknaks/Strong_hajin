from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from ax_workspace.modules.organization_access.domain import Principal, SEED_PERSONAS
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
from ax_workspace.modules.work.materials import MaterialNotFound
from ax_workspace.modules.actions.domain import ActionError as ActionCenterError, ActionNotFound
from ax_workspace.modules.work.requests import WorkRequestAccessDenied, WorkRequestError, WorkRequestIdempotencyConflict
from ax_workspace.modules.reports.application import DailyReportAccessDenied
from ax_workspace.modules.meetings.domain import MeetingAccessDenied, MeetingError, MeetingNotFound
from ax_workspace.modules.ax_execution.conversations import ConversationError, ConversationQueueOverflow
from ax_workspace.modules.ax_execution.actions import ActionAccessDenied, ActionCapabilityDenied, ActionError
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure


class PersonaResponse(BaseModel):
    id: str
    display_name: str


class LoginRequest(BaseModel):
    provider: Literal["developer"] = "developer"
    account: str = Field(min_length=1, max_length=100)


class CreateTaskRequest(BaseModel):
    title: str
    description: str | None = None
    start_date: date | None = None
    due_date: date | None = None


class CreateMeetingRequest(BaseModel):
    organization_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    starts_at: datetime
    ends_at: datetime
    visibility: Literal["public", "private"] = "private"
    attendee_ids: list[str] = Field(default_factory=list)


class UpdateMeetingRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    visibility: Literal["public", "private"] | None = None


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


class AssignTaskRequest(BaseModel):
    title: str
    assignee_id: str
    description: str | None = None
    start_date: date | None = None
    due_date: date | None = None


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


class CreateWorkRequestRequest(BaseModel):
    title: str
    assignee_id: str
    description: str | None = None
    due_date: date | None = None
    cc_member_ids: list[str] = []


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


class ChecklistItemPatch(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    done: bool | None = None


class ActionCommandRequest(BaseModel):
    """What a command needs from the caller; the server decides which command is available at all."""

    expected_version: int | None = None
    reason: str | None = Field(default=None, max_length=4000)
    changes: dict[str, object] | None = None


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
    if isinstance(error, ConversationQueueOverflow):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "conversation_queue_full", "queue_size": error.queue_size, "limit": error.limit},
        )
    if isinstance(error, (TaskNotFound, MaterialNotFound, MeetingNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, (TaskAccessDenied, WorkRequestAccessDenied, DailyReportAccessDenied, MeetingAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, (TaskError, InvalidTaskTransition, MeetingError)):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
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

        @app.get("/api/developer/personas", response_model=list[PersonaResponse])
        def personas() -> list[PersonaResponse]:
            return [PersonaResponse(id=persona.id, display_name=persona.display_name) for persona in SEED_PERSONAS.values()]

        @app.get("/api/auth/providers")
        def auth_providers() -> dict[str, object]:
            return {
                "developer": settings.developer_auth_enabled,
                "oidc": False,
                "accounts": [
                    {"id": persona.id, "display_name": persona.display_name} for persona in SEED_PERSONAS.values()
                ]
                if settings.developer_auth_enabled
                else [],
            }

        @app.post("/api/auth/login")
        def login(request: LoginRequest, response: Response) -> dict[str, object]:
            allowed = app.state.developer_auth.authenticate(request.account)
            try:
                principal = app.state.workflow_application.authenticated_principal(str(allowed.id))
            except LookupError as error:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active organization membership is required.") from error
            session_id = app.state.auth_sessions.create(str(principal.id), app.state.developer_auth.provider_name)
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
                return app.state.workflow_application.accept_conversation_message(principal, request.body, conversation_id, request.context, idempotency_key)
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
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.post("/api/tasks/assign", status_code=status.HTTP_201_CREATED)
        def assign_task(request: AssignTaskRequest, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.assign_task(
                    principal, request.title, request.assignee_id,
                    description=request.description, start_date=request.start_date, due_date=request.due_date,
                )
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/task-assignment-candidates", response_model=list[PersonaResponse])
        def task_assignment_candidates(principal: Principal = Depends(developer_principal)) -> list[PersonaResponse]:
            try:
                return [PersonaResponse(**candidate) for candidate in app.state.workflow_application.task_assignment_candidates(principal)]
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
            try:
                return app.state.workflow_application.update_task(principal, task_id, request.expected_version, changes)
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

        @app.get("/api/tasks/{task_id}/materials/search")
        def search_task_materials(
            task_id: UUID, q: str, limit: int = 5, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.search_task_materials(principal, task_id, q, limit=limit)
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

        @app.post("/api/tasks/{task_id}/materials/{material_id}/detach")
        def detach_task_material(task_id: UUID, material_id: UUID, principal: Principal = Depends(developer_principal)) -> dict[str, object]:
            try:
                return app.state.workflow_application.detach_task_material(principal, task_id, material_id)
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
                return app.state.workflow_application.add_task_checklist_item(principal, task_id, request.text)
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

        @app.delete("/api/tasks/{task_id}/checklist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
        def remove_task_checklist_item(task_id: UUID, item_id: UUID, principal: Principal = Depends(developer_principal)) -> Response:
            try:
                app.state.workflow_application.remove_task_checklist_item(principal, task_id, item_id)
                return Response(status_code=status.HTTP_204_NO_CONTENT)
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

        @app.post("/api/action-items/{action_item_id}/commands/{command}")
        def run_action_command(
            action_item_id: str, command: str, request: ActionCommandRequest, principal: Principal = Depends(developer_principal)
        ) -> dict[str, object]:
            try:
                return app.state.workflow_application.run_action_command(principal, action_item_id, command, request.model_dump(exclude_none=True))
            except Exception as error:
                raise _runtime_error(error) from error

        @app.get("/api/work-request-cc-candidates", response_model=list[PersonaResponse])
        def work_request_cc_candidates(principal: Principal = Depends(developer_principal)) -> list[PersonaResponse]:
            try:
                return [PersonaResponse(**candidate) for candidate in app.state.workflow_application.work_request_cc_candidates(principal)]
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

        @app.get("/api/work-request-assignee-candidates", response_model=list[PersonaResponse])
        def work_request_assignee_candidates(
            principal: Principal = Depends(developer_principal),
        ) -> list[PersonaResponse]:
            try:
                candidates = app.state.workflow_application.work_request_assignee_candidates(principal)
                return [PersonaResponse(**candidate) for candidate in candidates]
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
