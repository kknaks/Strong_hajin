"""Bind browser file intents to their existing owning commands in one session."""
from datetime import UTC, datetime
import hashlib
from uuid import UUID
from ax_workspace.modules.ax_execution.browser_interactions import BrowserFileReceipt, BrowserFileRequest, BrowserRecordingRequest, BrowserTargetView
from ax_workspace.modules.meetings.results import MeetingRecordingView
from ax_workspace.modules.meetings.materials import MaterialUpload
from ax_workspace.modules.ax_execution.result_contracts import ActionMaterialDraftView
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, MEETING_RECORD
from ax_workspace.modules.work.action_materials import ActionMaterialNotFound
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.meetings.domain import MeetingAccessDenied, MeetingError


class SessionBrowserFileTargets:
    def __init__(self, application, session):
        self._application, self._session = application, session

    def recording_target(self, principal, request: BrowserRecordingRequest, *, read=False) -> BrowserTargetView:
        if MEETING_RECORD not in principal.capabilities:
            raise MeetingAccessDenied('meeting.record capability is required')
        detail = self._application._meetings(self._session).get(principal, request.target_id)
        meeting = detail['meeting']
        return {
            'type': 'meeting',
            'id': str(request.target_id),
            'title': meeting['title'],
            'version': None,
        }

    def start_recording(self, principal, request: BrowserRecordingRequest) -> MeetingRecordingView:
        detail = self._application._meetings(self._session).start(principal, request.target_id)
        meeting = detail['meeting']
        return {
            'recording_id': str(request.target_id),
            'meeting_id': str(request.target_id),
            'purpose': request.purpose,
            'state': 'recording',
            'version': 1,
            'content_type': None,
            'original_name': None,
            'size_bytes': None,
            'sha256': None,
            'started_at': meeting['started_at'],
            'ended_at': None,
            'storage_key': None,
        }

    def stop_recording(self, principal, request: BrowserRecordingRequest, recording: MeetingRecordingView, *, name, content_type, data) -> MeetingRecordingView:
        if recording['recording_id'] != str(request.target_id):
            raise MeetingError('회의 녹음 대상이 일치하지 않습니다')
        extension = self._application._recording_storage.extension_for(
            content_type.split('/', 1)[-1].split(';', 1)[0]
        )
        try:
            storage_key = self._application._recording_storage.replace(
                str(request.target_id), data, extension=extension
            )
        except OSError as error:
            raise MeetingError('녹음 파일을 저장하지 못했습니다. 같은 원본으로 다시 시도해 주세요') from error
        meetings = self._application._meetings(self._session)
        meetings.note_recording_file(request.target_id, storage_key, content_type=content_type)
        detail = meetings.end(principal, request.target_id)
        self._application._enqueue_finalize(self._session, request.target_id)
        return {
            **recording,
            'state': 'uploaded',
            'version': recording['version'] + 1,
            'content_type': content_type,
            'original_name': name,
            'size_bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'ended_at': detail['meeting']['last_saved_at'] or datetime.now(UTC).isoformat(),
        }

    def interrupt_recording(self, principal, request: BrowserRecordingRequest, recording: MeetingRecordingView) -> MeetingRecordingView:
        meetings = self._application._meetings(self._session)
        detail = meetings.get(principal, request.target_id)
        if detail['meeting']['status'] == 'in_progress':
            detail = meetings.end(principal, request.target_id)
            self._application._enqueue_finalize(self._session, request.target_id)
        return {
            **recording,
            'state': 'failed',
            'version': recording['version'] + 1,
            'ended_at': detail['meeting']['last_saved_at'] or datetime.now(UTC).isoformat(),
        }

    def authorize(self, principal, request: BrowserFileRequest) -> BrowserTargetView:
        if request.intent == 'task_material':
            title, version = self._application._materials(self._session).upload_target(principal, request.target_id)
            kind = 'task'
        elif request.intent in {'meeting_material', 'meeting_material_replace'}:
            detail = self._application._meetings(self._session).get(principal, request.target_id)
            title, version, kind = detail['meeting']['title'], None, 'meeting'
        elif request.intent == 'folder_material':
            title = self._application._material_folders(self._session).upload_target(principal, request.target_id)
            kind, version = 'material_folder', None
        elif request.intent == 'action_material':
            title, version = self._application._action_materials(self._session).upload_target(principal, request.target_id)
            kind = 'action'
        else:
            title, version = self._application._work_requests(self._session).upload_target(principal, request.target_id, request.comment_id)
            kind = 'work_request'
        return {'type': kind, 'id': str(request.target_id), 'title': title, 'version': version}

    def read(self, principal, request: BrowserFileRequest) -> BrowserTargetView:
        if request.intent == 'task_material':
            result = self._application._tasks(self._session).get(principal, request.target_id)
            kind = 'task'
        elif request.intent in {'meeting_material', 'meeting_material_replace'}:
            result = self._application._meetings(self._session).get(principal, request.target_id)['meeting']
            return {'type': 'meeting', 'id': str(request.target_id), 'title': result['title'], 'version': None}
        elif request.intent == 'folder_material':
            return self.authorize(principal, request)
        elif request.intent == 'action_material':
            title, version = self._application._action_materials(self._session).upload_target(principal, request.target_id, pending=False)
            return {'type': 'action', 'id': str(request.target_id), 'title': title, 'version': version}
        else:
            result = self._application._work_requests(self._session).get(principal, request.target_id)
            kind = 'work_request'
        return {'type': kind, 'id': str(request.target_id), 'title': result['title'], 'version': result['version']}

    def upload(self, principal, request: BrowserFileRequest, *, name, content_type, data) -> BrowserFileReceipt:
        fields = {'name': name, 'content_type': content_type, 'data': data}
        if request.intent == 'task_material':
            return self._application._materials(self._session).attach(principal, request.target_id, kind=request.material_kind, **fields)
        if request.intent in {'meeting_material', 'meeting_material_replace'}:
            if request.intent == 'meeting_material_replace':
                raise MeetingError('새 회의 화면에서는 기존 자료를 교체하지 않고 새 자료로 연결합니다')
            attached, failed = self._application._meeting_materials(self._session).attach(
                principal,
                request.target_id,
                [MaterialUpload(**fields)],
            )
            if failed or not attached:
                raise MeetingError('회의 자료를 연결하지 못했습니다')
            return attached[0]
        if request.intent == 'folder_material':
            return self._application._material_folders(self._session).upload(principal, request.target_id, **fields)
        if request.intent == 'action_material':
            raise ValueError('action files must use the durable reservation')
        requests = self._application._work_requests(self._session)
        if request.intent == 'request_comment_attachment':
            return requests.attach_to_comment(principal, request.target_id, request.comment_id, **fields)
        return requests.add_evidence(principal, request.target_id, **fields)

    def reserve_action_file(self, principal, request: BrowserFileRequest, *, name, content_type, data) -> UUID:
        reserved = self._application._action_materials(self._session).reserve_file(
            principal, request.target_id, name=name, content_type=content_type, data=data)
        return UUID(reserved['material_draft_id'])

    def store_action_file(self, principal, request: BrowserFileRequest, reservation_id: UUID, data: bytes) -> ActionMaterialDraftView:
        current = SqlAlchemyOrganizationRepository(self._session).principal_for(str(principal.id))
        if current is None or ACTION_DECIDE not in (current.capabilities & principal.capabilities):
            raise ActionMaterialNotFound('action was not found')
        return self._application._action_materials(self._session).store_reserved_file(principal, request.target_id, reservation_id, data)

    def abort_action_file(self, principal, request: BrowserFileRequest, reservation_id: UUID) -> None:
        self._application._action_materials(self._session).abort_reserved_file(principal, request.target_id, reservation_id)
