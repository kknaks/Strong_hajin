"""Promote a meeting statement through the ordinary Work applications in one transaction."""
from ax_workspace.modules.meetings.results import MeetingFollowupResult
from uuid import UUID

from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.meetings.commands import MeetingFollowupCommand
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.application import TaskApplication
from ax_workspace.modules.work.materials import TaskMaterialApplication
from ax_workspace.modules.work.requests import WorkRequestApplication


class MeetingFollowupApplication:
    def __init__(self, meetings: MeetingApplication, tasks: TaskApplication, requests: WorkRequestApplication, materials: TaskMaterialApplication) -> None:
        self._meetings, self._tasks, self._requests, self._materials = meetings, tasks, requests, materials

    def promote(self, principal: Principal, meeting_id: UUID, summary_id: UUID, statement_index: int, *, kind: str, title: str | None = None, assignee_id: str | None = None) -> MeetingFollowupResult:
        command = MeetingFollowupCommand(meeting_id=meeting_id, summary_id=summary_id, statement_index=statement_index, kind=kind, title=title, assignee_id=assignee_id)
        candidate = self._meetings.followup_candidate(principal, meeting_id, summary_id, statement_index, lock=True)
        existing = candidate['promotion']
        if existing is not None:
            return {
                'already_promoted': True,
                'task': self._tasks.get(principal, existing.task_id) if existing.task_id else None,
                'work_request': self._requests.get(principal, existing.work_request_id) if existing.work_request_id else None,
            }
        wanted = ' '.join(str(command.title or candidate['statement'].statement_text).split())[:300]
        if command.kind == 'work_request':
            if not command.assignee_id:
                raise MeetingError('요청으로 만들려면 담당 후보가 필요합니다')
            created = self._requests.create(principal, wanted, command.assignee_id)
            self._meetings.record_followup_promotion(principal, candidate, work_request_id=UUID(created['request_id']))
            return {'already_promoted': False, 'work_request': created, 'task': None}
        created = self._tasks.create_self(principal, wanted)
        self._materials.attach_reference(principal, UUID(created['task_id']), kind='input', resource_type='meeting', resource_id=str(meeting_id))
        self._meetings.record_followup_promotion(principal, candidate, task_id=UUID(created['task_id']))
        return {'already_promoted': False, 'task': self._tasks.get(principal, UUID(created['task_id'])), 'work_request': None}
