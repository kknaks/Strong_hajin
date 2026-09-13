"""Checklist edits keep target identity/version separate from editable step values."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ChecklistVersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_task_version: int | None = Field(default=None, ge=1, title='업무 버전')


class ChecklistAddInput(ChecklistVersionInput):
    text: str = Field(min_length=1, max_length=300, title='단계 내용')


class ChecklistAddCommand(ChecklistAddInput):
    task_id: UUID = Field(title='대상 업무')


class ChecklistArchiveInput(ChecklistVersionInput):
    expected_version: int | None = Field(default=None, ge=1, title='단계 버전')


class ChecklistUpdateInput(ChecklistArchiveInput):
    text: str | None = Field(default=None, min_length=1, max_length=300, title='단계 내용')
    done: bool | None = Field(default=None, title='완료 여부')


class ChecklistUpdateCommand(ChecklistUpdateInput):
    task_id: UUID = Field(title='대상 업무')
    item_id: UUID = Field(title='대상 단계')
    expected_version: int = Field(ge=1, title='단계 버전')


class ChecklistArchiveCommand(ChecklistArchiveInput):
    task_id: UUID = Field(title='대상 업무')
    item_id: UUID = Field(title='대상 단계')
    expected_version: int = Field(ge=1, title='단계 버전')


class ChecklistOrderInput(ChecklistVersionInput):
    item_ids: list[UUID] = Field(min_length=1, title='단계 순서')


class ChecklistOrderCommand(ChecklistOrderInput):
    task_id: UUID = Field(title='대상 업무')
