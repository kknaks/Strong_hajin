"""Task-owned command values shared across direct and confirmed adapters."""
from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskCompletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title="업무 버전")
    summary: str = Field(min_length=1, max_length=2000, title="결과 요약")
    output_material_ids: list[UUID] = Field(default_factory=list, title="결과 자료")


class TaskCompletionCommand(TaskCompletionInput):
    task_id: UUID = Field(title="대상 업무")


class TaskReferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    referenced_task_id: UUID = Field(title="참고할 업무")


class TaskReferenceCommand(TaskReferenceInput):
    task_id: UUID = Field(title="대상 업무")


class TaskReferenceReleaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID = Field(title="대상 업무")
    reference_id: UUID = Field(title="해제할 참고 연결")


class TaskReassignInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title="업무 버전")
    assignee_id: str = Field(min_length=1, max_length=100, title="새 담당자")
    reason: str | None = Field(default=None, max_length=4000, title="변경 사유")


class TaskReassignCommand(TaskReassignInput):
    task_id: UUID = Field(title="대상 업무")


class TaskEditFields(BaseModel):
    """Canonical edits: absence preserves a field; null clears nullable fields."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, title='업무 명')
    description: str | None = Field(default=None, title='내용')
    start_date: date | None = Field(default=None, title='시작일')
    due_date: date | None = Field(default=None, title='기한')
    project_id: UUID | None = Field(default=None, title='프로젝트')

    @model_validator(mode='after')
    def validate_edits(self) -> Self:
        if 'title' in self.model_fields_set and self.title is None:
            raise ValueError('title cannot be cleared')
        if not (self.model_fields_set - {'task_id', 'expected_version'}):
            raise ValueError('at least one field is required')
        return self

    def changes(self) -> dict:
        return self.model_dump(exclude_unset=True, exclude={'task_id', 'expected_version'})


class TaskUpdateCommand(TaskEditFields):
    task_id: UUID = Field(title='대상 업무')
    expected_version: int = Field(ge=1, title='업무 버전')

    @model_validator(mode='before')
    @classmethod
    def read_legacy_pending(cls, value: object) -> object:
        if isinstance(value, dict) and 'changes' in value:
            if set(value) != {'task_id', 'expected_version', 'changes'} or not isinstance(value['changes'], dict):
                raise ValueError('invalid legacy task edit')
            if set(value['changes']) - set(TaskEditFields.model_fields):
                raise ValueError('unsupported task fields')
            return {'task_id': value['task_id'], 'expected_version': value['expected_version'], **value['changes']}
        return value


class TaskUpdateInput(BaseModel):
    """Existing screen/tool values normalize into one canonical edit command."""
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, title='업무 버전')
    title: str | None = Field(default=None, title='업무 명')
    description: str | None = Field(default=None, title='내용')
    start_date: date | None = Field(default=None, title='시작일')
    due_date: date | None = Field(default=None, title='기한')
    clear_start_date: bool = Field(default=False, title='시작일 삭제')
    clear_due_date: bool = Field(default=False, title='기한 삭제')
    project_id: UUID | None = Field(default=None, title='프로젝트')
    clear_project: bool = Field(default=False, title='프로젝트 연결 해제')

    def changes(self) -> dict:
        values = {key: getattr(self, key) for key in ('title', 'description') if getattr(self, key) is not None}
        for key, clear in [('start_date', self.clear_start_date), ('due_date', self.clear_due_date), ('project_id', self.clear_project)]:
            if clear or getattr(self, key) is not None:
                values[key] = None if clear else getattr(self, key)
        return TaskEditFields.model_validate(values).changes()


class TaskVersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title='업무 버전')


class TaskBlockInput(TaskVersionInput):
    reason: str = Field(min_length=1, title='차단 사유')


#: 사유가 **필수인 전이** — 왜 막혔는지, 왜 접었는지는 그 일을 잇는 사람이 읽어야 하는 사실이다
#: (SPEC-003 §4 Validation · SPEC-001 계승). 시작·완료는 사유를 묻지 않는다.
_REASON_REQUIRED_TARGETS = frozenset({'blocked', 'cancelled'})


class TaskTransitionInput(TaskVersionInput):
    target: Literal['in_progress', 'blocked', 'done', 'cancelled'] = Field(title='변경 상태')
    reason: str | None = Field(default=None, title='사유 (차단·취소에 필수)')

    @model_validator(mode='after')
    def validate_reason(self) -> Self:
        """차단과 **취소**에 사유가 필요하다. 나머지 전이에서는 실어 보내도 버린다.

        취소가 오래 빠져 있었다 — 사유 없이 사라진 업무는 남은 사람에게 「왜 없어졌는지」가 아무 데도
        없는 일이 된다. 공백만 있는 문자열도 사유가 아니므로 같이 거절한다.
        """
        cleaned = ' '.join(str(self.reason or '').split()) or None
        if self.target in _REASON_REQUIRED_TARGETS and not cleaned:
            raise ValueError(
                'block reason is required' if self.target == 'blocked' else '취소에는 사유가 필요합니다'
            )
        self.reason = cleaned if self.target in _REASON_REQUIRED_TARGETS else None
        return self


class TaskTransitionCommand(TaskTransitionInput):
    task_id: UUID = Field(title='대상 업무')


class TaskCancelInput(TaskVersionInput):
    """직접 취소 — **사유 필수** (SPEC-003 §4 API · Validation).

    수락된 요청 Task 에서는 이 명령 자체가 거부된다(`WORK_CANCEL_REQUIRES_AGREEMENT`) — 그 자리의
    취소는 제안–동의로만 간다. 여기 실린 사유는 진행 기록에 그대로 남는다.
    """

    reason: str = Field(min_length=1, max_length=4000, title='취소 사유')

    @model_validator(mode='after')
    def require_text(self) -> Self:
        cleaned = ' '.join(str(self.reason or '').split())
        if not cleaned:
            raise ValueError('취소에는 사유가 필요합니다')
        self.reason = cleaned
        return self
