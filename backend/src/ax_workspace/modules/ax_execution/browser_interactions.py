"""Durable browser requests; selecting bytes is a person's action, not an AX effect."""
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypedDict

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.material_results import TaskMaterialResult
from ax_workspace.modules.work.material_query_results import FolderMaterialView
from ax_workspace.modules.work.request_results import WorkRequestEvidenceResult
from ax_workspace.modules.actions.results import ActionDiscussionView
from ax_workspace.modules.meetings.results import MeetingMaterialView, MeetingRecordingView
from ax_workspace.modules.ax_execution.result_contracts import ActionMaterialDraftView

BrowserFileReceipt = TaskMaterialResult | FolderMaterialView | WorkRequestEvidenceResult | ActionDiscussionView | MeetingMaterialView | ActionMaterialDraftView


class CurrentMeetingMaterialView(TypedDict):
    material_id: str
    name: str
    content_type: str
    size: int
    uploaded_by: str
    uploaded_at: str
    can_detach: bool


BrowserFileReceipt = BrowserFileReceipt | CurrentMeetingMaterialView


class BrowserInteractionConflict(ValueError):
    pass


class BrowserFileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['task_material', 'meeting_material', 'meeting_material_replace', 'folder_material', 'request_evidence', 'request_comment_attachment', 'action_material']
    target_id: UUID
    request_key: str = Field(min_length=1, max_length=200)
    material_kind: Literal['input', 'output'] = 'input'
    binding_id: UUID | None = None
    comment_id: UUID | None = None

    @model_validator(mode='after')
    def validate_target_fields(self):
        if (self.binding_id is not None) != (self.intent == 'meeting_material_replace'):
            raise ValueError('binding_id is required only for replacing a meeting material')
        if (self.comment_id is not None) != (self.intent == 'request_comment_attachment'):
            raise ValueError('comment_id is required only for attaching to a request comment')
        if self.intent != 'task_material' and self.material_kind != 'input':
            raise ValueError('output materials are only supported for tasks')
        return self


class BrowserInterruptionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['cancelled', 'denied', 'failed', 'unsupported']


class BrowserRecordingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['meeting_recording'] = 'meeting_recording'
    target_id: UUID
    request_key: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)


class BrowserRecordingStartInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    capture_id: UUID


class BrowserTargetView(TypedDict):
    type: str
    id: str
    title: str
    version: int | None


class BrowserInteractionResult(TypedDict):
    interaction_id: str
    kind: str
    intent: str
    target: BrowserTargetView
    status: str
    result: BrowserFileReceipt | MeetingRecordingView | None
    open_url: str
    created_at: str
    updated_at: str


class BrowserInteractionRepository(Protocol):
    def find_request(self, owner_id: str, request_key: str) -> Any | None: ...
    def get(self, owner_id: str, interaction_id: UUID, *, lock: bool = False) -> Any | None: ...
    def add(self, **fields: Any) -> Any: ...


class BrowserFileTargets(Protocol):
    def recording_target(self, principal: Principal, request: BrowserRecordingRequest, *, read: bool = False) -> BrowserTargetView: ...
    def start_recording(self, principal: Principal, request: BrowserRecordingRequest) -> MeetingRecordingView: ...
    def stop_recording(self, principal: Principal, request: BrowserRecordingRequest, recording: MeetingRecordingView, *, name: str, content_type: str, data: bytes) -> MeetingRecordingView: ...
    def interrupt_recording(self, principal: Principal, request: BrowserRecordingRequest, recording: MeetingRecordingView) -> MeetingRecordingView: ...
    def authorize(self, principal: Principal, request: BrowserFileRequest) -> BrowserTargetView: ...
    def read(self, principal: Principal, request: BrowserFileRequest) -> BrowserTargetView: ...
    def upload(self, principal: Principal, request: BrowserFileRequest, *, name: str, content_type: str, data: bytes) -> BrowserFileReceipt: ...
    def reserve_action_file(self, principal: Principal, request: BrowserFileRequest, *, name: str, content_type: str, data: bytes) -> UUID: ...
    def store_action_file(self, principal: Principal, request: BrowserFileRequest, reservation_id: UUID, data: bytes) -> ActionMaterialDraftView: ...
    def abort_action_file(self, principal: Principal, request: BrowserFileRequest, reservation_id: UUID) -> None: ...


class BrowserInteractionApplication:
    def __init__(self, repository: BrowserInteractionRepository, targets: BrowserFileTargets, web_origin: str):
        self._repository, self._targets = repository, targets
        self._web_origin = web_origin

    def request_file(self, principal: Principal, request: BrowserFileRequest) -> BrowserInteractionResult:
        payload = request.model_dump(mode='json')
        existing = self._repository.find_request(str(principal.id), request.request_key)
        if existing is not None:
            if existing.kind != 'file' or BrowserFileRequest.model_validate(existing.payload).model_dump(mode='json') != payload:
                raise BrowserInteractionConflict('이 요청 키는 다른 대상에 이미 사용되었습니다')
            self._targets.read(principal, request)
            return self._view(existing)
        target = self._targets.authorize(principal, request)
        now = datetime.now(UTC)
        return self._view(self._repository.add(
            owner_id=str(principal.id), request_key=request.request_key, kind='file',
            payload=payload, target=target, status='waiting', result=None,
            created_at=now, updated_at=now,
        ))

    def get(self, principal: Principal, interaction_id: UUID) -> BrowserInteractionResult:
        row = self._owned(principal, interaction_id)
        if row.kind == 'recording':
            self._targets.recording_target(principal, BrowserRecordingRequest.model_validate(row.payload), read=True)
        else:
            self._targets.read(principal, BrowserFileRequest.model_validate(row.payload))
        return self._view(row)

    def request_recording(self, principal: Principal, request: BrowserRecordingRequest) -> BrowserInteractionResult:
        payload = request.model_dump(mode='json')
        existing = self._repository.find_request(str(principal.id), request.request_key)
        if existing is not None:
            if existing.kind != 'recording' or existing.payload != payload:
                raise BrowserInteractionConflict('이 요청 키는 다른 대상에 이미 사용되었습니다')
            return self.get(principal, existing.id)
        target = self._targets.recording_target(principal, request)
        now = datetime.now(UTC)
        return self._view(self._repository.add(owner_id=str(principal.id), request_key=request.request_key,
                          kind='recording', payload=payload, target=target, status='waiting', result=None,
                          created_at=now, updated_at=now))

    def start_recording(self, principal: Principal, interaction_id: UUID, capture_id: UUID) -> BrowserInteractionResult:
        row = self._owned(principal, interaction_id, lock=True)
        if row.kind != 'recording':
            raise BrowserInteractionConflict('녹음 요청이 아닙니다')
        request = BrowserRecordingRequest.model_validate(row.payload)
        if row.status in {'recording', 'upload_failed', 'completed'}:
            if row.capture_id != capture_id:
                raise BrowserInteractionConflict('이 요청의 녹음은 다른 창에서 시작했습니다')
            return self.get(principal, interaction_id)
        if row.status != 'waiting':
            raise BrowserInteractionConflict('녹음 시작을 기다리는 요청이 아닙니다')
        target = self._targets.recording_target(principal, request)
        if row.target['version'] != target['version']:
            raise BrowserInteractionConflict('회의가 변경되었습니다. 최신 내용에서 녹음 요청을 다시 열어 주세요')
        row.result = self._targets.start_recording(principal, request)
        row.capture_id = capture_id
        row.status, row.updated_at = 'recording', datetime.now(UTC)
        return self._view(row)

    def stop_recording(self, principal: Principal, interaction_id: UUID, capture_id: UUID, *, name: str, content_type: str, data: bytes) -> BrowserInteractionResult:
        row = self._owned(principal, interaction_id, lock=True)
        if row.kind != 'recording':
            raise BrowserInteractionConflict('녹음 요청이 아닙니다')
        if row.capture_id != capture_id:
            raise BrowserInteractionConflict('녹음을 시작한 창에서 종료해 주세요')
        request = BrowserRecordingRequest.model_validate(row.payload)
        fingerprint = self._fingerprint(name, content_type, data)
        if row.status == 'completed':
            self._targets.recording_target(principal, request, read=True)
            if row.file_fingerprint != fingerprint:
                raise BrowserInteractionConflict('완료된 녹음에 다른 파일을 보낼 수 없습니다')
            return self._view(row)
        if row.status not in {'recording', 'upload_failed'} or row.result is None:
            raise BrowserInteractionConflict('진행 중인 녹음이 아닙니다')
        row.result = self._targets.stop_recording(principal, request, row.result, name=name, content_type=content_type, data=data)
        row.file_fingerprint = fingerprint
        row.status, row.updated_at = 'completed', datetime.now(UTC)
        return self._view(row)

    def recover_recording_upload_failure(self, principal: Principal, interaction_id: UUID, capture_id: UUID, *, name: str, content_type: str, data: bytes) -> BrowserInteractionResult | None:
        row = self._owned(principal, interaction_id, lock=True)
        if row.kind != 'recording':
            raise BrowserInteractionConflict('녹음 요청이 아닙니다')
        if row.status == 'completed':
            return self.stop_recording(principal, interaction_id, capture_id, name=name, content_type=content_type, data=data)
        if row.status == 'recording':
            row.status, row.updated_at = 'upload_failed', datetime.now(UTC)
        return None

    def recover_recording_start_failure(self, principal: Principal, interaction_id: UUID, capture_id: UUID, *, denied: bool) -> BrowserInteractionResult | None:
        row = self._owned(principal, interaction_id, lock=True)
        if row.kind != 'recording':
            raise BrowserInteractionConflict('녹음 요청이 아닙니다')
        if row.status in {'recording', 'upload_failed', 'completed'}:
            return self.start_recording(principal, interaction_id, capture_id)
        if row.status == 'waiting':
            row.status, row.updated_at = ('denied' if denied else 'failed'), datetime.now(UTC)
        return None

    def upload(self, principal: Principal, interaction_id: UUID, *, name: str, content_type: str, data: bytes) -> BrowserInteractionResult:
        row = self._owned(principal, interaction_id, lock=True)
        if row.kind != 'file':
            raise BrowserInteractionConflict('파일 첨부 요청이 아닙니다')
        request = BrowserFileRequest.model_validate(row.payload)
        fingerprint = self._fingerprint(name, content_type, data)
        if row.status == 'completed':
            self._targets.read(principal, request)
            if row.file_fingerprint != fingerprint:
                raise BrowserInteractionConflict('완료된 요청에 다른 파일을 보낼 수 없습니다')
            return self._view(row)
        if row.status != 'waiting':
            raise BrowserInteractionConflict('파일 선택을 기다리는 요청이 아닙니다')
        target = self._targets.authorize(principal, request)
        if row.target['version'] != target['version']:
            raise BrowserInteractionConflict('대상이 변경되었습니다. 최신 내용에서 파일 요청을 다시 열어 주세요')
        row.result = self._targets.upload(principal, request, name=name, content_type=content_type, data=data)
        row.file_fingerprint = fingerprint
        row.status, row.updated_at = 'completed', datetime.now(UTC)
        return self._view(row)

    def prepare_action_upload(self, principal: Principal, interaction_id: UUID, *, name: str, content_type: str, data: bytes) -> UUID | None:
        row = self._owned(principal, interaction_id, lock=True)
        if row.kind != 'file':
            raise BrowserInteractionConflict('파일 첨부 요청이 아닙니다')
        request = BrowserFileRequest.model_validate(row.payload)
        if request.intent != 'action_material' or row.status == 'completed':
            return None
        fingerprint = self._fingerprint(name, content_type, data)
        if row.status == 'uploading':
            if row.file_fingerprint != fingerprint:
                raise BrowserInteractionConflict('진행 중인 요청에는 선택했던 같은 파일만 전송할 수 있습니다')
            return row.reservation_id
        if row.status != 'waiting':
            raise BrowserInteractionConflict('파일 선택을 기다리는 요청이 아닙니다')
        target = self._targets.authorize(principal, request)
        if target['version'] != row.target['version']:
            raise BrowserInteractionConflict('대상이 변경되었습니다. 파일 요청을 다시 열어 주세요')
        row.reservation_id = self._targets.reserve_action_file(principal, request, name=name, content_type=content_type, data=data)
        row.file_fingerprint = fingerprint
        row.status, row.updated_at = 'uploading', datetime.now(UTC)
        return row.reservation_id

    def upload_reserved_action_file(self, principal: Principal, interaction_id: UUID, *, name: str, content_type: str, data: bytes) -> BrowserInteractionResult:
        row = self._owned(principal, interaction_id, lock=True)
        if row.status == 'completed':
            return self.upload(principal, interaction_id, name=name, content_type=content_type, data=data)
        request = BrowserFileRequest.model_validate(row.payload)
        if row.status != 'uploading' or row.reservation_id is None or row.file_fingerprint != self._fingerprint(name, content_type, data):
            raise BrowserInteractionConflict('현재 파일 예약과 일치하지 않습니다')
        target = self._targets.authorize(principal, request)
        if target['version'] != row.target['version']:
            raise BrowserInteractionConflict('대상이 변경되었습니다. 파일 요청을 다시 열어 주세요')
        row.result = self._targets.store_action_file(principal, request, row.reservation_id, data)
        row.status, row.updated_at = 'completed', datetime.now(UTC)
        return self._view(row)

    def _owned(self, principal: Principal, interaction_id: UUID, *, lock: bool = False) -> Any:
        row = self._repository.get(str(principal.id), interaction_id, lock=lock)
        if row is None:
            raise ResourceNotFound('browser request was not found')
        return row

    def interrupt(self, principal: Principal, interaction_id: UUID, status: str) -> BrowserInteractionResult:
        event = BrowserInterruptionInput(status=status)
        row = self._owned(principal, interaction_id, lock=True)
        if row.status == event.status:
            return self._view(row)
        if row.kind == 'recording' and row.status in {'recording', 'upload_failed'}:
            row.result = self._targets.interrupt_recording(principal, BrowserRecordingRequest.model_validate(row.payload), row.result)
            row.status, row.updated_at = event.status, datetime.now(UTC)
            return self._view(row)
        if row.status not in {'waiting', 'uploading'}:
            raise BrowserInteractionConflict('이미 종료된 화면 요청입니다')
        self._abort_reservation(principal, row)
        row.status, row.updated_at = event.status, datetime.now(UTC)
        return self._view(row)

    def recover_upload_failure(self, principal: Principal, interaction_id: UUID, *, denied: bool,
                               name: str, content_type: str, data: bytes) -> BrowserInteractionResult | None:
        row = self._owned(principal, interaction_id, lock=True)
        if row.status == 'completed':
            return self.upload(principal, interaction_id, name=name, content_type=content_type, data=data)
        if row.status in {'waiting', 'uploading'}:
            self._abort_reservation(principal, row)
            row.status, row.updated_at = ('denied' if denied else 'failed'), datetime.now(UTC)
        return None

    def _abort_reservation(self, principal: Principal, row: Any) -> None:
        if row.reservation_id is not None:
            self._targets.abort_action_file(principal, BrowserFileRequest.model_validate(row.payload), row.reservation_id)

    @staticmethod
    def _fingerprint(name: str, content_type: str, data: bytes) -> str:
        return hashlib.sha256(json.dumps([name, content_type, hashlib.sha256(data).hexdigest()]).encode()).hexdigest()

    def _view(self, row: Any) -> BrowserInteractionResult:
        def iso(value: datetime) -> str:
            return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.astimezone(UTC).isoformat()
        return {'interaction_id': str(row.id), 'kind': row.kind, 'intent': row.payload['intent'],
                'target': dict(row.target), 'status': row.status, 'result': row.result,
                'open_url': self._web_origin + '/?interaction=' + str(row.id), 'created_at': iso(row.created_at), 'updated_at': iso(row.updated_at)}
