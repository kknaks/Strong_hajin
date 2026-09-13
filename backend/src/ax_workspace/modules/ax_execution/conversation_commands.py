"""Conversation command inputs shared by browser, MCP and confirmed execution."""
from __future__ import annotations
from typing import Literal, TYPE_CHECKING
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


if TYPE_CHECKING:
    from ax_workspace.modules.ax_execution.conversations import ConversationContextReferenceInput


class ConversationCreateInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(default='새 대화', title='대화 제목')


class ConversationContextInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_type: Literal['task', 'work_request'] = Field(title='맥락 종류')
    resource_id: UUID = Field(title='맥락 대상')
    resource_version: int = Field(ge=1, title='맥락 버전')
    included: bool = Field(title='내용 포함')


class ConversationMessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    body: str = Field(min_length=1, title='메시지 본문')
    context: list[ConversationContextInput] = Field(default_factory=list, title='선택한 업무 맥락')
    follow_up_candidate_id: UUID | None = Field(default=None, title='선택한 후속 제안')

    def references(self) -> list[ConversationContextReferenceInput]:
        from ax_workspace.modules.ax_execution.conversations import ConversationContextReferenceInput
        return [ConversationContextReferenceInput(resource_type=item.resource_type, resource_id=str(item.resource_id), resource_version=item.resource_version, included=item.included) for item in self.context]


class ConversationMessageCommand(ConversationMessageInput):
    conversation_id: UUID = Field(title='대상 대화')
    idempotency_key: str | None = Field(default=None, title='재전송 식별자')


class ConversationCancelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, title='대화 버전')


class ConversationCancelCommand(ConversationCancelInput):
    conversation_id: UUID = Field(title='대상 대화')


class ConversationRetryCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: UUID = Field(title='대상 대화')
    turn_id: UUID = Field(title='다시 시도할 실패 또는 취소된 응답')


class ConversationMessageResult(TypedDict):
    conversation_id: str
    message_id: str
    turn_id: str | None
    queued: bool
    queue_size: int


class ConversationRetryResult(TypedDict):
    conversation_id: str
    turn_id: str
    retry_of_turn_id: str
