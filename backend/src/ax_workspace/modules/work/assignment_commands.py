"""A direct assignment's recipient decides separately from its creator's approval."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class AssignmentDeclineInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, title='거절 사유')


class AssignmentAcceptCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assignment_id: UUID = Field(title='대상 배정')
    # Previously delivered direct calls omit this; canonical judgements bind it.
    expected_task_version: int | None = Field(default=None, ge=1, title='업무 버전')


class AssignmentDeclineCommand(AssignmentAcceptCommand, AssignmentDeclineInput):
    pass
