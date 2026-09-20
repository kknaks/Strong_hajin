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


class WorkPayloadFields(TaskCreationFields):
    """**내 업무와 업무 요청이 함께 쓰는 한 벌** — 아홉 값이 여기 한 곳에서만 정의된다.

    title · description · start_date · due_date · project_id · parent_task_id · checklist ·
    reference_task_ids · cc_member_ids. 두 표면이 각자 필드를 적으면 한쪽만 늘어났을 때 「같은 업무를
    다르게 저장하는 두 경로」가 생긴다 — 실제로 요청에는 시작일·프로젝트가 없었고 내 업무에는 참조자가
    없었다. 그 어긋남을 다시 만들지 않기 위해 상속으로 묶는다.

    담당(`assignee_id`)은 **여기 없다** — 누구의 일이 되는가는 공통 내용이 아니라 경로의 차이다.
    승인자(`approver_id`)와 선행 업무(`preceding_task_ids`)도 없다: 후속 개발이고, 받지 않는 값을
    미리 열어 두지 않는다.
    """

    project_id: UUID | None = Field(default=None, title='프로젝트')
    #: 참조자 — **읽기와 논의만** 열린다 (`modules/work/parties.py`). 담당도 판단도 옮기지 않는다.
    cc_member_ids: list[str] = Field(default_factory=list, title='참조자')
    #: 선행업무 0..N — **이것이 끝나야 시작한다.** 상위·참고와 다른 세 번째 관계다 (SPEC-001 §4 Data
    #: Contract). 같은 프로젝트 안에서만 서므로 **하나라도 있으면 `project_id` 가 필수**가 된다.
    #: 여기서는 **중복만 턴다** — 자기 자신·프로젝트 일치·순환은 저장소를 아는 application 이 답한다.
    preceding_task_ids: list[UUID] = Field(default_factory=list, title='선행업무')
    #: 승인자 0..1 — 화면 라벨은 **「결재자」**이고 같은 값이다 (SPEC-001 §7 OQ-N).
    #: **두 갈래가 함께 받는다**: 내 업무는 만드는 사람이, 요청은 받는 사람이 담당이고, 그 담당과
    #: 같은 사람은 결재자가 될 수 없다는 규칙 하나가 양쪽에 똑같이 걸린다. 누가 담당인지만 다르므로
    #: **값의 자리는 공통**이고 판정에 넘기는 담당자만 갈래가 정한다.
    approver_id: str | None = Field(default=None, max_length=100, title='승인자')

    @field_validator('project_id', 'approver_id', mode='before')
    @classmethod
    def empty_project(cls, value: object) -> object:
        return None if value == '' else value

    @field_validator('cc_member_ids', 'preceding_task_ids', mode='before')
    @classmethod
    def absent_cc(cls, value: object) -> object:
        return [] if value is None else value

    @model_validator(mode='after')
    def normalize_payload(self) -> Self:
        self.cc_member_ids = list(dict.fromkeys(member for item in self.cc_member_ids if (member := str(item).strip())))
        # **중복을 조용히 털지 않는다.** 나머지 네 거절(자기 자신·프로젝트·순환)과 같은 모양이어야
        # 한다 — 화면은 이미 고른 것을 후보에서 빼고, 그래도 온 것은 `WORK_PREDECESSOR_DUPLICATE` 로
        # 답한다 (SPEC-001 §4 Case Matrix). 여기서 털면 그 거절이 영영 닿지 않는 코드가 된다.
        # 저장 결과에 중복 행이 없다는 사실(§4 Validation 「중복 제거」)은 **활성 행 유일성**이 답한다.
        return self


class TaskCreateInput(WorkPayloadFields):
    #: 담당. 비었거나 본인이면 본인 업무, 다른 사람이면 **수락 없이** 그 사람의 업무가 된다 (WORK-001 Phase 1).
    #: 멱등 키는 여기 없다 — `Idempotency-Key` 헤더이고 MCP 는 도구의 명시적 인자로 받는다.
    #: 승인자도 여기 없다 — W1 은 열만 만들고 값을 받지 않는다.
    assignee_id: str | None = Field(default=None, max_length=100, title='담당')

    @field_validator('assignee_id', mode='before')
    @classmethod
    def empty_person(cls, value: object) -> object:
        return None if value == '' else value

    @model_validator(mode='after')
    def drop_assignee_from_cc(self) -> Self:
        """담당은 참조자가 아니다 — 같은 사람을 두 자리에 세우지 않는다 (요청 쪽과 같은 규칙)."""
        if self.assignee_id:
            self.cc_member_ids = [member for member in self.cc_member_ids if member != self.assignee_id]
        return self

    def for_owner(self, owner_id: str | None) -> Self:
        """만드는 사람 자신은 참조자로 남기지 않는다 — 이미 담당이거나 요청자 자리에 있다."""
        return self.model_copy(
            update={'cc_member_ids': [member for member in self.cc_member_ids if member != str(owner_id or '')]}
        )


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
