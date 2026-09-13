"""Inputs for task material relationships; file bytes use the browser bridge."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskMaterialLinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['input', 'output'] = Field(title='자료 역할')
    url: str = Field(min_length=1, max_length=500, title='링크 주소')
    label: str = Field(min_length=1, max_length=300, title='자료 이름')

    @field_validator('url')
    @classmethod
    def openable_url(cls, value: str) -> str:
        from ax_workspace.modules.work.material_values import MaterialError, normalize_material_link
        try:
            return normalize_material_link(value)
        except MaterialError as error:
            raise ValueError(str(error)) from error


class TaskMaterialLinkCommand(TaskMaterialLinkInput):
    task_id: UUID = Field(title='대상 업무')


class TaskMaterialReferenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['input', 'output'] = Field(title='자료 역할')
    resource_type: Literal['task', 'meeting'] = Field(title='참조 종류', description='회의는 자료로 연결합니다. 업무는 별도의 참고 업무 연결을 사용하세요.')
    resource_id: UUID = Field(title='참조 대상')


class TaskMaterialReferenceCommand(TaskMaterialReferenceInput):
    task_id: UUID = Field(title='대상 업무')


class TaskMaterialDetachCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: UUID = Field(title='대상 업무')
    binding_id: UUID = Field(title='분리할 자료 연결')


class ActionMaterialLinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=500, title='링크 주소')
    label: str = Field(min_length=1, max_length=300, title='자료 이름')

    @field_validator('url')
    @classmethod
    def openable_url(cls, value: str) -> str:
        return TaskMaterialLinkInput.openable_url(value)


class ActionMaterialLinkCommand(ActionMaterialLinkInput):
    action_item_id: UUID = Field(title='자료를 준비할 승인 항목')


class ActionMaterialDiscardCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action_item_id: UUID = Field(title='자료를 준비한 승인 항목')
    material_draft_id: UUID = Field(title='버릴 자료 초안')
