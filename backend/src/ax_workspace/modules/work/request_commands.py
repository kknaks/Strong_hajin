"""Request-owned inputs; content revisions remain separate from recipient decisions."""
from datetime import date
from typing import Literal, Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ax_workspace.modules.work.task_creation import WorkPayloadFields


class WorkRequestCommentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=4000, title='댓글 본문')


class WorkRequestCommentCommand(WorkRequestCommentInput):
    request_id: UUID = Field(title='대상 업무 요청')
    idempotency_key: str | None = Field(default=None, title='재전송 식별자')


class WorkRequestMaterialLinkInput(BaseModel):
    """요청에 링크 자료 하나를 건다 — 업무 자료 링크와 **같은 모양**이다 (`TaskMaterialLinkInput`).

    `kind` 는 받되 **참고 자료뿐**이다: 요청에는 아직 결과가 없어 산출물이 설 자리가 없고, 수락이
    이 자료를 업무에 그대로 이어 붙이므로 여기서 산출물을 허용하면 아무도 만들지 않은 산출물이
    업무의 완료 보고 후보로 선다.
    """

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['input'] = Field(default='input', title='자료 역할')
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


class WorkRequestMaterialLinkCommand(WorkRequestMaterialLinkInput):
    request_id: UUID = Field(title='대상 업무 요청')


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


class WorkRequestCreateInput(WorkPayloadFields):
    """요청 한 건의 내용 — **내 업무와 같은 공통 payload** 위에 요청 전용 둘이 얹힌다.

    공통 아홉(`WorkPayloadFields`)은 여기서 다시 적지 않는다. 요청만 갖는 것은 둘뿐이다:
    받는 사람(`assignee_id`)과 이전 요청(`supersedes_request_id`).
    """

    assignee_id: str = Field(min_length=1, max_length=100, title='요청 대상')
    #: 재요청이면 이전 요청. 새 요청·새 Task 이고 옛것을 되살리지 않는다 (정책 V-12).
    supersedes_request_id: UUID | None = Field(default=None, title='이전 요청')

    @field_validator('supersedes_request_id', mode='before')
    @classmethod
    def empty_previous_request(cls, value: object) -> object:
        return None if value == '' else value

    @model_validator(mode='after')
    def normalize_values(self) -> Self:
        self.cc_member_ids = [member for member in self.cc_member_ids if member != self.assignee_id]
        return self

    def for_requester(self, requester_id: str | None) -> Self:
        return self.model_copy(update={'cc_member_ids': [member for member in self.cc_member_ids if member != requester_id]})


class TaskVersionInput(BaseModel):
    """회차만 받는 명령 — **상태를 바꾸는 모든 명령에 회차가 필수다** (K-4)."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title='업무 버전')


class TaskReopenInput(BaseModel):
    """재개 — 회차는 필수, 사유는 선택이다 (SPEC-003 §4 `reopen`)."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title='업무 버전')
    reason: str | None = Field(default=None, max_length=1000, title='재개 사유')


class TaskProposalInput(BaseModel):
    """취소·조건 변경 제안. **제안만으로는 아무것도 바뀌지 않는다.**"""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: str = Field(title='제안 종류')
    expected_version: int = Field(ge=1, title='업무 버전')
    reason: str | None = Field(default=None, max_length=1000, title='사유')
    payload: dict[str, JsonValue] | None = Field(default=None, title='바꿀 내용')

    @field_validator('kind')
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in {'cancellation', 'terms_change'}:
            raise ValueError('제안 종류는 cancellation 또는 terms_change 입니다')
        return value


class TaskProposalResponseInput(BaseModel):
    """동의 / 동의하지 않음 — **담당자만** 부른다."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title='업무 버전')
    agree: bool = Field(title='동의 여부')
    reason: str | None = Field(default=None, max_length=1000, title='사유')


class TaskReopenCommand(TaskReopenInput):
    task_id: UUID = Field(title='대상 업무')


class TaskProposalCommand(TaskProposalInput):
    task_id: UUID = Field(title='대상 업무')


class TaskProposalResponseCommand(TaskProposalResponseInput):
    task_id: UUID = Field(title='대상 업무')
    proposal_id: UUID = Field(title='대상 제안')


class TaskProposalWithdrawCommand(TaskVersionInput):
    task_id: UUID = Field(title='대상 업무')
    proposal_id: UUID = Field(title='대상 제안')


class WorkRequestWithdrawCommand(WorkRequestVersionInput):
    request_id: UUID = Field(title='대상 업무 요청')


class WorkRequestListEntryCommand(BaseModel):
    """요청자 목록에서 빼기 — 상태를 바꾸지 않으므로 회차를 받지 않는다. 두 번 눌러도 한 건이다."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: UUID = Field(title='대상 업무 요청')
