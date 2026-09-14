"""Meeting command values shared by HTTP, MCP and confirmed execution.

Transport paths add the target identity; neither defaults nor serialization may
turn an omitted edit into an explicit clear.
"""
from datetime import UTC, date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MeetingVersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1, title="대상 버전")


class MeetingUpdateInput(MeetingVersionInput):
    title: str | None = Field(default=None, min_length=1, max_length=300, title="회의 명")
    description: str | None = Field(default=None, title="내용")
    starts_at: datetime | None = Field(default=None, title="시작")
    ends_at: datetime | None = Field(default=None, title="종료")
    visibility: Literal["public", "private"] | None = Field(default=None, title="공개 범위")

    @model_validator(mode="after")
    def validate_explicit_fields(self) -> Self:
        for field in self.model_fields_set - {"description", "expected_version", "meeting_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared")
        for timestamp in (self.starts_at, self.ends_at):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("meeting times must include a timezone")
        return self

    def changes(self) -> dict:
        return self.model_dump(exclude_unset=True, exclude={"expected_version", "meeting_id"})


class MeetingUpdateCommand(MeetingUpdateInput):
    meeting_id: UUID = Field(title="대상 회의")


class MeetingShareInput(MeetingVersionInput):
    member_id: str = Field(min_length=1, max_length=100, title="공유 대상")


class MeetingShareCommand(MeetingShareInput):
    meeting_id: UUID = Field(title="대상 회의")


class MeetingNoteCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    body: str = Field(min_length=1, title="회의록 본문")


class MeetingNoteCreateCommand(MeetingNoteCreateInput):
    meeting_id: UUID = Field(title="대상 회의")


class MeetingNoteSaveInput(MeetingVersionInput, MeetingNoteCreateInput):
    pass


class MeetingNoteSaveCommand(MeetingNoteSaveInput):
    meeting_id: UUID = Field(title="대상 회의")


class MeetingNoteFinalizeCommand(MeetingVersionInput):
    meeting_id: UUID = Field(title="대상 회의")


class MeetingMaterialLinkInput(MeetingVersionInput):
    url: str = Field(min_length=1, max_length=500, title='링크 주소')
    label: str = Field(min_length=1, max_length=300, title='자료 이름')

    @model_validator(mode='after')
    def validate_link(self) -> Self:
        from ax_workspace.modules.work.material_values import MaterialError, normalize_material_link
        try:
            self.url = normalize_material_link(self.url)
        except MaterialError as error:
            raise ValueError(str(error)) from error
        return self


class MeetingMaterialLinkCommand(MeetingMaterialLinkInput):
    meeting_id: UUID = Field(title='대상 회의')


class MeetingMaterialDetachCommand(MeetingVersionInput):
    meeting_id: UUID = Field(title='대상 회의')
    binding_id: UUID = Field(title='분리할 자료 연결')


class MeetingSummaryAdoptCommand(MeetingVersionInput):
    meeting_id: UUID = Field(title='대상 회의')
    summary_id: UUID = Field(title='채택할 요약')


class MeetingSpeakerInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    transcript_revision_id: UUID = Field(title='원문 전사 버전')
    speaker_label: str = Field(min_length=1, max_length=100, title='화자 표시')
    member_id: str = Field(min_length=1, max_length=100, title='확인한 구성원')
    scope: Literal['segment_range', 'speaker_track'] = Field(title='적용 범위')
    raw_start_source_key: str = Field(min_length=1, max_length=200, title='원문 시작 구간')
    raw_end_source_key: str = Field(min_length=1, max_length=200, title='원문 종료 구간')


class MeetingSpeakerCommand(MeetingSpeakerInput):
    meeting_id: UUID = Field(title='대상 회의')


class MeetingFollowupInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['task', 'work_request'] = Field(default='task', title='후속 생성 종류')
    title: str | None = Field(default=None, max_length=300, title='업무 명')
    assignee_id: str | None = Field(default=None, max_length=100, title='요청 대상')


class MeetingFollowupCommand(MeetingFollowupInput):
    meeting_id: UUID = Field(title='대상 회의')
    summary_id: UUID = Field(title='근거 요약')
    statement_index: int = Field(ge=0, title='후속 문장 번호')


class MeetingAgendaDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)


class MeetingReservationInput(BaseModel):
    """The current meeting reservation contract shared by HTTP, MCP and approval replay."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, max_length=300)
    purpose: str | None = Field(default=None, max_length=1000)
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=300)
    attendee_ids: list[str] = Field(default_factory=list)
    external_attendees: list[str] = Field(default_factory=list)
    agendas: list[MeetingAgendaDraftInput] = Field(default_factory=list)
    carried_from_meeting_id: UUID | None = None
    room_id: int | None = None

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("meeting times must include a timezone")
        self.starts_at = self.starts_at.astimezone(UTC)
        self.ends_at = self.ends_at.astimezone(UTC)
        self.attendee_ids = list(dict.fromkeys(item for item in self.attendee_ids if item))
        self.external_attendees = list(dict.fromkeys(item for item in self.external_attendees if item))
        return self

    def values(self) -> dict:
        values = self.model_dump(exclude={"room_id"})
        values["agendas"] = [row.model_dump() for row in self.agendas]
        return values


class MeetingInfoPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, max_length=300)
    purpose: str | None = Field(default=None, max_length=1000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=300)
    attendee_ids: list[str] | None = None
    external_attendees: list[str] | None = None

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        for value in (self.starts_at, self.ends_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("meeting times must include a timezone")
        return self

    def changes(self) -> dict:
        return self.model_dump(exclude_unset=True)


class MeetingNoteLineInput(BaseModel):
    """저장이 싣는 최종 벌 줄 하나 — **줄 id 를 함께 든다** (SCAX-SPEC-004 v0.5.1 §8-9).

    `line_id` 가 오면 그 줄을 이어 쓴다: 본문이 그대로면 계보(`from_lines`)도 그대로이고, 본문이
    달라지면 **그 줄의** 계보만 지워진다. `line_id` 가 없거나 `null` 이면 새 줄이고 계보가 없다.
    목록에 없는 id 는 지워진 줄이며, 그 안건의 줄이 아닌 id 가 오면 **그 줄만** 거절된다.

    **글자 하나로는 보내지 못한다.** id 없이 목록만 보내면 손대지 않은 줄의 계보까지 첫 저장에
    사라지고, 그러면 「내가 적은 것이 최종본에 살아남았나」를 물을 재료가 없어진다 (검수 F-3).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    line_id: UUID | None = None
    text: str = Field(max_length=2000)


class MeetingAgendaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, max_length=100)
    concluded: bool | None = None
    order: int | None = Field(default=None, ge=1)
    #: 그 안건의 **최종 벌** 줄 목록을 통째로 보낸다 — 줄마다 id 를 실어 계보를 잇는다 (§8-9).
    #: 원본 두 벌의 안건에 이것이 오면 거절한다: 사람 벌·AI 벌은 사후에 손대는 자리가 없다 (D53).
    lines: list[MeetingNoteLineInput] | None = None
    expected_last_saved_at: str | None = None

    def changes(self) -> dict:
        changes = self.model_dump(exclude_unset=True)
        if changes.get("lines") is not None:
            # `exclude_unset` 은 안쪽 모델에도 걸린다 — id 를 안 실은 줄에는 키 자체가 없고, 그것이
            # 「id 없이 온 줄」이다 (§8-9).
            changes["lines"] = [
                {
                    "line_id": None if row.get("line_id") is None else str(row["line_id"]),
                    "text": row.get("text") or "",
                }
                for row in self.model_dump()["lines"]
            ]
        return changes


class MeetingShareManyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    member_ids: list[str] = Field(min_length=1)


class MeetingMemoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=2000)


class MeetingTodoPromotionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    assignee_id: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    due_date: date | None = None
    checklist: list[str] | None = None
