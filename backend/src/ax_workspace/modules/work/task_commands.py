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


class TaskTransitionInput(TaskVersionInput):
    target: Literal['in_progress', 'blocked', 'done', 'cancelled'] = Field(title='변경 상태')
    reason: str | None = Field(default=None, title='차단 사유')

    @model_validator(mode='after')
    def validate_reason(self) -> Self:
        if self.target == 'blocked' and not self.reason:
            raise ValueError('block reason is required')
        if self.target != 'blocked':
            self.reason = None
        return self


class TaskTransitionCommand(TaskTransitionInput):
    task_id: UUID = Field(title='대상 업무')
