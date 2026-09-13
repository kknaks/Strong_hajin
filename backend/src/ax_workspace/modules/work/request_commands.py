"""Request-owned inputs; content revisions remain separate from recipient decisions."""
from datetime import date
from typing import Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ax_workspace.modules.work.errors import TaskError
from ax_workspace.modules.work.task_values import clean_checklist


class WorkRequestCommentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=4000, title='댓글 본문')


class WorkRequestCommentCommand(WorkRequestCommentInput):
    request_id: UUID = Field(title='대상 업무 요청')
    idempotency_key: str | None = Field(default=None, title='재전송 식별자')


class WorkRequestRevisionInput(BaseModel):
    """Null preserves content; an empty description or clear flag explicitly removes it."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title='요청 버전')
    title: str | None = Field(default=None, min_length=1, max_length=300, title='요청 제목')
    description: str | None = Field(default=None, title='설명')
    due_date: date | None = Field(default=None, title='기한')
    clear_due_date: bool = Field(default=False, title='기한 없애기')

    @field_validator('due_date', mode='before')
    @classmethod
    def empty_date(cls, value: object) -> object:
        return None if value == '' else value


class WorkRequestAmendCommand(WorkRequestRevisionInput):
    request_id: UUID = Field(title='대상 업무 요청')


class WorkRequestVersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title='요청 버전')


class WorkRequestDecisionInput(WorkRequestVersionInput):
    # The existing HTTP accept/reject envelope carries an optional reason;
    # rejection's owning command below requires it.
    reason: str | None = None


class WorkRequestRejectInput(WorkRequestVersionInput):
    reason: str = Field(min_length=1, title='거절 사유')


class WorkRequestNegotiationInput(WorkRequestVersionInput):
    conditions: dict[str, JsonValue] = Field(min_length=1, title='조정 조건')


class WorkRequestAcceptCommand(WorkRequestVersionInput):
    request_id: UUID = Field(title='대상 업무 요청')


class WorkRequestRejectCommand(WorkRequestRejectInput):
    request_id: UUID = Field(title='대상 업무 요청')


class WorkRequestNegotiationCommand(WorkRequestNegotiationInput):
    request_id: UUID = Field(title='대상 업무 요청')


class WorkRequestCreateInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300, title='업무 명')
    assignee_id: str = Field(min_length=1, max_length=100, title='요청 대상')
    description: str | None = Field(default=None, title='내용')
    due_date: date | None = Field(default=None, title='기한')
    cc_member_ids: list[str] = Field(default_factory=list, title='참조 구성원')
    checklist: list[str] = Field(default_factory=list, title='체크리스트')
    reference_task_ids: list[UUID] = Field(default_factory=list, title='참고 업무')

    @field_validator('description', 'due_date', mode='before')
    @classmethod
    def empty_optional(cls, value: object) -> object:
        return None if value == '' else value

    @field_validator('cc_member_ids', 'checklist', 'reference_task_ids', mode='before')
    @classmethod
    def absent_list(cls, value: object) -> object:
        return [] if value is None else value

    @model_validator(mode='after')
    def normalize_values(self) -> Self:
        self.description = (self.description or '').strip() or None
        self.cc_member_ids = list(dict.fromkeys(member for item in self.cc_member_ids if (member := item.strip()) and member != self.assignee_id))
        self.reference_task_ids = list(dict.fromkeys(self.reference_task_ids))
        try:
            self.checklist = clean_checklist(self.checklist)
        except TaskError as error:
            raise ValueError(str(error)) from error
        return self

    def for_requester(self, requester_id: str | None) -> Self:
        return self.model_copy(update={'cc_member_ids': [member for member in self.cc_member_ids if member != requester_id]})
