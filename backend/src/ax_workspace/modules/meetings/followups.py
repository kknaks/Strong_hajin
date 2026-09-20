"""Promote a meeting statement through the ordinary Work applications in one transaction."""
from ax_workspace.modules.meetings.results import MeetingFollowupResult
from uuid import UUID

from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.meetings.commands import MeetingFollowupCommand
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.application import TaskApplication
from ax_workspace.modules.work.creation import followup_idempotency_key
from ax_workspace.modules.work.creation_commands import TaskCreationApplication
from ax_workspace.modules.work.materials import TaskMaterialApplication
from ax_workspace.modules.work.requests import WorkRequestApplication


class MeetingFollowupApplication:
    """승격은 **같은 생성 명령·같은 권한·같은 멱등성**을 쓴다 — 우회 생성 경로를 두지 않는다 (WORK-001 Phase 5).

    두 층이 겹쳐 있다. 후보 잠금(`followup_candidate(..., lock=True)` → `already_promoted`)이 **키가 달라도
    같은 후보는 한 건**임을 보장하고, 생성 층의 멱등 키가 재시도를 한 건으로 묶는다. 서로 다른 층이다.
    """

    def __init__(
        self,
        meetings: MeetingApplication,
        tasks: TaskApplication,
        requests: WorkRequestApplication,
        materials: TaskMaterialApplication,
        creation: TaskCreationApplication,
    ) -> None:
        self._meetings, self._tasks, self._requests, self._materials = meetings, tasks, requests, materials
        self._creation = creation

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
        # 후보 identity 에서 만든 **안정 키** — 재시도해도 같은 값이다.
        stable_key = followup_idempotency_key(command.meeting_id, command.summary_id, command.statement_index)
        if command.kind == 'work_request':
            if not command.assignee_id:
                raise MeetingError('요청으로 만들려면 담당 후보가 필요합니다')
            # **승격도 발송 계약을 그대로 쓴다** — 우회 어댑터가 없다 (K-10). 그래서 v2 에서 승격 요청도
            # **수락 대기로 선다**: 회의에서 나왔다는 사실이 사람의 수락을 대신하지 않는다 (정책 L-15·L-16).
            # 자동 수락을 여기 만들지 않는다.
            #
            # **미답으로 남는 자리 하나**: 요청자가 `system:meeting` 인 승격 요청은 완료 승인을 부를 수
            # 있는 사람이 0명이다 — 확인자가 요청 행의 `requester_id` 에서 나오는데(대행 없음 · O-31)
            # 그 값이 시스템이고, 판단 명령은 그 값과 같은 사람에게만 열린다(O-32). SPEC-003 §7 OQ-206 이
            # 미정으로 두었으므로 **여기서 기본값을 정하지 않는다.** 그 Task 가 상위의 완결 판정 대상이
            # 되면 상위 완료가 막히는데, 그것은 **미답의 결과이지 제품 버그가 아니다.**
            # 요청자 전용 조작(수정·재상신·철회)은 이 미정에 들어가지 않는다 — 누른 사람이 그 자리에 선다.
            created = self._creation.create_work_request(
                principal, wanted, command.assignee_id, idempotency_key=stable_key
            )
            self._meetings.record_followup_promotion(principal, candidate, work_request_id=UUID(created['request_id']))
            return {'already_promoted': False, 'work_request': created, 'task': None}
        created = self._creation.create_task(principal, wanted, idempotency_key=stable_key)
        self._materials.attach_reference(principal, UUID(created['task_id']), kind='input', resource_type='meeting', resource_id=str(meeting_id))
        self._meetings.record_followup_promotion(principal, candidate, task_id=UUID(created['task_id']))
        return {'already_promoted': False, 'task': self._tasks.get(principal, UUID(created['task_id'])), 'work_request': None}
