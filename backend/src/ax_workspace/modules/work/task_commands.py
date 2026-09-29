"""Task-owned command values shared across direct and confirmed adapters."""
from datetime import date, time
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    #: 상위 업무 **0..1** — 만든 뒤에도 옮길 수 있다 (SPEC-007 §4 · D-17). 배열도 집합도 아니라서
    #: 한 건씩 붙였다 떼는 전용 명령을 두지 않는다. **하위 해제·추가도 이 칸을 쓴다** — 대상이
    #: 「그 하위 업무」이고, 「해제」는 그 업무의 `clear_parent`, 「추가」는 그 업무의 `parent_task_id` 다.
    parent_task_id: UUID | None = Field(default=None, title='상위 업무')
    #: **상위를 비운다.** 「생략」과 「비우기」를 한 필드로 가를 수 없어 플래그를 따로 둔다 —
    #: `project_id` + `clear_project` 가 같은 모양을 이미 쓴다. `parent_task_id` 와 **함께 보내면
    #: 거절**이다 (아래 `validate_edits`).
    #:
    #: **이 칸이 `TaskUpdateInput` 이 아니라 여기 있는 이유**: 확인 카드(AX)와 MCP 는
    #: `TaskUpdateCommand` 로 payload 를 **직접** 검증해 `TaskUpdateInput` 을 지나지 않는다.
    #: 「둘을 함께 보내면 거절」을 HTTP 표면에만 두면 그 갈래에서 규칙이 빠진다.
    clear_parent: bool = Field(default=False, title='상위 연결 해제')
    #: 선행업무 — **배열 전체 교체**다 (SPEC-001 §4). 한 건씩 붙였다 떼는 전용 명령을 두지 않는다:
    #: 선행은 화면이 프로젝트 안에서 한 번에 여러 개를 고르는 **집합**이다.
    #: **생략하면 건드리지 않고**(`exclude_unset`), 빈 배열은 「전부 뗀다」다.
    preceding_task_ids: list[UUID] = Field(default_factory=list, title='선행업무')
    #: 승인자(화면 라벨 「결재자」) — 없음은 보존, `null` 은 비우기다. `승인 대기` 뒤에는 바꿀 수 없다.
    approver_id: str | None = Field(default=None, max_length=100, title='승인자')

    @field_validator('preceding_task_ids', mode='before')
    @classmethod
    def absent_predecessors(cls, value: object) -> object:
        # `null` 과 빈 배열을 같은 뜻으로 읽는다 — 둘 다 「전부 뗀다」다. 생략은 그것과 다른 뜻이고
        # `model_fields_set` 이 그 차이를 갖는다.
        return [] if value is None else value

    @field_validator('approver_id', mode='before')
    @classmethod
    def empty_approver(cls, value: object) -> object:
        return None if value == '' else value

    @model_validator(mode='after')
    def validate_edits(self) -> Self:
        if 'title' in self.model_fields_set and self.title is None:
            raise ValueError('title cannot be cleared')
        if self.clear_parent and self.parent_task_id is not None:
            # 「이 업무 아래로 옮겨라」와 「상위를 비워라」가 한 명령에 함께 오면 **무엇을 원했는지
            # 알 수 없다.** 하나를 골라 실행하면 사람이 고르지 않은 쪽이 조용히 일어난다.
            raise ValueError('parent_task_id and clear_parent cannot be sent together')
        if self.clear_parent:
            # **여기서 하나의 모양으로 접는다** — 아래층은 `parent_task_id` 한 키만 읽으면 되고
            # 「없음」은 `None` 이다. `project_id` + `clear_project` 가 이미 그 모양으로 접힌다.
            # `model_fields_set` 에 넣어야 `changes()` 의 `exclude_unset` 을 지난다.
            self.model_fields_set.add('parent_task_id')
        if not (self.model_fields_set - {'task_id', 'expected_version'}):
            raise ValueError('at least one field is required')
        return self

    def changes(self) -> dict:
        # `clear_parent` 는 **플래그이지 값이 아니다** — 위에서 `parent_task_id=None` 으로 접혔으므로
        # 아래층에 그대로 흘려보내면 같은 사실이 두 키로 도착한다.
        return self.model_dump(exclude_unset=True, exclude={'task_id', 'expected_version', 'clear_parent'})


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
    #: 상위 업무 — 보내면 그 업무 아래로 옮기고, `clear_parent` 면 비운다 (SPEC-007 §4 · D-17).
    #: 둘을 함께 보내면 거절이다 — 판정은 `TaskEditFields` 한 곳에 있다.
    parent_task_id: UUID | None = Field(default=None, title='상위 업무')
    clear_parent: bool = Field(default=False, title='상위 연결 해제')
    #: 선행업무 — 보내면 **전체 교체**, 생략하면 건드리지 않는다. 빈 배열은 「전부 뗀다」다.
    preceding_task_ids: list[UUID] | None = Field(default=None, title='선행업무')
    approver_id: str | None = Field(default=None, title='승인자')
    clear_approver: bool = Field(default=False, title='승인자 비우기')

    def changes(self) -> dict:
        values = {key: getattr(self, key) for key in ('title', 'description') if getattr(self, key) is not None}
        for key, clear in [('start_date', self.clear_start_date), ('due_date', self.clear_due_date), ('project_id', self.clear_project)]:
            if clear or getattr(self, key) is not None:
                values[key] = None if clear else getattr(self, key)
        # **`None` 은 「생략」이고 빈 배열은 「전부 뗀다」다.** 둘을 한 값으로 접으면 화면이 선행을
        # 비울 길이 없어진다 — 그래서 이 표면만 `None` 을 생략의 뜻으로 읽는다.
        if self.preceding_task_ids is not None:
            values['preceding_task_ids'] = self.preceding_task_ids
        if self.clear_approver or self.approver_id is not None:
            values['approver_id'] = None if self.clear_approver else self.approver_id
        # **상위는 접지 않고 둘을 그대로 넘긴다** — 「둘을 함께 보냈다」는 거절이 `TaskEditFields` 의
        # 몫이고, 여기서 미리 접으면 그 거절이 이 표면에 닿지 않는다. 다른 칸들과 다른 이유로
        # 다르게 다룬다: 저쪽은 플래그가 그 모델에 없어 여기서 접을 수밖에 없다.
        if self.clear_parent:
            values['clear_parent'] = True
        if self.parent_task_id is not None:
            values['parent_task_id'] = self.parent_task_id
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


class TaskScheduleCreateInput(BaseModel):
    """배정 생성의 본문 — **회차를 받지 않는다** (SPEC-004 §4 · 증보 K10).

    생성 전용이라 바꿀 값이 없고, 조건부 회차를 두면 **잃은 갱신이 열린다**.
    멱등 키는 본문이 아니라 `Idempotency-Key` **헤더**로 온다 (SPEC-003 §5 K-1 계승).
    """

    model_config = ConfigDict(extra="forbid")
    on_date: date = Field(title="배정할 날")
    starts_at: time = Field(title="시작 시각")
    ends_at: time = Field(title="종료 시각")


class TaskScheduleRetimeInput(BaseModel):
    """시각 변경의 본문. **`on_date` 를 받지 않는다** — 날짜 이동 경로가 없다 (§2.3 R6).

    `expected_version` 은 **그 배정 자신의 회차**이고 **무조건 필수**다 (증보 K8·K10).
    두 시각을 **둘 다** 보낸다 — 세로 손잡이가 한쪽만 끌어도 계약은 한 쌍이다.
    """

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, title="배정 버전")
    starts_at: time = Field(title="시작 시각")
    ends_at: time = Field(title="종료 시각")
