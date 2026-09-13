"""Report inputs preserve source occurrence identities and the selected draft version."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class ReportSourceReference(BaseModel):
    # Existing report sources may carry additional captured metadata. Keep it as
    # JSON while validating the three fields that identify one work occurrence.
    model_config = ConfigDict(extra='allow', str_strip_whitespace=True)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    task_id: str = Field(min_length=1)
    task_version: int = Field(ge=1)
    occurred_at: str = Field(min_length=1)
    state: str | None = None

    @field_validator('task_id')
    @classmethod
    def task_identity(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator('occurred_at')
    @classmethod
    def occurrence_identity(cls, value: str) -> str:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value


class ReportDraftVersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    draft_id: UUID = Field(title='대상 보고 초안')
    expected_version: int = Field(ge=1, title='보고 초안 버전')


class ReportEditInput(ReportDraftVersionInput):
    body: str = Field(min_length=1, title='보고 본문')
    include_source_refs: list[ReportSourceReference] = Field(default_factory=list, title='포함할 보고 근거')
    exclude_source_refs: list[ReportSourceReference] = Field(default_factory=list, title='제외할 보고 근거')

    @field_validator('include_source_refs', 'exclude_source_refs', mode='before')
    @classmethod
    def absent_sources(cls, value: object) -> object:
        return [] if value is None else value

    def values(self) -> dict:
        result = self.model_dump(mode='json')
        for key in ('include_source_refs', 'exclude_source_refs'):
            result[key] = [source.model_dump(mode='json', exclude_unset=True) for source in getattr(self, key)]
        return result


class ReportEditCommand(ReportEditInput):
    report_id: UUID = Field(title='대상 일일보고')


class ReportSubmitInput(ReportDraftVersionInput):
    reason: str | None = Field(default=None, title='제출 사유')


class ReportSubmitCommand(ReportSubmitInput):
    report_id: UUID = Field(title='대상 일일보고')
