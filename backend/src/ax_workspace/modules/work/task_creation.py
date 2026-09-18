"""Canonical values for creating work; source lineage stays with the caller transaction."""
from datetime import date
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ax_workspace.modules.work.errors import TaskError
from ax_workspace.modules.work.task_values import clean_checklist, validate_schedule


class TaskCreationFields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300, title='업무 명')
    description: str | None = Field(default=None, title='내용')
    start_date: date | None = Field(default=None, title='시작일')
    due_date: date | None = Field(default=None, title='기한')
    checklist: list[str] = Field(default_factory=list, title='체크리스트')
    reference_task_ids: list[UUID] = Field(default_factory=list, title='참고 업무')
    parent_task_id: UUID | None = Field(default=None, title='상위 업무')

    @field_validator('description', 'start_date', 'due_date', 'parent_task_id', mode='before')
    @classmethod
    def empty_optional_value(cls, value: object) -> object:
        return None if value == '' else value

    @field_validator('checklist', 'reference_task_ids', mode='before')
    @classmethod
    def absent_list(cls, value: object) -> object:
        return [] if value is None else value

    @model_validator(mode='after')
    def normalize_creation(self) -> Self:
        try:
            validate_schedule(self.start_date, self.due_date)
            self.checklist = clean_checklist(self.checklist)
        except TaskError as error:
            raise ValueError(str(error)) from error
        self.description = (self.description or '').strip() or None
        self.reference_task_ids = list(dict.fromkeys(self.reference_task_ids))
        return self


class TaskCreateInput(TaskCreationFields):
    project_id: UUID | None = Field(default=None, title='프로젝트')
    #: 담당. 비었거나 본인이면 본인 업무, 다른 사람이면 **수락 없이** 그 사람의 업무가 된다 (WORK-001 Phase 1).
    #: 멱등 키는 여기 없다 — `Idempotency-Key` 헤더이고 MCP 는 도구의 명시적 인자로 받는다.
    #: 승인자도 여기 없다 — W1 은 열만 만들고 값을 받지 않는다.
    assignee_id: str | None = Field(default=None, max_length=100, title='담당')

    @field_validator('project_id', 'assignee_id', mode='before')
    @classmethod
    def empty_project(cls, value: object) -> object:
        return None if value == '' else value


class TaskAssignmentInput(TaskCreationFields):
    assignee_id: str = Field(min_length=1, max_length=100, title='담당자')

    @model_validator(mode='before')
    @classmethod
    def read_legacy_empty_project(cls, value: object) -> object:
        # Older shared Task draft normalization appended this unused null field.
        # A non-null project is not owned by assignment creation and must not be
        # silently ignored by the executor.
        if isinstance(value, dict) and value.get('project_id', object()) is None:
            return {key: item for key, item in value.items() if key != 'project_id'}
        return value
