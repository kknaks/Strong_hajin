"""Persona-bound MCP adapter for SCAX public product operations.

The persona is process configuration, never a tool argument.  The server reads
the current principal from the same Organization & Access projection used by
HTTP and invokes local operations in-process.
"""
from __future__ import annotations

import asyncio

from ax_workspace.modules.ax_execution.browser_interactions import BrowserRecordingRequest, BrowserFileRequest, BrowserInteractionResult

from ax_workspace.modules.work.material_query_results import FolderMaterialView
from ax_workspace.modules.work.graph_results import GraphNeighborsResult
from ax_workspace.modules.work.graph_results import GraphOverviewResult
from ax_workspace.modules.work.graph_results import GraphSearchResult
from ax_workspace.modules.work.material_query_results import MaterialMetadataResult
from ax_workspace.modules.work.material_query_results import MaterialSearchResult

from ax_workspace.modules.organization_access.results import InstalledRoleView, MemberAccessView, MemberAxisHistoryView, MemberCandidateView, MemberDetailView, MemberDirectoryView, MyOrganizationProfileView, OrganizationActivityView, OrganizationUnitView, UnitMemberView

from ax_workspace.modules.reports.results import ReportHistoryResult, ReportStatusResult, ReportRecentView
from ax_workspace.modules.meetings.results import MeetingFollowupResult

from ax_workspace.modules.ax_execution.conversation_results import ConversationView, ConversationSearchResult

from ax_workspace.modules.work.material_results import TaskMaterialView, TaskMaterialResult
from ax_workspace.modules.work.task_results import TaskCompletionResult, TaskReferenceResult, TaskReferenceReleaseResult

from ax_workspace.modules.work.project_results import ProjectView, ProjectDetailResult, ProjectAssignmentView, ProjectParticipationView

from ax_workspace.modules.ax_execution.result_contracts import ActionMaterialDraftView
from ax_workspace.modules.ax_execution.conversation_commands import ConversationMessageResult, ConversationRetryResult
from ax_workspace.modules.notifications import NotificationView
from ax_workspace.modules.organization_access.commands import AssistantCharacterResult
from ax_workspace.modules.work.folder_commands import FolderView, FolderArchiveResult, FolderDetachResult
from ax_workspace.modules.work.project_commands import ProjectReleaseResult

from ax_workspace.modules.work.request_results import WorkRequestDetailResult, WorkRequestHistoryResult
from ax_workspace.modules.actions.results import ActionDiscussionView

from ax_workspace.modules.work.task_results import TaskChecklistResult, TaskDetailResult, TaskHistoryDiffResult, TaskHistoryResult, TaskListEntry, TaskSubtasksResult

from ax_workspace.modules.actions.results import ActionEnvelopeResult, ActionDetailResult

import os
import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ax_workspace.entrypoints.mcp_server import PersonaMcpServer

from ax_workspace.modules.actions.commands import ActionCommandInput
from ax_workspace.modules.ax_execution.result_contracts import ActionProposalResult, CommandResult
from ax_workspace.modules.work.task_results import TaskMutationResult, TaskAssignmentResult, ChecklistMutationResult, ChecklistOrderResult
from ax_workspace.modules.work.request_results import WorkRequestMutationResult

from ax_workspace.modules.work.task_creation import TaskCreateInput, TaskAssignmentInput
from ax_workspace.modules.work.material_search import MaterialResourceType
from ax_workspace.modules.work.materials import MaterialError, MaterialNotFound
from ax_workspace.bootstrap.application import WorkflowApplication, create_workflow_application
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, DAILY_REPORT_EDIT, DAILY_REPORT_SUBMIT, MEETING_MANAGE, MEETING_SHARE, Principal, TASK_ASSIGN, TASK_SELF_MANAGE, WORK_REQUEST_CREATE, WORK_REQUEST_DECIDE
from ax_workspace.modules.ax_execution.actions import (
    ACTION_ITEM_COMMAND,
    ACTION_ITEM_COMMAND_TITLE,
)
from ax_workspace.modules.ax_execution.ai import AiProvider
from ax_workspace.modules.work.checklist_commands import ChecklistAddCommand, ChecklistUpdateCommand, ChecklistArchiveCommand, ChecklistOrderCommand
from ax_workspace.modules.work.task_commands import (
    TaskTransitionCommand,
    TaskUpdateInput, TaskUpdateCommand, TaskCompletionInput, TaskCompletionCommand, TaskReferenceCommand, TaskReferenceReleaseCommand, TaskReassignInput, TaskReassignCommand,
)
from ax_workspace.modules.work.folder_commands import FolderCreateInput, FolderArchiveCommand, FolderMaterialDetachCommand
from ax_workspace.modules.work.project_commands import (
    ProjectCreateInput, ProjectMemberInput, ProjectMemberCommand,
    ProjectReleaseCommand, ProjectWorkInput, ProjectWorkCommand,
)
from ax_workspace.modules.reports.results import ReportDraftResult, ReportSubmissionResult
from ax_workspace.modules.reports.commands import ReportEditCommand, ReportSubmitCommand, ReportSourceReference
from ax_workspace.modules.meetings.results import MeetingMaterialView, MeetingSummaryAdoptionResult, MeetingSpeakerView
from ax_workspace.modules.meetings.commands import (
    MeetingMaterialLinkInput, MeetingMaterialLinkCommand, MeetingMaterialDetachCommand, MeetingSummaryAdoptCommand, MeetingSpeakerInput, MeetingSpeakerCommand, MeetingFollowupCommand,
    MeetingAgendaDraftInput,
    MeetingAgendaPatch,
    MeetingInfoPatch,
    MeetingMemoInput,
    MeetingReservationInput,
    MeetingShareManyInput,
    MeetingTodoPromotionInput,
)
from ax_workspace.modules.work.material_commands import (TaskMaterialLinkInput, TaskMaterialLinkCommand, TaskMaterialReferenceInput, TaskMaterialReferenceCommand, TaskMaterialDetachCommand)

from ax_workspace.modules.notifications import NotificationReadCommand
from ax_workspace.modules.organization_access.commands import AssistantCharacterInput
from ax_workspace.modules.work.assignment_commands import AssignmentAcceptCommand, AssignmentDeclineCommand
from ax_workspace.modules.work.request_commands import WorkRequestAcceptCommand, WorkRequestRejectCommand, WorkRequestNegotiationCommand, WorkRequestAmendCommand, WorkRequestRevisionInput, WorkRequestCreateInput, WorkRequestCommentCommand

from ax_workspace.modules.ax_execution.conversation_commands import (ConversationCreateInput, ConversationMessageInput, ConversationMessageCommand, ConversationCancelCommand, ConversationRetryCommand)

from ax_workspace.modules.work.material_commands import ActionMaterialLinkInput, ActionMaterialLinkCommand, ActionMaterialDiscardCommand


class McpMeetingReservationInput(MeetingReservationInput):
    """MCP makes the room-create retry identity part of the request contract."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "if": {"properties": {"room_id": {"type": "integer"}}, "required": ["room_id"]},
            "then": {"required": ["idempotency_key"]},
        },
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="room_id를 지정한 회의실 생성 재시도를 식별하는 필수 키",
    )

    @model_validator(mode="after")
    def require_room_idempotency_key(self) -> McpMeetingReservationInput:
        if self.room_id is not None and not self.idempotency_key:
            raise ValueError("room_id를 지정하면 idempotency_key가 필요합니다")
        return self

    def reservation_input(self) -> MeetingReservationInput:
        return MeetingReservationInput.model_validate(
            self.model_dump(exclude={"idempotency_key"})
        )

DELEGATED_ACTION_CAPABILITIES = {
    "action.material.link.stage": ACTION_DECIDE,
    "action.material.draft.discard": ACTION_DECIDE,
    "conversation.create": ACTION_DECIDE,
    "conversation.message.send": ACTION_DECIDE,
    "conversation.turn.cancel": ACTION_DECIDE,
    "conversation.turn.retry": ACTION_DECIDE,
    "notification.mark_read": ACTION_DECIDE,
    "assistant.character.set": ACTION_DECIDE,
    "work_request.comment.add": "work_request.read",
    "meeting.material.attach_link": MEETING_MANAGE,
    "meeting.material.detach": MEETING_MANAGE,
    "meeting.material.remove": MEETING_MANAGE,
    "meeting.summary.adopt": MEETING_MANAGE,
    "meeting.speaker.assign": MEETING_MANAGE,
    "meeting.followup.task": TASK_SELF_MANAGE,
    "meeting.followup.request": WORK_REQUEST_CREATE,
    "meeting.quick_start": MEETING_MANAGE,
    "meeting.cancel": MEETING_MANAGE,
    "meeting.note.delete": MEETING_MANAGE,
    "meeting.start": MEETING_MANAGE,
    "meeting.end": MEETING_MANAGE,
    "meeting.finalize.retry": MEETING_MANAGE,
    "meeting.todo.promote": MEETING_MANAGE,
    "meeting.todo.remove": MEETING_MANAGE,
    "meeting.agenda.add": MEETING_MANAGE,
    "meeting.agenda.update": MEETING_MANAGE,
    "meeting.agenda.remove": MEETING_MANAGE,
    "meeting.memo.write": MEETING_MANAGE,
    "meeting.share_many": MEETING_SHARE,
    "meeting.share.revoke": MEETING_SHARE,
    "meeting.info.update": MEETING_MANAGE,
    "task.material.attach_link": TASK_SELF_MANAGE,
    "task.material.attach_reference": TASK_SELF_MANAGE,
    "task.material.detach": TASK_SELF_MANAGE,
    "task.reassign": TASK_ASSIGN,
    "task.completion.submit": TASK_SELF_MANAGE,
    "task.reference.add": TASK_SELF_MANAGE,
    "task.reference.release": TASK_SELF_MANAGE,
    "material_folder.create": ACTION_DECIDE,
    "material_folder.archive": ACTION_DECIDE,
    "material_folder.detach": ACTION_DECIDE,
    "project.create": "project.manage",
    "project.assign_member": "project.manage",
    "project.release_member": "project.manage",
    "project.plan_work": TASK_ASSIGN,
    "daily_report.edit": DAILY_REPORT_EDIT,
    "daily_report.submit": DAILY_REPORT_SUBMIT,
    "work_request.create": WORK_REQUEST_CREATE,
    "work_request.amend": WORK_REQUEST_CREATE,
    "work_request.accept": WORK_REQUEST_DECIDE,
    "work_request.negotiate": WORK_REQUEST_DECIDE,
    "work_request.reject": WORK_REQUEST_DECIDE,
    "task.create_self": TASK_SELF_MANAGE,
    "task.transition": TASK_SELF_MANAGE,
    "task.update": TASK_SELF_MANAGE,
    "task.assign": TASK_ASSIGN,
    "task.assignment.accept": TASK_SELF_MANAGE,
    "task.assignment.decline": TASK_SELF_MANAGE,
    "task.checklist.add": TASK_SELF_MANAGE,
    "task.checklist.update": TASK_SELF_MANAGE,
    "task.checklist.archive": TASK_SELF_MANAGE,
    "task.checklist.reorder": TASK_SELF_MANAGE,
    "task.progress.batch": TASK_SELF_MANAGE,
    "meeting.reservation.create": MEETING_MANAGE,
    "meeting.share": MEETING_SHARE,
    "meeting.note.create": MEETING_MANAGE,
    "meeting.note.save": MEETING_MANAGE,
    "meeting.note.finalize": MEETING_MANAGE,
}


_READ_ONLY_TOOL = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
#: A command is not read-only and may withdraw, reject or decline, so it is marked destructive even though the canonical
#: payload-aware receipt makes re-sending the same answer safe.
_COMMAND_TOOL = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


class MaterialOpenResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_id: str
    name: str
    content_type: str
    size_bytes: int
    open_url: str


class McpDelegatedActionAccessDenied(RuntimeError):
    pass


class McpReportsFacade:
    """MCP transport adapter for canonical Reports application operations."""

    def __init__(
        self,
        settings: Settings,
        persona_id: str,
        report_provider: AiProvider | None = None,
    ) -> None:
        if not settings.developer_auth_enabled:
            raise RuntimeError("MCP developer adapter is available only in development and test profiles")
        self._application: WorkflowApplication = create_workflow_application(settings, report_provider)
        self._persona_id = persona_id

    @property
    def principal(self) -> Principal:
        return self._application.authenticated_principal(self._persona_id)

    def generate_daily_report_draft(self, report_date: str) -> ReportStatusResult:
        return self._application.request_daily_report_draft(
            self.principal,
            report_date,
            self._mutation_key("daily_report.generate_draft", {"report_date": report_date}),
        )

    def edit_daily_report(
        self,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[ReportSourceReference] | None = None,
        exclude_source_refs: list[ReportSourceReference] | None = None,
    ) -> ReportDraftResult | ActionProposalResult:
        command = ReportEditCommand(report_id=report_id, draft_id=draft_id, expected_version=expected_version, body=body, include_source_refs=include_source_refs, exclude_source_refs=exclude_source_refs)
        payload = command.values()
        action = self._propose_chat_action("daily_report.edit", "일일보고 초안 수정 확인", payload)
        if action is not None:
            return action
        return self._application.edit_daily_report(self.principal, **command.values())

    def submit_daily_report(
        self,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None = None,
    ) -> ReportSubmissionResult | ActionProposalResult:
        command = ReportSubmitCommand(report_id=report_id, draft_id=draft_id, expected_version=expected_version, reason=reason)
        action = self._propose_chat_action('daily_report.submit', '일일보고 제출 확인', command.model_dump(mode='json'))
        if action is not None:
            return action
        return self._application.submit_daily_report(self.principal, **command.model_dump(mode='json'))

    def daily_report_history(self, report_id: str) -> ReportHistoryResult:
        return self._application.daily_report_history(self.principal, report_id)

    def work_request_assignee_candidates(self) -> list[MemberCandidateView]:
        return self._application.work_request_assignee_candidates(self.principal)

    def list_work_requests(self) -> list[WorkRequestMutationResult]:
        requests = self._application.list_work_requests(self.principal)
        self._remember(
            [
                {"resource_type": "work_request", "resource_id": str(row["request_id"]), "resource_version": row.get("version")}
                for row in requests
            ]
        )
        return requests

    def resubmit_work_request(self, request_id: str, expected_version: int, title: str | None, description: str | None, due_date: str | None) -> dict[str, Any]:
        return self._application.resubmit_work_request(
            self.principal, UUID(request_id), expected_version, title=title, description=description, due_date=_parse_iso_date(due_date)
        )

    def amend_work_request(
        self, request_id: str, expected_version: int, title: str | None, description: str | None, due_date: str | None,
        clear_due_date: bool = False,
    ) -> WorkRequestMutationResult | ActionProposalResult:
        command = WorkRequestAmendCommand(request_id=request_id, expected_version=expected_version, title=title, description=description, due_date=due_date, clear_due_date=clear_due_date)
        payload = command.model_dump(mode='json')
        action = self._propose_chat_action("work_request.amend", "요청 수정 확인", payload)
        if action is not None:
            return action
        return self._application.amend_work_request(
            self.principal, **command.model_dump(),
        )

    def work_request_history(self, request_id: str) -> WorkRequestHistoryResult:
        return self._application.work_request_timeline(self.principal, UUID(request_id))

    def get_work_request(self, request_id: str) -> WorkRequestDetailResult:
        request = self._application.get_work_request(self.principal, UUID(request_id))
        self._remember(
            [{"resource_type": "work_request", "resource_id": str(request["request_id"]), "resource_version": request.get("version")}]
        )
        return request

    def create_work_request(
        self, title: str, assignee_id: str, due_date: str | None = None, description: str | None = None,
        cc_member_ids: list[str] | None = None, checklist: list[str] | None = None,
        reference_task_ids: list[str] | None = None,
    ) -> WorkRequestMutationResult | ActionProposalResult:
        command = WorkRequestCreateInput(title=title, assignee_id=assignee_id, due_date=due_date, description=description, cc_member_ids=cc_member_ids, checklist=checklist, reference_task_ids=reference_task_ids).for_requester(str(self.principal.id))
        payload = command.model_dump(mode='json')
        action = self._propose_chat_action('work_request.create', '업무 요청 생성 확인', payload)
        if action is not None:
            return action
        return self._application.create_work_request(self.principal, **command.model_dump(), causation_key=self._mutation_key('work_request.create', payload))

    def accept_work_request(self, request_id: str, expected_version: int) -> dict[str, Any]:
        command = WorkRequestAcceptCommand(request_id=request_id, expected_version=expected_version)
        action = self._propose_chat_action('work_request.accept', '업무 요청 수락 확인', command.model_dump(mode='json'))
        if action is not None:
            return action
        return self._application.accept_work_request(self.principal, **command.model_dump())

    def negotiate_work_request(
        self, request_id: str, expected_version: int, conditions: dict[str, Any]
    ) -> dict[str, Any]:
        command = WorkRequestNegotiationCommand(request_id=request_id, expected_version=expected_version, conditions=conditions)
        action = self._propose_chat_action('work_request.negotiate', '업무 요청 협의 확인', command.model_dump(mode='json'))
        if action is not None:
            return action
        return self._application.negotiate_work_request(self.principal, **command.model_dump())

    def reject_work_request(
        self, request_id: str, expected_version: int, reason: str
    ) -> dict[str, Any]:
        command = WorkRequestRejectCommand(request_id=request_id, expected_version=expected_version, reason=reason)
        action = self._propose_chat_action('work_request.reject', '업무 요청 거절 확인', command.model_dump(mode='json'))
        if action is not None:
            return action
        return self._application.reject_work_request(self.principal, **command.model_dump())

    def pending_action_items(self) -> list[ActionEnvelopeResult]:
        return self._application.pending_action_items(self.principal)

    def action_item_detail(self, action_item_id: str) -> ActionDetailResult:
        return self._application.action_item_detail(self.principal, action_item_id)

    def run_action_command(
        self,
        action_item_id: str,
        command: str,
        *,
        expected_version: int,
        base_submission_version: int | None = None,
        draft: dict[str, JsonValue] | None = None,
        attachment_draft_ids: list[str] | None = None,
        reason: str | None = None,
        changes: dict[str, JsonValue] | None = None,
    ) -> ActionEnvelopeResult | ActionProposalResult:
        """The same payload HTTP sends: omitted fields are absent, never null, so the server sees one request shape."""
        payload = ActionCommandInput(
            expected_version=expected_version, base_submission_version=base_submission_version,
            draft=draft, attachment_draft_ids=attachment_draft_ids, reason=reason, changes=changes,
        ).model_dump(mode='json', exclude_none=True)
        gated = self._propose_action_item_command(action_item_id, command, payload)
        if gated is not None:
            return gated
        return self._application.run_action_command(self.principal, action_item_id, command, payload)

    def _propose_action_item_command(
        self, action_item_id: str, command: str, payload: dict[str, Any]
    ) -> ActionProposalResult | None:
        """Inside a delegated turn a judgement is prepared for a person, never made by the turn itself.

        Outside one — a direct admin or test caller — the canonical operation runs as before.
        """
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        if not causation_id:
            return None
        principal = self.principal
        if ACTION_DECIDE not in principal.capabilities:
            raise McpDelegatedActionAccessDenied(f"{ACTION_DECIDE} capability is required")
        # The server envelope is the policy, so refuse here what it would refuse there — and never let the turn
        # approve an AX proposal, which is the very gate that puts a person in front of this effect.
        detail = self._application.action_item_detail(principal, action_item_id)
        if str(detail.get("kind", "")).startswith("ax."):
            raise McpDelegatedActionAccessDenied("AX 제안은 사람이 승인합니다")
        # The server decides what this command actually carries, so what is stored is what will run.
        canonical = {
            "action_item_id": action_item_id,
            "command": command,
            **self._application.normalize_action_command(principal, action_item_id, command, payload),
        }
        # One confirmation slot per turn — `propose_action` refuses a different judgement for every
        # tool alike, so a different item, command, version or reason waits for the next turn.
        return self._application.propose_action(
            principal, UUID(causation_id), ACTION_ITEM_COMMAND, ACTION_ITEM_COMMAND_TITLE, canonical
        )

    def _remember(self, references: list[dict[str, Any]]) -> None:
        """Inside a delegated turn, what a read returned becomes something the answer can point at, item by item."""
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        if not causation_id or not references:
            return
        self._application.record_answer_resources(self.principal, UUID(causation_id), references)

    def my_work(self, *, include_closed: bool = False) -> list[TaskListEntry]:
        tasks = self._application.my_work(self.principal, include_closed=include_closed)
        self._remember_tasks(tasks)
        return tasks

    def list_tasks(self, *, include_closed: bool = False) -> list[TaskListEntry]:
        tasks = self._application.list_tasks(self.principal, include_closed=include_closed)
        self._remember_tasks(tasks)
        return tasks

    def _remember_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self._remember(
            [{"resource_type": "task", "resource_id": str(row["task_id"]), "resource_version": row.get("version")} for row in tasks]
        )

    def get_task(self, task_id: str) -> TaskDetailResult:
        task = self._application.get_task(self.principal, UUID(task_id))
        # The authorized detail includes readable children; those observations can be cited too.
        self._remember([
            {"resource_type": "task", "resource_id": str(row["task_id"]), "resource_version": row.get("version")}
            for row in [task, *task.get("children", [])]
        ])
        return task

    def stage_action_material_link(self, action_item_id: str, request: ActionMaterialLinkInput) -> ActionMaterialDraftView | ActionProposalResult:
        command = ActionMaterialLinkCommand(action_item_id=UUID(action_item_id), **request.model_dump())
        action = self._propose_chat_action("action.material.link.stage", "승인 항목의 링크 자료 준비 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.stage_action_material_link(self.principal, **command.model_dump())

    def discard_action_material_draft(self, action_item_id: str, material_draft_id: str) -> ActionMaterialDraftView | ActionProposalResult:
        command = ActionMaterialDiscardCommand(action_item_id=UUID(action_item_id), material_draft_id=UUID(material_draft_id))
        action = self._propose_chat_action("action.material.draft.discard", "승인 항목의 자료 초안 버리기 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.discard_action_material_draft(self.principal, **command.model_dump())

    def create_conversation(self, request: ConversationCreateInput) -> ConversationView | ActionProposalResult:
        action = self._propose_chat_action("conversation.create", "새 대화 생성 확인", request.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.create_conversation(self.principal, request.title)

    def accept_conversation_message(self, conversation_id: str, request: ConversationMessageInput, idempotency_key: str | None = None) -> ConversationMessageResult | ActionProposalResult:
        command = ConversationMessageCommand(conversation_id=UUID(conversation_id), idempotency_key=idempotency_key, **request.model_dump())
        action = self._propose_chat_action("conversation.message.send", "대화 메시지 접수 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.accept_conversation_message(self.principal, **command.model_dump(exclude={'context'}), context=command.context)

    def cancel_conversation_turn(self, conversation_id: str, expected_version: int) -> ConversationView | ActionProposalResult:
        command = ConversationCancelCommand(conversation_id=UUID(conversation_id), expected_version=expected_version)
        action = self._propose_chat_action("conversation.turn.cancel", "대화 응답 취소 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.cancel_conversation_turn(self.principal, **command.model_dump())

    def retry_conversation_turn(self, conversation_id: str, turn_id: str) -> ConversationRetryResult | ActionProposalResult:
        command = ConversationRetryCommand(conversation_id=UUID(conversation_id), turn_id=UUID(turn_id))
        action = self._propose_chat_action("conversation.turn.retry", "대화 응답 다시 시도 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.retry_conversation_turn(self.principal, **command.model_dump())

    def mark_notification_read(self, notification_id: str) -> NotificationView | ActionProposalResult:
        command = NotificationReadCommand(notification_id=UUID(notification_id))
        action = self._propose_chat_action("notification.mark_read", "알림 읽음 처리 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.mark_notification_read(self.principal, command.notification_id)

    def set_assistant_character(self, request: AssistantCharacterInput) -> AssistantCharacterResult | ActionProposalResult:
        action = self._propose_chat_action("assistant.character.set", "AX 캐릭터 변경 확인", request.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.set_assistant_character(self.principal, **request.model_dump())

    def add_work_request_comment(self, request_id: str, body: str, idempotency_key: str | None = None) -> ActionDiscussionView | ActionProposalResult:
        command = WorkRequestCommentCommand(request_id=UUID(request_id), body=body, idempotency_key=idempotency_key)
        action = self._propose_chat_action("work_request.comment.add", "업무 요청 댓글 등록 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.add_work_request_comment(self.principal, **command.model_dump())

    def attach_meeting_material_link(self, meeting_id: str, request: MeetingMaterialLinkInput) -> MeetingMaterialView | ActionProposalResult:
        command = MeetingMaterialLinkCommand(meeting_id=UUID(meeting_id), **request.model_dump())
        action = self._propose_chat_action("meeting.material.attach_link", "회의 자료 링크 연결 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.attach_meeting_material_link(self.principal, **command.model_dump())

    def detach_meeting_material(self, meeting_id: str, binding_id: str, expected_version: int) -> MeetingMaterialView | ActionProposalResult:
        command = MeetingMaterialDetachCommand(meeting_id=UUID(meeting_id), binding_id=UUID(binding_id), expected_version=expected_version)
        action = self._propose_chat_action("meeting.material.detach", "회의 자료 분리 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.detach_meeting_material(self.principal, **command.model_dump())

    def adopt_meeting_summary(self, meeting_id: str, summary_id: str, expected_version: int) -> MeetingSummaryAdoptionResult | ActionProposalResult:
        command = MeetingSummaryAdoptCommand(meeting_id=UUID(meeting_id), summary_id=UUID(summary_id), expected_version=expected_version)
        action = self._propose_chat_action("meeting.summary.adopt", "회의 요약 채택 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.adopt_meeting_summary(self.principal, **command.model_dump())

    def assign_meeting_speaker_identity(self, meeting_id: str, request: MeetingSpeakerInput) -> MeetingSpeakerView | ActionProposalResult:
        command = MeetingSpeakerCommand(meeting_id=UUID(meeting_id), **request.model_dump())
        action = self._propose_chat_action("meeting.speaker.assign", "회의 화자 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.assign_meeting_speaker_identity(self.principal, **command.model_dump())

    def create_meeting_followup_task(self, meeting_id: str, summary_id: str, statement_index: int, title: str | None = None) -> MeetingFollowupResult | ActionProposalResult:
        command = MeetingFollowupCommand(meeting_id=UUID(meeting_id), summary_id=UUID(summary_id), statement_index=statement_index, kind="task", title=title)
        action = self._propose_chat_action("meeting.followup.task", "회의 후속 내 업무 생성 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.promote_meeting_followup(self.principal, **command.model_dump())

    def create_meeting_followup_request(self, meeting_id: str, summary_id: str, statement_index: int, assignee_id: str, title: str | None = None) -> MeetingFollowupResult | ActionProposalResult:
        command = MeetingFollowupCommand(meeting_id=UUID(meeting_id), summary_id=UUID(summary_id), statement_index=statement_index, kind="work_request", title=title, assignee_id=assignee_id)
        action = self._propose_chat_action("meeting.followup.request", "회의 후속 업무 요청 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.promote_meeting_followup(self.principal, **command.model_dump())

    def attach_task_material_link(self, task_id: str, request: TaskMaterialLinkInput) -> TaskMaterialResult | ActionProposalResult:
        command = TaskMaterialLinkCommand(task_id=UUID(task_id), **request.model_dump())
        action = self._propose_chat_action("task.material.attach_link", "업무 자료 링크 연결 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.attach_task_material_link(self.principal, **command.model_dump())

    def attach_task_material_reference(self, task_id: str, request: TaskMaterialReferenceInput) -> TaskMaterialResult | ActionProposalResult:
        command = TaskMaterialReferenceCommand(task_id=UUID(task_id), **request.model_dump())
        action = self._propose_chat_action("task.material.attach_reference", "업무 회의 자료 연결 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.attach_task_material_reference(self.principal, **command.model_dump())

    def detach_task_material(self, task_id: str, binding_id: str) -> TaskMaterialResult | ActionProposalResult:
        command = TaskMaterialDetachCommand(task_id=UUID(task_id), binding_id=UUID(binding_id))
        action = self._propose_chat_action("task.material.detach", "업무 자료 분리 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.detach_task_material(self.principal, **command.model_dump())

    def reassign_task(self, task_id: str, request: TaskReassignInput) -> TaskAssignmentResult | ActionProposalResult:
        command = TaskReassignCommand(task_id=UUID(task_id), **request.model_dump())
        action = self._propose_chat_action("task.reassign", "업무 담당자 변경 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.reassign_task(self.principal, **command.model_dump())

    def submit_task_completion(self, task_id: str, request: TaskCompletionInput) -> TaskCompletionResult | ActionProposalResult:
        command = TaskCompletionCommand(task_id=UUID(task_id), **request.model_dump())
        action = self._propose_chat_action("task.completion.submit", "업무 완료 보고 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.submit_task_completion(self.principal, **command.model_dump())

    def add_task_reference(self, task_id: str, referenced_task_id: str) -> TaskReferenceResult | ActionProposalResult:
        command = TaskReferenceCommand(task_id=UUID(task_id), referenced_task_id=UUID(referenced_task_id))
        action = self._propose_chat_action("task.reference.add", "참고 업무 연결 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.add_task_reference(self.principal, command.task_id, command.referenced_task_id)

    def release_task_reference(self, task_id: str, reference_id: str) -> TaskReferenceReleaseResult | ActionProposalResult:
        command = TaskReferenceReleaseCommand(task_id=UUID(task_id), reference_id=UUID(reference_id))
        action = self._propose_chat_action("task.reference.release", "참고 업무 해제 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.release_task_reference(self.principal, command.task_id, command.reference_id)

    def task_history(self, task_id: str) -> TaskHistoryResult:
        return self._application.task_history(self.principal, UUID(task_id))

    def create_self_task(
        self,
        title: str,
        checklist: list[str] | None = None,
        reference_task_ids: list[str] | None = None,
        parent_task_id: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        project_id: str | None = None,
    ) -> TaskMutationResult | ActionProposalResult:
        command = TaskCreateInput(title=title, description=description, start_date=start_date, due_date=due_date, checklist=checklist, reference_task_ids=reference_task_ids, parent_task_id=parent_task_id, project_id=project_id)
        payload = command.model_dump(mode='json')
        action = self._propose_chat_action('task.create_self', '업무 생성 확인', payload)
        if action is not None:
            return action
        return self._application.create_self_task(self.principal, causation_key=self._mutation_key('task.create_self', payload), **command.model_dump())

    def _mutation_key(self, operation: str, payload: dict[str, Any]) -> str | None:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        if not causation_id:
            return None
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{causation_id}:{operation}:{canonical}".encode()).hexdigest()

    def _propose_chat_action(
        self,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> ActionProposalResult | None:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        if not causation_id:
            return None
        principal = self._principal_for_delegated_action(action_type)
        return self._application.propose_action(
            principal,
            UUID(causation_id),
            action_type,
            title,
            payload,
        )

    def _principal_for_delegated_action(self, action_type: str) -> Principal:
        capability = DELEGATED_ACTION_CAPABILITIES.get(action_type)
        if capability is None:
            raise RuntimeError(f"delegated Action capability is not mapped for {action_type}")
        principal = self.principal
        if capability not in principal.capabilities:
            raise McpDelegatedActionAccessDenied(f"{capability} capability is required")
        return principal

    def update_task(self, task_id: str, expected_version: int, changes: dict[str, Any]) -> TaskMutationResult | ActionProposalResult:
        command = TaskUpdateCommand.model_validate({'task_id': task_id, 'expected_version': expected_version, 'changes': changes})
        action = self._propose_chat_action('task.update', '업무 내용·일정 수정 확인', command.model_dump(mode='json', exclude_unset=True))
        if action is not None:
            return action
        return self._application.update_task(self.principal, command.task_id, command.expected_version, command.changes())

    def list_meetings(self) -> list[dict[str, Any]]:
        entries = self._application.list_meetings(self.principal)
        self._remember_meetings(entries)
        return entries

    def my_meetings(self) -> list[dict[str, Any]]:
        entries = self._application.my_meetings(self.principal)
        self._remember_meetings(entries)
        return entries

    def _remember_meetings(self, entries: list[dict[str, Any]]) -> None:
        self._remember(
            [
                {"resource_type": "meeting", "resource_id": str(row["meeting_id"]), "resource_version": row.get("version")}
                for row in entries
                if row.get("kind") == "meeting"
            ]
        )

    def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        detail = self._application.get_meeting(self.principal, UUID(meeting_id))
        self._remember(
            [{"resource_type": "meeting", "resource_id": str(detail["meeting"]["meeting_id"]), "resource_version": None}]
        )
        return detail

    def create_current_meeting(
        self, request: MeetingReservationInput, idempotency_key: str | None = None
    ) -> dict[str, Any] | ActionProposalResult:
        payload = request.model_dump(mode="json")
        action = self._propose_chat_action("meeting.reservation.create", "회의 생성 확인", payload)
        if action is not None:
            return action
        return self._application.create_meeting(
            self.principal,
            room_id=request.room_id,
            idempotency_key=idempotency_key or self._mutation_key("meeting.reservation.create", payload),
            **request.values(),
        )

    def quick_start_meeting(self) -> dict[str, Any] | ActionProposalResult:
        action = self._propose_chat_action("meeting.quick_start", "회의 바로 시작 확인", {})
        return action if action is not None else self._application.quick_start_meeting(self.principal)

    def update_current_meeting(
        self, meeting_id: str, request: MeetingInfoPatch
    ) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "changes": request.changes()}
        action = self._propose_chat_action("meeting.info.update", "회의 수정 확인", payload)
        if action is not None:
            return action
        return self._application.update_meeting(self.principal, UUID(meeting_id), request.changes())

    def cancel_current_meeting(self, meeting_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id}
        action = self._propose_chat_action("meeting.cancel", "회의 취소 확인", payload)
        if action is not None:
            return action
        self._application.cancel_meeting(self.principal, UUID(meeting_id))
        return {"meeting_id": meeting_id, "state": "cancelled"}

    def delete_current_meeting_note(self, meeting_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id}
        action = self._propose_chat_action("meeting.note.delete", "회의록 삭제 확인", payload)
        if action is not None:
            return action
        self._application.delete_meeting_note(self.principal, UUID(meeting_id))
        return {"meeting_id": meeting_id, "state": "note_deleted"}

    def start_current_meeting(self, meeting_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id}
        action = self._propose_chat_action("meeting.start", "회의 시작 확인", payload)
        return action if action is not None else self._application.start_meeting(self.principal, UUID(meeting_id))

    def end_current_meeting(self, meeting_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id}
        action = self._propose_chat_action("meeting.end", "회의 종료 확인", payload)
        if action is not None:
            return action
        result = self._application.end_meeting(self.principal, UUID(meeting_id))
        asyncio.run(self._application.close_meeting_stream(UUID(meeting_id)))
        return result

    def retry_current_meeting_finalize(self, meeting_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id}
        action = self._propose_chat_action("meeting.finalize.retry", "회의 정리 다시 시도 확인", payload)
        return action if action is not None else self._application.retry_meeting_finalize(self.principal, UUID(meeting_id))

    def promote_current_meeting_todo(
        self, meeting_id: str, todo_id: str, request: MeetingTodoPromotionInput
    ) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "todo_id": todo_id, **request.model_dump(mode="json")}
        action = self._propose_chat_action("meeting.todo.promote", "회의 다음 할 일 요청 확인", payload)
        if action is not None:
            return action
        return self._application.promote_meeting_todo(
            self.principal, UUID(meeting_id), UUID(todo_id), **request.model_dump()
        )

    def remove_current_meeting_todo(self, meeting_id: str, todo_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "todo_id": todo_id}
        action = self._propose_chat_action("meeting.todo.remove", "회의 다음 할 일 삭제 확인", payload)
        if action is not None:
            return action
        self._application.remove_meeting_todo(self.principal, UUID(meeting_id), UUID(todo_id))
        return {**payload, "state": "removed"}

    def add_current_meeting_agenda(
        self, meeting_id: str, request: MeetingAgendaDraftInput
    ) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, **request.model_dump(mode="json")}
        action = self._propose_chat_action("meeting.agenda.add", "회의 안건 추가 확인", payload)
        return action if action is not None else self._application.add_meeting_agenda(
            self.principal, UUID(meeting_id), request.title
        )

    def update_current_meeting_agenda(
        self, meeting_id: str, agenda_id: str, request: MeetingAgendaPatch
    ) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "agenda_id": agenda_id, "changes": request.changes()}
        action = self._propose_chat_action("meeting.agenda.update", "회의 안건 수정 확인", payload)
        if action is not None:
            return action
        return self._application.update_meeting_agenda(
            self.principal, UUID(meeting_id), UUID(agenda_id), request.changes()
        )

    def remove_current_meeting_agenda(self, meeting_id: str, agenda_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "agenda_id": agenda_id}
        action = self._propose_chat_action("meeting.agenda.remove", "회의 안건 삭제 확인", payload)
        if action is not None:
            return action
        self._application.remove_meeting_agenda(self.principal, UUID(meeting_id), UUID(agenda_id))
        return {**payload, "state": "removed"}

    def write_current_meeting_memo(
        self, meeting_id: str, agenda_id: str, request: MeetingMemoInput
    ) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "agenda_id": agenda_id, **request.model_dump(mode="json")}
        action = self._propose_chat_action("meeting.memo.write", "회의 메모 작성 확인", payload)
        return action if action is not None else self._application.write_meeting_memo(
            self.principal, UUID(meeting_id), UUID(agenda_id), request.text
        )

    def detach_current_meeting_material(self, meeting_id: str, material_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "material_id": material_id}
        action = self._propose_chat_action("meeting.material.remove", "회의 자료 분리 확인", payload)
        if action is not None:
            return action
        self._application.detach_meeting_material(self.principal, UUID(meeting_id), UUID(material_id))
        return {**payload, "state": "detached"}

    def share_current_meeting(
        self, meeting_id: str, request: MeetingShareManyInput
    ) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, **request.model_dump(mode="json")}
        action = self._propose_chat_action("meeting.share_many", "회의 공유 확인", payload)
        return action if action is not None else {
            "viewers": self._application.share_meeting_with(
                self.principal, UUID(meeting_id), request.member_ids
            )
        }

    def revoke_current_meeting_share(self, meeting_id: str, member_id: str) -> dict[str, Any] | ActionProposalResult:
        payload = {"meeting_id": meeting_id, "member_id": member_id}
        action = self._propose_chat_action("meeting.share.revoke", "회의 공유 해제 확인", payload)
        return action if action is not None else {
            "viewers": self._application.revoke_meeting_share(
                self.principal, UUID(meeting_id), member_id
            )
        }

    def meeting_rooms(self, starts_at: str | None = None, ends_at: str | None = None) -> list[dict[str, Any]]:
        return self._application.meeting_rooms(
            starts_at=_parse_iso_datetime(starts_at) if starts_at else None,
            ends_at=_parse_iso_datetime(ends_at) if ends_at else None,
        )

    def meeting_transcript(self, meeting_id: str) -> list[dict[str, Any]]:
        return self._application.meeting_transcript(self.principal, UUID(meeting_id))

    def meeting_materials(self, meeting_id: str) -> list[dict[str, Any]]:
        return self._application.list_meeting_materials(self.principal, UUID(meeting_id))

    def meeting_viewers(self, meeting_id: str) -> list[dict[str, Any]]:
        return self._application.meeting_viewers(self.principal, UUID(meeting_id))

    def export_current_meeting(self, meeting_id: str) -> dict[str, str]:
        return self._application.export_meeting(self.principal, UUID(meeting_id))

    def list_members(self, meeting_id: str | None = None) -> list[dict[str, Any]]:
        """이 회의에 담긴 사람, 회의를 말하지 않으면 이 사람이 읽을 수 있는 조직의 사람."""
        if meeting_id:
            detail = self._application.get_meeting(self.principal, UUID(meeting_id))
            return list(detail["meeting"]["attendees"])
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for unit in self._application.organization_tree(self.principal):
            for member in self._application.organization_unit_members(self.principal, str(unit["id"])):
                identifier = str(member.get("member_id"))
                if identifier in seen:
                    continue
                seen.add(identifier)
                rows.append(member)
        return rows

    #: 히스토리 목록의 기본 10개는 화면 미리보기용이다 — 이 검색은 그보다 훨씬 넓게, 사실상 사용자의
    #: 전체 대화를 대상으로 훑어야 한다. 그래도 무한은 아니다: 한 사람의 대화 수에 현실적인 상한을 둔다.
    _SEARCH_CONVERSATION_SCOPE = 200

    def search_conversation_turns(self, query: str, limit: int = 5) -> ConversationSearchResult:
        """Find prior user Turns and remember exactly which cross-conversation sources this Turn observed."""
        needle = query.strip().casefold()
        if not needle:
            raise ValueError("conversation search query is required")
        bounded_limit = max(1, min(int(limit), 20))
        matches: list[dict[str, Any]] = []
        for summary in self._application.conversations(self.principal, limit=self._SEARCH_CONVERSATION_SCOPE):
            # A full-text search over one conversation's own history must not itself be windowed to the recent
            # page a screen would show — that would silently miss a match sitting earlier in a long conversation.
            detail = self._application.conversation(self.principal, UUID(str(summary["conversation_id"])), message_limit=None)
            for message in reversed(detail.get("messages") or []):
                body = str(message.get("body") or "").strip()
                if message.get("role") != "user" or not message.get("turn_id"):
                    continue
                if needle not in f"{summary.get('title', '')}\n{body}".casefold():
                    continue
                matches.append({
                    "turn_id": str(message["turn_id"]),
                    "conversation_id": str(summary["conversation_id"]),
                    "conversation_title": str(summary.get("title") or "대화"),
                    "excerpt": body[:400],
                    "created_at": message.get("created_at"),
                })
                if len(matches) >= bounded_limit:
                    break
            if len(matches) >= bounded_limit:
                break
        self._remember([
            {
                "resource_type": "conversation_turn",
                "resource_id": match["turn_id"],
                "parent_resource_id": match["conversation_id"],
            }
            for match in matches
        ])
        return {"turns": matches}

    def graph_overview(self, view: str = "member", limit: int = 120) -> GraphOverviewResult:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        return self._application.graph_overview(
            self.principal,
            view=view,
            limit=limit,
            execution_id=UUID(causation_id) if causation_id else None,
        )

    def graph_search(self, query: str, limit: int = 20) -> GraphSearchResult:
        """Inside a delegated turn, what this finds becomes that turn's own record of where it looked."""
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        return self._application.graph_search(
            self.principal, query, limit, execution_id=UUID(causation_id) if causation_id else None
        )

    def graph_neighbors(self, node: str, limit: int = 20) -> GraphNeighborsResult:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        return self._application.graph_neighbors(
            self.principal, node, limit, execution_id=UUID(causation_id) if causation_id else None
        )

    def task_subtasks(self, task_id: str) -> TaskSubtasksResult:
        task = self.get_task(task_id)
        return {
            "task_id": task_id,
            "parent": task.get("parent"),
            "children": task.get("children", []),
            "progress": task.get("child_progress"),
        }

    def task_checklist(self, task_id: str) -> TaskChecklistResult:
        task = self._application.get_task(self.principal, UUID(task_id))
        return {"task_id": task_id, "checklist": task.get("checklist", []), "progress": task.get("checklist_progress")}

    def add_checklist_item(self, task_id: str, text: str, expected_task_version: int | None = None) -> ChecklistMutationResult | ActionProposalResult:
        command = ChecklistAddCommand(task_id=task_id, text=text, expected_task_version=expected_task_version)
        action = self._propose_chat_action('task.checklist.add', '체크리스트 단계 추가 확인', command.model_dump(mode='json'))
        if action is not None:
            return action
        return self._application.add_task_checklist_item(self.principal, command.task_id, command.text, command.expected_task_version)

    def update_checklist_item(
        self, task_id: str, item_id: str, expected_version: int, text: str | None = None, done: bool | None = None, expected_task_version: int | None = None
    ) -> ChecklistMutationResult | ActionProposalResult:
        command = ChecklistUpdateCommand(task_id=task_id, item_id=item_id, expected_version=expected_version, text=text, done=done, expected_task_version=expected_task_version)
        payload = command.model_dump(mode='json')
        action = self._propose_chat_action("task.checklist.update", "체크리스트 단계 수정 확인", payload)
        if action is not None:
            return action
        return self._application.update_task_checklist_item(self.principal, command.task_id, command.item_id, **command.model_dump(exclude={'task_id', 'item_id'}, exclude_none=True))

    def update_task_progress_batch(self, operations: list[dict[str, Any]]) -> ActionProposalResult:
        """Propose one human-reviewed batch instead of losing same-kind effects in one delegated Turn."""
        action = self._propose_chat_action(
            "task.progress.batch",
            "업무 진행 일괄 반영 확인",
            {"operations": operations},
        )
        if action is None:
            raise ValueError("task progress batches require a delegated conversation turn")
        return action

    def archive_checklist_item(self, task_id: str, item_id: str, expected_version: int, expected_task_version: int | None = None) -> ChecklistMutationResult | ActionProposalResult:
        command = ChecklistArchiveCommand(task_id=task_id, item_id=item_id, expected_version=expected_version, expected_task_version=expected_task_version)
        payload = command.model_dump(mode='json')
        action = self._propose_chat_action("task.checklist.archive", "체크리스트 단계 정리 확인", payload)
        if action is not None:
            return action
        return self._application.archive_task_checklist_item(
            self.principal, command.task_id, command.item_id, expected_version=command.expected_version, expected_task_version=command.expected_task_version
        )

    def reorder_checklist(self, task_id: str, item_ids: list[str], expected_task_version: int | None = None) -> ChecklistOrderResult | ActionProposalResult:
        command = ChecklistOrderCommand(task_id=task_id, item_ids=item_ids, expected_task_version=expected_task_version)
        payload = command.model_dump(mode='json')
        action = self._propose_chat_action("task.checklist.reorder", "체크리스트 순서 변경 확인", payload)
        if action is not None:
            return action
        return self._application.reorder_task_checklist(self.principal, command.task_id, command.item_ids, expected_task_version=command.expected_task_version)

    def list_task_materials(self, task_id: str) -> list[TaskMaterialView]:
        materials = self._application.list_task_materials(self.principal, UUID(task_id))
        self._remember(
            [
                {"resource_type": "material", "resource_id": row["material_id"], "integrity_ref": row["integrity_ref"],
                 "source_contexts": [{"resource_type": "task", "resource_id": task_id, "binding_id": row["binding_id"]}]}
                for row in materials
            ]
        )
        return materials

    def task_assignment_candidates(self) -> list[MemberCandidateView]:
        return self._application.task_assignment_candidates(self.principal)

    def search_materials(self, query: str, *, resource_types: list[MaterialResourceType] | None = None,
                         resource_type: MaterialResourceType | None = None, resource_id: str | None = None,
                         material_id: str | None = None, limit: int = 5,
                         registered_from: str | None = None, registered_until: str | None = None) -> MaterialSearchResult:
        causation_id = os.getenv("AX_MCP_CAUSATION_ID")
        found = self._application.search_materials(self.principal, query, limit=limit, resource_types=resource_types,
            resource_type=resource_type, resource_id=resource_id, material_id=UUID(material_id) if material_id else None,
            registered_from=_parse_iso_date(registered_from), registered_until=_parse_iso_date(registered_until),
            execution_id=UUID(causation_id) if causation_id else None)
        seen = {hit["material_id"]: {"resource_type": "material", "resource_id": hit["material_id"],
                "source_contexts": hit["source_contexts"], "integrity_ref": hit["integrity_ref"], "source_locator": hit.get("source_locator")} for hit in reversed(found["results"])}
        self._remember(list(seen.values()))
        return found

    def task_assignment_inbox(self) -> list[dict[str, Any]]:
        return self._application.task_assignment_inbox(self.principal)

    def assign_task(
        self, title: str, assignee_id: str, description: str | None, start_date: str | None, due_date: str | None,
        checklist: list[str] | None = None, reference_task_ids: list[str] | None = None, parent_task_id: str | None = None,
    ) -> TaskAssignmentResult | ActionProposalResult:
        command = TaskAssignmentInput(title=title, assignee_id=assignee_id, description=description, start_date=start_date, due_date=due_date, checklist=checklist, reference_task_ids=reference_task_ids, parent_task_id=parent_task_id)
        action = self._propose_chat_action('task.assign', f'업무 배정 확인: {command.title}', command.model_dump(mode='json'))
        if action is not None:
            return action
        return self._application.assign_task(self.principal, **command.model_dump())

    def decide_task_assignment(self, assignment_id: str, decision: str, reason: str | None = None) -> dict[str, Any]:
        if decision not in {'accept', 'decline'}:
            raise ValueError('unsupported assignment decision')
        command = (AssignmentAcceptCommand(assignment_id=assignment_id) if decision == 'accept'
                   else AssignmentDeclineCommand(assignment_id=assignment_id, reason=reason or ''))
        action = self._propose_chat_action(
            f"task.assignment.{decision}", "업무 배정 수락 확인" if decision == "accept" else "업무 배정 거절 확인", command.model_dump(mode='json'),
        )
        if action is not None:
            return action
        if decision == 'accept':
            return self._application.accept_task_assignment(self.principal, command.assignment_id)
        return self._application.decline_task_assignment(self.principal, command.assignment_id, command.reason)

    def transition_task(
        self,
        task_id: str,
        target: str,
        expected_version: int,
        reason: str | None = None,
    ) -> TaskMutationResult | ActionProposalResult:
        command = TaskTransitionCommand(task_id=task_id, target=target, expected_version=expected_version, reason=reason)
        action = self._propose_chat_action('task.transition', '업무 상태 변경 확인', command.model_dump(mode='json'))
        if action is not None:
            return action
        from ax_workspace.modules.work.application import TaskState
        return self._application.transition_task(command.task_id, self.principal, TaskState(command.target), command.reason, command.expected_version)

    def member_directory(self) -> list[MemberDirectoryView]:
        return self._application.member_directory(self.principal)

    def organization_tree(self) -> list[OrganizationUnitView]:
        return self._application.organization_tree(self.principal)

    def organization_unit_members(self, unit_id: str) -> list[UnitMemberView]:
        return self._application.organization_unit_members(self.principal, unit_id)

    def organization_member_detail(self, member_id: str) -> MemberDetailView:
        return self._application.organization_member_detail(self.principal, member_id)

    def organization_member_history(self, member_id: str, axis: str) -> list[MemberAxisHistoryView]:
        return self._application.organization_member_history(self.principal, member_id, axis)

    def organization_activity(self, *, unit_id: str | None=None, limit: int=50, cursor: str | None=None) -> list[OrganizationActivityView]:
        return self._application.organization_activity(self.principal, unit_id=unit_id, limit=limit, cursor=cursor)

    def my_organization_profile(self) -> MyOrganizationProfileView:
        return self._application.my_organization_profile(self.principal)

    def installed_access_roles(self) -> list[InstalledRoleView]:
        return self._application.installed_access_roles(self.principal)

    def member_access(self, member_id: str) -> MemberAccessView:
        return self._application.member_access(self.principal, member_id)

    def daily_report_status(self, report_date: str) -> ReportStatusResult:
        return self._application.daily_report_status(self.principal, report_date)

    def daily_report_recent(self, *, limit: int=3) -> list[ReportRecentView]:
        return self._application.daily_report_recent(self.principal, limit=limit)

    def list_material_folders(self) -> list[FolderView]:
        return self._application.list_material_folders(self.principal)

    def create_material_folder(self, request: FolderCreateInput) -> FolderView | ActionProposalResult:
        action = self._propose_chat_action("material_folder.create", "자료함 생성 확인", request.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.create_material_folder(self.principal, **request.model_dump())

    def archive_material_folder(self, folder_id: str) -> FolderArchiveResult | ActionProposalResult:
        command = FolderArchiveCommand(folder_id=UUID(folder_id))
        action = self._propose_chat_action("material_folder.archive", "자료함 보관 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.archive_material_folder(self.principal, command.folder_id)

    def detach_folder_material(self, folder_id: str, material_id: str) -> FolderDetachResult | ActionProposalResult:
        command = FolderMaterialDetachCommand(folder_id=UUID(folder_id), material_id=UUID(material_id))
        action = self._propose_chat_action("material_folder.detach", "자료함에서 자료 분리 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.detach_folder_material(self.principal, command.folder_id, command.material_id)

    def list_folder_materials(self, folder_id: str) -> list[FolderMaterialView]:
        return self._application.list_folder_materials(self.principal, UUID(folder_id))

    def material_metadata(self, material_id: str) -> MaterialMetadataResult:
        return self._application.material_metadata(self.principal, UUID(material_id))

    def list_notifications(self) -> list[NotificationView]:
        return self._application.list_notifications(self.principal)

    def create_project(self, request: ProjectCreateInput) -> ProjectView | ActionProposalResult:
        action = self._propose_chat_action("project.create", "프로젝트 생성 확인", request.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.create_project(self.principal, **request.model_dump())

    def assign_to_project(self, project_id: str, request: ProjectMemberInput) -> ProjectAssignmentView | ActionProposalResult:
        command = ProjectMemberCommand(project_id=UUID(project_id), **request.model_dump())
        action = self._propose_chat_action("project.assign_member", "프로젝트 참여 배정 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.assign_to_project(self.principal, **command.model_dump())

    def release_from_project(self, project_id: str, member_id: str, assignment_id: str, reason: str | None = None) -> ProjectReleaseResult | ActionProposalResult:
        command = ProjectReleaseCommand(project_id=UUID(project_id), member_id=member_id, assignment_id=UUID(assignment_id), reason=reason)
        action = self._propose_chat_action("project.release_member", "프로젝트 참여 해제 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.release_from_project(self.principal, **command.model_dump())

    def project_participation_history(self, project_id: str) -> list[ProjectParticipationView]:
        return self._application.project_participation_history(self.principal, UUID(project_id))

    def plan_project_work(self, project_id: str, request: ProjectWorkInput) -> TaskMutationResult | ActionProposalResult:
        command = ProjectWorkCommand(project_id=UUID(project_id), **request.model_dump())
        action = self._propose_chat_action("project.plan_work", "프로젝트 업무 계획 확인", command.model_dump(mode="json"))
        if action is not None:
            return action
        return self._application.plan_project_work(self.principal, **command.model_dump())

    def list_projects(self) -> list[ProjectView]:
        return self._application.list_projects(self.principal)

    def get_project(self, project_id: str) -> ProjectDetailResult:
        return self._application.get_project(self.principal, UUID(project_id))

    def sent_task_assignments(self) -> list[TaskAssignmentResult]:
        return self._application.sent_task_assignments(self.principal)

    def work_request_cc_candidates(self) -> list[MemberCandidateView]:
        return self._application.work_request_cc_candidates(self.principal)

    def task_history_diff(self, task_id: str, before: int, after: int) -> TaskHistoryDiffResult:
        return self._application.task_history_diff(self.principal, UUID(task_id), before, after)

    def conversations(self) -> list[ConversationView]:
        return self._application.conversations(self.principal)

    def conversation(self, conversation_id: str) -> ConversationView:
        return self._application.conversation(self.principal, UUID(conversation_id))


    def open_task_material(self, task_id: str, material_id: str) -> MaterialOpenResult:
        owner_id, artifact_id = UUID(task_id), UUID(material_id)
        metadata, content = self._application.open_task_material(self.principal, owner_id, artifact_id)
        return MaterialOpenResult(
            material_id=str(artifact_id), name=metadata["name"], content_type=metadata["content_type"],
            size_bytes=len(content), open_url=f"/api/tasks/{owner_id}/materials/{artifact_id}/content",
        )

    def open_folder_material(self, folder_id: str, material_id: str) -> MaterialOpenResult:
        owner_id, artifact_id = UUID(folder_id), UUID(material_id)
        metadata, content = self._application.open_folder_material(self.principal, owner_id, artifact_id)
        return MaterialOpenResult(
            material_id=str(artifact_id), name=metadata["name"], content_type=metadata["content_type"],
            size_bytes=len(content), open_url=f"/api/material-folders/{owner_id}/materials/{artifact_id}/content",
        )

    def open_meeting_material(self, meeting_id: str, material_id: str) -> MaterialOpenResult:
        owner_id, artifact_id = UUID(meeting_id), UUID(material_id)
        metadata, content = self._application.open_meeting_material(self.principal, owner_id, artifact_id)
        return MaterialOpenResult(
            material_id=str(artifact_id), name=metadata["name"], content_type=metadata["content_type"],
            size_bytes=len(content), open_url=f"/api/meetings/{owner_id}/materials/{artifact_id}/content",
        )

    def open_report_material(self, report_id: str, material_id: str) -> MaterialOpenResult:
        owner_id, artifact_id = UUID(report_id), UUID(material_id)
        metadata, content = self._application.open_report_material(self.principal, owner_id, artifact_id)
        return MaterialOpenResult(
            material_id=str(artifact_id), name=metadata["name"], content_type=metadata["content_type"],
            size_bytes=len(content), open_url=f"/api/daily-reports/{owner_id}/materials/{artifact_id}/content",
        )

    def request_file_attachment(self, request: BrowserFileRequest) -> BrowserInteractionResult:
        return self._application.request_file_attachment(self.principal, request)

    def request_recording(self, request: BrowserRecordingRequest) -> BrowserInteractionResult:
        return self._application.request_recording(self.principal, request)

    def browser_interaction(self, interaction_id: UUID) -> BrowserInteractionResult:
        return self._application.browser_interaction(self.principal, interaction_id)

    def open_work_request_attachment(self, request_id: str, attachment_id: str) -> MaterialOpenResult:
        owner_id, artifact_id = UUID(request_id), UUID(attachment_id)
        metadata, content = self._application.open_work_request_attachment(self.principal, owner_id, artifact_id)
        return MaterialOpenResult(
            material_id=str(artifact_id), name=metadata["name"], content_type=metadata["content_type"],
            size_bytes=len(content), open_url=f"/api/work-requests/{owner_id}/attachments/{artifact_id}/content",
        )


def create_mcp_server(settings: Settings | None = None) -> MCPServer:
    bound_persona = os.getenv("AX_MCP_PERSONA")
    if not bound_persona:
        raise RuntimeError("AX_MCP_PERSONA must bind this MCP server to an allow-listed demo persona")
    facade = McpReportsFacade(settings or Settings.from_environment(), bound_persona)
    return _create_bound_persona_server(facade)


def _create_bound_persona_server(facade: McpReportsFacade) -> MCPServer:
    principal = facade.principal
    server = PersonaMcpServer(
        facade,
        f"SCAX — {principal.display_name}",
        delegated=bool(os.getenv("AX_MCP_CAUSATION_ID")),
        instructions=(
            "This server is bound to one person and calls canonical business operations with current permissions. "
            "In a delegated chat, edits and submissions prepare human confirmation. Report draft generation is a "
            "separate operation; never infer its completion from having requested it. "
            "Every human judgement — a WorkRequest, a Task assignment, an AX gated proposal — is one ActionItem: read "
            "it with action_item_list and action_item_get, and answer it with action_item_command using only the "
            "allowed_commands the server put on that item. Never decide from the kind or the status; there is no other "
            "way to accept, adjust, reject, revise, withdraw or decline anything. Inside a delegated chat turn the "
            "command does not take effect: it returns a pending confirmation for the person to approve."
        ),
    )
    _register_action_item_tools(server, facade)
    _register_conversation_tools(server, facade)
    _register_additional_query_tools(server, facade)
    _register_daily_report_tools(server, facade)
    _register_graph_tools(server, facade)
    _register_task_tools(server, facade)
    _register_material_tools(server, facade)
    _register_meeting_tools(server, facade)
    _register_project_tools(server, facade)
    _register_action_material_tools(server, facade)
    _register_conversation_command_tools(server, facade)
    _register_personal_command_tools(server, facade)
    _register_folder_tools(server, facade)
    _register_task_relationship_tools(server, facade)
    _register_work_request_read_tools(server, facade)
    _register_work_request_create_tools(server, facade)
    @server.tool(structured_output=True)
    def file_attachment_request(request: BrowserFileRequest) -> BrowserInteractionResult:
        return facade.request_file_attachment(request)

    @server.tool(structured_output=True)
    def recording_request(request: BrowserRecordingRequest) -> BrowserInteractionResult:
        return facade.request_recording(request)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def browser_interaction_get(interaction_id: UUID) -> BrowserInteractionResult:
        return facade.browser_interaction(interaction_id)

    server.validate_catalog()
    return server


def _register_additional_query_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def open_task_material(task_id: str, material_id: str) -> MaterialOpenResult:
        return facade.open_task_material(task_id, material_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def open_folder_material(folder_id: str, material_id: str) -> MaterialOpenResult:
        return facade.open_folder_material(folder_id, material_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def open_meeting_material(meeting_id: str, material_id: str) -> MaterialOpenResult:
        return facade.open_meeting_material(meeting_id, material_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def open_report_material(report_id: str, material_id: str) -> MaterialOpenResult:
        return facade.open_report_material(report_id, material_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def open_work_request_attachment(request_id: str, attachment_id: str) -> MaterialOpenResult:
        return facade.open_work_request_attachment(request_id, attachment_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def member_directory() -> list[MemberDirectoryView]:
        return facade.member_directory()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def organization_tree() -> list[OrganizationUnitView]:
        return facade.organization_tree()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def organization_unit_members(unit_id: str) -> list[UnitMemberView]:
        return facade.organization_unit_members(unit_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def organization_member_detail(member_id: str) -> MemberDetailView:
        return facade.organization_member_detail(member_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def organization_member_history(member_id: str, axis: str) -> list[MemberAxisHistoryView]:
        return facade.organization_member_history(member_id, axis)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def organization_activity(*, unit_id: str | None=None, limit: int=50, cursor: str | None=None) -> list[OrganizationActivityView]:
        return facade.organization_activity(unit_id=unit_id, limit=limit, cursor=cursor)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def my_organization_profile() -> MyOrganizationProfileView:
        return facade.my_organization_profile()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def installed_access_roles() -> list[InstalledRoleView]:
        return facade.installed_access_roles()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def member_access(member_id: str) -> MemberAccessView:
        return facade.member_access(member_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def daily_report_status(report_date: str) -> ReportStatusResult:
        return facade.daily_report_status(report_date)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def daily_report_recent(*, limit: int=3) -> list[ReportRecentView]:
        return facade.daily_report_recent(limit=limit)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def list_material_folders() -> list[FolderView]:
        return facade.list_material_folders()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def list_folder_materials(folder_id: str) -> list[FolderMaterialView]:
        return facade.list_folder_materials(folder_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def material_metadata(material_id: str) -> MaterialMetadataResult:
        return facade.material_metadata(material_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def list_notifications() -> list[NotificationView]:
        return facade.list_notifications()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def list_projects() -> list[ProjectView]:
        return facade.list_projects()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def get_project(project_id: str) -> ProjectDetailResult:
        return facade.get_project(project_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def sent_task_assignments() -> list[TaskAssignmentResult]:
        return facade.sent_task_assignments()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def work_request_cc_candidates() -> list[MemberCandidateView]:
        return facade.work_request_cc_candidates()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def task_history_diff(task_id: str, before: int, after: int) -> TaskHistoryDiffResult:
        return facade.task_history_diff(task_id, before, after)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def conversations() -> list[ConversationView]:
        return facade.conversations()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def conversation(conversation_id: str) -> ConversationView:
        return facade.conversation(conversation_id)


def _register_conversation_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def conversation_search(query: str, limit: int = 5) -> ConversationSearchResult:
        return facade.search_conversation_turns(query, limit)


def _register_action_item_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    """The one judgement ledger. Policy is the server's envelope; this adapter adds no transition of its own."""

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def action_item_list() -> list[ActionEnvelopeResult]:
        return facade.pending_action_items()

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def action_item_get(action_item_id: str) -> ActionDetailResult:
        return facade.action_item_detail(action_item_id)


    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def action_item_command(
        action_item_id: str,
        command: str,
        expected_version: int,
        base_submission_version: int | None = None,
        draft: dict[str, JsonValue] | None = None,
        attachment_draft_ids: list[UUID] | None = None,
        reason: Annotated[str | None, ActionCommandInput.model_fields["reason"]] = None,
        changes: dict[str, JsonValue] | None = None,
    ) -> CommandResult[ActionEnvelopeResult]:
        return CommandResult[ActionEnvelopeResult](facade.run_action_command(
            action_item_id,
            command,
            expected_version=expected_version,
            base_submission_version=base_submission_version,
            draft=draft,
            attachment_draft_ids=[str(value) for value in attachment_draft_ids] if attachment_draft_ids is not None else None,
            reason=reason,
            changes=changes,
        ))


def _register_daily_report_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool()
    def daily_report_generate_draft(report_date: str) -> ReportStatusResult:
        return facade.generate_daily_report_draft(report_date)

    @server.tool()
    def daily_report_edit(
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[ReportSourceReference] | None = None,
        exclude_source_refs: list[ReportSourceReference] | None = None,
    ) -> CommandResult[ReportDraftResult]:
        return CommandResult[ReportDraftResult](facade.edit_daily_report(
            report_id,
            draft_id,
            expected_version,
            body,
            include_source_refs,
            exclude_source_refs,
        ))

    @server.tool()
    def daily_report_submit(
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None = None,
    ) -> CommandResult[ReportSubmissionResult]:
        return CommandResult[ReportSubmissionResult](facade.submit_daily_report(report_id, draft_id, expected_version, reason))

    @server.tool()
    def daily_report_history(report_id: str) -> ReportHistoryResult:
        return facade.daily_report_history(report_id)


def _register_work_request_read_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool()
    def work_request_list() -> list[WorkRequestMutationResult]:
        return facade.list_work_requests()

    @server.tool()
    def work_request_get(request_id: str) -> WorkRequestDetailResult:
        return facade.get_work_request(request_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def work_request_history(request_id: str) -> WorkRequestHistoryResult:
        return facade.work_request_history(request_id)

def _register_work_request_create_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool()
    def work_request_assignee_candidates() -> list[MemberCandidateView]:
        return facade.work_request_assignee_candidates()

    @server.tool()
    def work_request_amend(
        request_id: str, expected_version: int, title: Annotated[str | None, WorkRequestRevisionInput.model_fields['title']] = None, description: str | None = None,
        due_date: str | None = None, clear_due_date: bool = False,
    ) -> CommandResult[WorkRequestMutationResult]:
        return CommandResult[WorkRequestMutationResult](facade.amend_work_request(request_id, expected_version, title, description, due_date, clear_due_date))

    @server.tool()
    def work_request_create(
        title: Annotated[str, WorkRequestCreateInput.model_fields['title']], assignee_id: str, due_date: str | None = None, description: str | None = None,
        cc_member_ids: list[str] | None = None, checklist: list[str] | None = None,
        reference_task_ids: list[str] | None = None,
    ) -> CommandResult[WorkRequestMutationResult]:
        return CommandResult[WorkRequestMutationResult](facade.create_work_request(
            title, assignee_id, due_date, description, cc_member_ids, checklist, reference_task_ids
        ))


def _register_meeting_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_list() -> dict[str, Any]:
        return {"entries": facade.list_meetings()}

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def my_meeting_list() -> dict[str, Any]:
        return {"entries": facade.my_meetings()}

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_get(meeting_id: str) -> dict[str, Any]:
        return facade.get_meeting(meeting_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_room_list(starts_at: str | None = None, ends_at: str | None = None) -> list[dict[str, Any]]:
        return facade.meeting_rooms(starts_at, ends_at)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_transcript(meeting_id: str) -> list[dict[str, Any]]:
        return facade.meeting_transcript(meeting_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_materials_list(meeting_id: str) -> list[dict[str, Any]]:
        return facade.meeting_materials(meeting_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_viewer_list(meeting_id: str) -> list[dict[str, Any]]:
        return facade.meeting_viewers(meeting_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def meeting_export(meeting_id: str) -> dict[str, str]:
        return facade.export_current_meeting(meeting_id)

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def project_list() -> dict[str, Any]:
        return {"entries": facade.list_projects()}

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def member_list(meeting_id: str | None = None) -> dict[str, Any]:
        return {"entries": facade.list_members(meeting_id)}

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_create(request: McpMeetingReservationInput) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](
            facade.create_current_meeting(request.reservation_input(), request.idempotency_key)
        )

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_quick_start() -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.quick_start_meeting())

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_update(meeting_id: str, request: MeetingInfoPatch) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.update_current_meeting(meeting_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_cancel(meeting_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.cancel_current_meeting(meeting_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_note_delete(meeting_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.delete_current_meeting_note(meeting_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_start(meeting_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.start_current_meeting(meeting_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_end(meeting_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.end_current_meeting(meeting_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_finalize_retry(meeting_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.retry_current_meeting_finalize(meeting_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_todo_promote(meeting_id: str, todo_id: str, request: MeetingTodoPromotionInput) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.promote_current_meeting_todo(meeting_id, todo_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_todo_remove(meeting_id: str, todo_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.remove_current_meeting_todo(meeting_id, todo_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_agenda_add(meeting_id: str, request: MeetingAgendaDraftInput) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.add_current_meeting_agenda(meeting_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_agenda_update(meeting_id: str, agenda_id: str, request: MeetingAgendaPatch) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.update_current_meeting_agenda(meeting_id, agenda_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_agenda_remove(meeting_id: str, agenda_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.remove_current_meeting_agenda(meeting_id, agenda_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_memo_write(meeting_id: str, agenda_id: str, request: MeetingMemoInput) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.write_current_meeting_memo(meeting_id, agenda_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_material_detach(meeting_id: str, material_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.detach_current_meeting_material(meeting_id, material_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_share(meeting_id: str, request: MeetingShareManyInput) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.share_current_meeting(meeting_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def meeting_revoke_share(meeting_id: str, member_id: str) -> CommandResult[dict[str, Any]]:
        return CommandResult[dict[str, Any]](facade.revoke_current_meeting_share(meeting_id, member_id))


def _register_action_material_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def action_material_link_stage(action_item_id: str, request: ActionMaterialLinkInput) -> CommandResult[ActionMaterialDraftView]:
        return CommandResult[ActionMaterialDraftView](facade.stage_action_material_link(action_item_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def action_material_draft_discard(action_item_id: str, material_draft_id: str) -> CommandResult[ActionMaterialDraftView]:
        return CommandResult[ActionMaterialDraftView](facade.discard_action_material_draft(action_item_id, material_draft_id))

def _register_conversation_command_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def conversation_create(request: ConversationCreateInput) -> CommandResult[ConversationView]:
        return CommandResult[ConversationView](facade.create_conversation(request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def conversation_message_send(conversation_id: str, request: ConversationMessageInput, idempotency_key: str | None = None) -> CommandResult[ConversationMessageResult]:
        return CommandResult[ConversationMessageResult](facade.accept_conversation_message(conversation_id, request, idempotency_key))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def conversation_turn_cancel(conversation_id: str, expected_version: int) -> CommandResult[ConversationView]:
        return CommandResult[ConversationView](facade.cancel_conversation_turn(conversation_id, expected_version))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def conversation_turn_retry(conversation_id: str, turn_id: str) -> CommandResult[ConversationRetryResult]:
        return CommandResult[ConversationRetryResult](facade.retry_conversation_turn(conversation_id, turn_id))

def _register_personal_command_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def notification_mark_read(notification_id: str) -> CommandResult[NotificationView]:
        return CommandResult[NotificationView](facade.mark_notification_read(notification_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def assistant_character_set(request: AssistantCharacterInput) -> CommandResult[AssistantCharacterResult]:
        return CommandResult[AssistantCharacterResult](facade.set_assistant_character(request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def work_request_comment_add(request_id: str, body: str, idempotency_key: str | None = None) -> CommandResult[ActionDiscussionView]:
        return CommandResult[ActionDiscussionView](facade.add_work_request_comment(request_id, body, idempotency_key))

def _register_folder_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def material_folder_create(request: FolderCreateInput) -> CommandResult[FolderView]:
        return CommandResult[FolderView](facade.create_material_folder(request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def material_folder_archive(folder_id: str) -> CommandResult[FolderArchiveResult]:
        return CommandResult[FolderArchiveResult](facade.archive_material_folder(folder_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def folder_material_detach(folder_id: str, material_id: str) -> CommandResult[FolderDetachResult]:
        return CommandResult[FolderDetachResult](facade.detach_folder_material(folder_id, material_id))

def _register_task_relationship_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_material_attach_link(task_id: str, request: TaskMaterialLinkInput) -> CommandResult[TaskMaterialResult]:
        return CommandResult[TaskMaterialResult](facade.attach_task_material_link(task_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_material_attach_reference(task_id: str, request: TaskMaterialReferenceInput) -> CommandResult[TaskMaterialResult]:
        return CommandResult[TaskMaterialResult](facade.attach_task_material_reference(task_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_material_detach(task_id: str, binding_id: str) -> CommandResult[TaskMaterialResult]:
        return CommandResult[TaskMaterialResult](facade.detach_task_material(task_id, binding_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_reassign(task_id: str, request: TaskReassignInput) -> CommandResult[TaskAssignmentResult]:
        return CommandResult[TaskAssignmentResult](facade.reassign_task(task_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_completion_submit(task_id: str, request: TaskCompletionInput) -> CommandResult[TaskCompletionResult]:
        return CommandResult[TaskCompletionResult](facade.submit_task_completion(task_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_reference_add(task_id: str, referenced_task_id: str) -> CommandResult[TaskReferenceResult]:
        return CommandResult[TaskReferenceResult](facade.add_task_reference(task_id, referenced_task_id))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_reference_release(task_id: str, reference_id: str) -> CommandResult[TaskReferenceReleaseResult]:
        return CommandResult[TaskReferenceReleaseResult](facade.release_task_reference(task_id, reference_id))

def _register_project_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def project_create(request: ProjectCreateInput) -> CommandResult[ProjectView]:
        return CommandResult[ProjectView](facade.create_project(request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def project_assign_member(project_id: str, request: ProjectMemberInput) -> CommandResult[ProjectAssignmentView]:
        return CommandResult[ProjectAssignmentView](facade.assign_to_project(project_id, request))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def project_release_member(project_id: str, member_id: str, assignment_id: str, reason: str | None = None) -> CommandResult[ProjectReleaseResult]:
        return CommandResult[ProjectReleaseResult](facade.release_from_project(project_id, member_id, assignment_id, reason))

    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def project_participation_history(project_id: str) -> list[ProjectParticipationView]:
        return facade.project_participation_history(project_id)

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def project_plan_work(project_id: str, request: ProjectWorkInput) -> CommandResult[TaskMutationResult]:
        return CommandResult[TaskMutationResult](facade.plan_project_work(project_id, request))


def _register_graph_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def graph_overview(view: str = "member", limit: int = 120) -> GraphOverviewResult:
        return facade.graph_overview(view, limit)
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def graph_search(query: str, limit: int = 20) -> GraphSearchResult:
        return facade.graph_search(query, limit)
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def graph_neighbors(node: str, limit: int = 20) -> GraphNeighborsResult:
        return facade.graph_neighbors(node, limit)


def _register_material_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def material_search(query: str, resource_types: list[MaterialResourceType] | None = None,
                        resource_type: MaterialResourceType | None = None, resource_id: str | None = None,
                        material_id: str | None = None, limit: int = 5,
                        registered_from: str | None = None, registered_until: str | None = None) -> MaterialSearchResult:
        try:
            return facade.search_materials(query, resource_types=resource_types, resource_type=resource_type,
                resource_id=resource_id, material_id=material_id, limit=limit,
                registered_from=registered_from, registered_until=registered_until)
        except MaterialNotFound:
            raise
        except MaterialError as error:
            raise ToolError(str(error)[:200]) from error
        except ValueError as error:
            raise ToolError("invalid material search request") from error
        except Exception as error:
            raise ToolError("material search is temporarily unavailable") from error


def _register_task_tools(server: MCPServer, facade: McpReportsFacade) -> None:
    @server.tool()
    def my_task_list(include_closed: bool = False) -> list[TaskListEntry]:
        return facade.my_work(include_closed=include_closed)
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def task_list(include_closed: bool = False) -> list[TaskListEntry]:
        return facade.list_tasks(include_closed=include_closed)
    @server.tool()
    def task_get(task_id: str) -> TaskDetailResult:
        return facade.get_task(task_id)
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def task_history(task_id: str) -> TaskHistoryResult:
        return facade.task_history(task_id)
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def task_subtask_list(task_id: str) -> TaskSubtasksResult:
        return facade.task_subtasks(task_id)
    @server.tool(annotations=_READ_ONLY_TOOL, structured_output=True)
    def task_checklist_list(task_id: str) -> TaskChecklistResult:
        return facade.task_checklist(task_id)
    @server.tool()
    def task_materials_list(task_id: str) -> list[TaskMaterialView]:
        return facade.list_task_materials(task_id)


    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_checklist_add(task_id: str, text: str, expected_task_version: int | None = None) -> CommandResult[ChecklistMutationResult]:
        return CommandResult[ChecklistMutationResult](facade.add_checklist_item(task_id, text, expected_task_version))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_checklist_update(
        task_id: str, item_id: str, expected_version: int, text: str | None = None, done: bool | None = None, expected_task_version: int | None = None
    ) -> CommandResult[ChecklistMutationResult]:
        return CommandResult[ChecklistMutationResult](facade.update_checklist_item(task_id, item_id, expected_version, text, done, expected_task_version))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_progress_batch(operations: list[dict[str, Any]]) -> ActionProposalResult:
        return facade.update_task_progress_batch(operations)

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_checklist_archive(task_id: str, item_id: str, expected_version: int, expected_task_version: int | None = None) -> CommandResult[ChecklistMutationResult]:
        return CommandResult[ChecklistMutationResult](facade.archive_checklist_item(task_id, item_id, expected_version, expected_task_version))

    @server.tool(annotations=_COMMAND_TOOL, structured_output=True)
    def task_checklist_reorder(task_id: str, item_ids: list[str], expected_task_version: int | None = None) -> CommandResult[ChecklistOrderResult]:
        return CommandResult[ChecklistOrderResult](facade.reorder_checklist(task_id, item_ids, expected_task_version))

    @server.tool()
    def task_create_self(
        title: Annotated[str, TaskCreateInput.model_fields["title"]],
        checklist: list[str] | None = None,
        reference_task_ids: list[str] | None = None,
        parent_task_id: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        project_id: str | None = None,
    ) -> CommandResult[TaskMutationResult]:
        return CommandResult[TaskMutationResult](facade.create_self_task(
            title,
            checklist,
            reference_task_ids,
            parent_task_id,
            description,
            start_date,
            due_date,
            project_id,
        ))

    @server.tool()
    def task_assignment_candidates() -> list[MemberCandidateView]:
        return facade.task_assignment_candidates()
    @server.tool()
    def task_assign(
        title: Annotated[str, TaskAssignmentInput.model_fields['title']], assignee_id: str, description: str | None = None, start_date: str | None = None,
        due_date: str | None = None, checklist: list[str] | None = None,
        reference_task_ids: list[str] | None = None, parent_task_id: str | None = None,
    ) -> CommandResult[TaskAssignmentResult]:
        return CommandResult[TaskAssignmentResult](facade.assign_task(title, assignee_id, description, start_date, due_date, checklist, reference_task_ids, parent_task_id))

    @server.tool()
    def task_update(
        task_id: str,
        expected_version: int,
        title: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        clear_start_date: bool = False,
        clear_due_date: bool = False,
        project_id: str | None = None,
        clear_project: bool = False,
    ) -> CommandResult[TaskMutationResult]:
        request = TaskUpdateInput(
            expected_version=expected_version, title=title, description=description,
            start_date=start_date or None, due_date=due_date or None,
            clear_start_date=clear_start_date or start_date == '',
            clear_due_date=clear_due_date or due_date == '',
            project_id=project_id, clear_project=clear_project,
        )
        return CommandResult[TaskMutationResult](facade.update_task(task_id, request.expected_version, request.changes()))

    def transition(
        name: str,
        target: str,
    ) -> None:
        @server.tool(name=name)
        def task_transition(
            task_id: str,
            expected_version: int,
            reason: str | None = None,
        ) -> CommandResult[TaskMutationResult]:
            return CommandResult[TaskMutationResult](facade.transition_task(task_id, target, expected_version, reason))

    transition("task_start", "in_progress")
    transition("task_block", "blocked")
    transition("task_resume", "in_progress")
    transition("task_complete", "done")
    transition("task_cancel", "cancelled")

def _parse_iso_date(value: Any):
    if value in (None, ""):
        return None
    from datetime import date

    return date.fromisoformat(str(value))


def _parse_iso_datetime(value: Any):
    from datetime import datetime

    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("meeting times must include a timezone")
    return parsed


def main() -> None:
    create_mcp_server().run(transport="stdio")


# 서버를 띄우는 줄은 파일의 맨 끝에 있어야 한다. `python -m`으로 실행하면 이 줄에서 module이 곧장 돌기 시작하고,
# 아래에 남은 것은 아직 정의되지 않은 채로 도구가 그것을 부른다. 그 실패는 protocol이 이유를 지운 채 전달하므로
# 오래 보이지 않을 수 있다.
if __name__ == "__main__":
    main()
