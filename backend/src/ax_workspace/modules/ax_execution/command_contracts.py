"""Typed confirmation payloads and the immutable identity each approval answers.

This registry validates values only. Owning applications are explicitly bound in
bootstrap and invoked by the approval executor; no service is selected here.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel
from ax_workspace.modules.reports.commands import ReportEditCommand, ReportSubmitCommand

from ax_workspace.modules.ax_execution.conversation_commands import ConversationCreateInput, ConversationMessageCommand, ConversationCancelCommand, ConversationRetryCommand
from ax_workspace.modules.meetings.commands import MeetingUpdateCommand, MeetingShareCommand, MeetingNoteCreateCommand, MeetingNoteSaveCommand, MeetingNoteFinalizeCommand, MeetingMaterialLinkCommand, MeetingMaterialDetachCommand, MeetingSummaryAdoptCommand, MeetingSpeakerCommand, MeetingFollowupCommand
from ax_workspace.modules.notifications import NotificationReadCommand
from ax_workspace.modules.organization_access.commands import AssistantCharacterInput
from ax_workspace.modules.work.folder_commands import FolderCreateInput, FolderArchiveCommand, FolderMaterialDetachCommand
from ax_workspace.modules.work.material_commands import TaskMaterialLinkCommand, TaskMaterialReferenceCommand, TaskMaterialDetachCommand, ActionMaterialLinkCommand, ActionMaterialDiscardCommand
from ax_workspace.modules.work.project_commands import ProjectCreateInput, ProjectMemberCommand, ProjectReleaseCommand, ProjectWorkCommand
from ax_workspace.modules.work.request_commands import WorkRequestAmendCommand, WorkRequestCommentCommand
from ax_workspace.modules.work.checklist_commands import ChecklistAddCommand, ChecklistUpdateCommand, ChecklistArchiveCommand, ChecklistOrderCommand
from ax_workspace.modules.work.task_commands import TaskTransitionCommand, TaskUpdateCommand, TaskCompletionCommand, TaskReferenceCommand, TaskReferenceReleaseCommand, TaskReassignCommand


@dataclass(frozen=True)
class CommandContract:
    model: type[BaseModel]
    fixed_fields: frozenset[str]

    def normalize(self, payload: Any) -> dict[str, Any]:
        command = self.model.model_validate(payload)
        if isinstance(command, ReportEditCommand):
            return command.values()
        return command.model_dump(mode='json', exclude_unset=self.model in {MeetingUpdateCommand, TaskUpdateCommand})

    def validate_edit(self, original: dict, final: dict) -> None:
        changed = [key for key in self.fixed_fields if original.get(key) != final.get(key)]
        if changed:
            raise ValueError('승인 대상이나 기준 버전은 바꿀 수 없습니다. 새 변경을 준비해 주세요.')

    def empty_policy(self, key: str, definition: dict[str, Any]) -> str:
        if self.model is WorkRequestAmendCommand:
            if key == 'description':
                return 'empty_string'
            if key in {'title', 'due_date'}:
                return 'omit'
        # Meeting edits distinguish absence from an explicit clear. Pydantic's
        # nullable placeholder alone cannot express the after-validator rule.
        if self.model is TaskTransitionCommand and key == 'reason':
            return 'forbid'
        if (self.model is MeetingUpdateCommand and key != 'description') or (self.model is TaskUpdateCommand and key == 'title'):
            return 'omit'
        if any(item.get('type') == 'null' for item in definition.get('anyOf', [])):
            return 'null'
        return 'forbid'


def _contract(model: type[BaseModel], *fixed_fields: str) -> CommandContract:
    unknown = set(fixed_fields) - set(model.model_fields)
    if unknown:
        raise ValueError(f'Unknown fixed fields on {model.__name__}: {sorted(unknown)}')
    return CommandContract(model, frozenset(fixed_fields))


COMMAND_CONTRACTS = MappingProxyType({
    'daily_report.edit': _contract(ReportEditCommand, 'report_id', 'draft_id', 'expected_version'),
    'daily_report.submit': _contract(ReportSubmitCommand, 'report_id', 'draft_id', 'expected_version'),
    'project.create': _contract(ProjectCreateInput),
    'project.assign_member': _contract(ProjectMemberCommand, 'project_id'),
    'project.release_member': _contract(ProjectReleaseCommand, 'project_id', 'member_id', 'assignment_id'),
    'project.plan_work': _contract(ProjectWorkCommand, 'project_id'),
    'material_folder.create': _contract(FolderCreateInput, 'kind', 'organization_id'),
    'material_folder.archive': _contract(FolderArchiveCommand, 'folder_id'),
    'material_folder.detach': _contract(FolderMaterialDetachCommand, 'folder_id', 'material_id'),
    'task.checklist.add': _contract(ChecklistAddCommand, 'task_id', 'expected_task_version'),
    'task.checklist.update': _contract(ChecklistUpdateCommand, 'task_id', 'item_id', 'expected_version', 'expected_task_version'),
    'task.checklist.archive': _contract(ChecklistArchiveCommand, 'task_id', 'item_id', 'expected_version', 'expected_task_version'),
    'task.checklist.reorder': _contract(ChecklistOrderCommand, 'task_id', 'expected_task_version'),
    'task.transition': _contract(TaskTransitionCommand, 'task_id', 'expected_version', 'target'),
    'task.update': _contract(TaskUpdateCommand, 'task_id', 'expected_version'),
    'task.reassign': _contract(TaskReassignCommand, 'task_id', 'expected_version'),
    'task.completion.submit': _contract(TaskCompletionCommand, 'task_id', 'expected_version'),
    'task.reference.add': _contract(TaskReferenceCommand, 'task_id'),
    'task.reference.release': _contract(TaskReferenceReleaseCommand, 'task_id', 'reference_id'),
    'task.material.attach_link': _contract(TaskMaterialLinkCommand, 'task_id'),
    'task.material.attach_reference': _contract(TaskMaterialReferenceCommand, 'task_id', 'resource_type'),
    'task.material.detach': _contract(TaskMaterialDetachCommand, 'task_id', 'binding_id'),
    'meeting.update': _contract(MeetingUpdateCommand, 'meeting_id', 'expected_version'),
    'meeting.share': _contract(MeetingShareCommand, 'meeting_id', 'expected_version'),
    'meeting.revoke_share': _contract(MeetingShareCommand, 'meeting_id', 'member_id', 'expected_version'),
    'meeting.note.create': _contract(MeetingNoteCreateCommand, 'meeting_id'),
    'meeting.note.save': _contract(MeetingNoteSaveCommand, 'meeting_id', 'expected_version'),
    'meeting.note.finalize': _contract(MeetingNoteFinalizeCommand, 'meeting_id', 'expected_version'),
    'meeting.material.attach_link': _contract(MeetingMaterialLinkCommand, 'meeting_id', 'expected_version'),
    'meeting.material.detach': _contract(MeetingMaterialDetachCommand, 'meeting_id', 'binding_id', 'expected_version'),
    'meeting.summary.adopt': _contract(MeetingSummaryAdoptCommand, 'meeting_id', 'summary_id', 'expected_version'),
    'meeting.speaker.assign': _contract(MeetingSpeakerCommand, 'meeting_id', 'transcript_revision_id', 'speaker_label', 'scope', 'raw_start_source_key', 'raw_end_source_key'),
    'meeting.followup.task': _contract(MeetingFollowupCommand, 'meeting_id', 'summary_id', 'statement_index', 'kind', 'assignee_id'),
    'meeting.followup.request': _contract(MeetingFollowupCommand, 'meeting_id', 'summary_id', 'statement_index', 'kind'),
    'assistant.character.set': _contract(AssistantCharacterInput, 'expected_version'),
    'notification.mark_read': _contract(NotificationReadCommand, 'notification_id'),
    'work_request.comment.add': _contract(WorkRequestCommentCommand, 'request_id', 'idempotency_key'),
    'work_request.amend': _contract(WorkRequestAmendCommand, 'request_id', 'expected_version'),
    'conversation.create': _contract(ConversationCreateInput),
    'conversation.message.send': _contract(ConversationMessageCommand, 'conversation_id', 'context', 'idempotency_key', 'follow_up_candidate_id'),
    'conversation.turn.cancel': _contract(ConversationCancelCommand, 'conversation_id', 'expected_version'),
    'conversation.turn.retry': _contract(ConversationRetryCommand, 'conversation_id', 'turn_id'),
    'action.material.link.stage': _contract(ActionMaterialLinkCommand, 'action_item_id'),
    'action.material.draft.discard': _contract(ActionMaterialDiscardCommand, 'action_item_id', 'material_draft_id'),
})
