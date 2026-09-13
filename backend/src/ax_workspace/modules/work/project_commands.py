"""Project command input contracts; participation policy stays with its owner."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


class ProjectReleaseResult(TypedDict):
    project_id: str
    member_id: str
    assignment_id: str
    released: bool


class ProjectCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=300, title="프로젝트 명")
    description: str | None = Field(default=None, title="설명")
    starts_on: date | None = Field(default=None, title="시작일")
    ends_on: date | None = Field(default=None, title="종료일")
    external_key: str | None = Field(default=None, max_length=200, title="외부 식별자")


class ProjectMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    member_id: str = Field(min_length=1, max_length=100, title="구성원")
    kind: Literal["lead", "member"] = Field(default="member", title="참여 종류")
    valid_from: datetime | None = Field(default=None, title="참여 시작")
    valid_until: datetime | None = Field(default=None, title="참여 종료")


class ProjectMemberCommand(ProjectMemberInput):
    project_id: UUID = Field(title="대상 프로젝트")


class ProjectReleaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    assignment_id: UUID | None = Field(default=None, title="참여 회차")
    reason: str | None = Field(default=None, max_length=4000, title="종료 사유")


class ProjectReleaseCommand(ProjectReleaseInput):
    # An AX approval always names the immutable participation round it answers.
    assignment_id: UUID = Field(title="참여 회차")
    project_id: UUID = Field(title="대상 프로젝트")
    member_id: str = Field(min_length=1, max_length=100, title="구성원")


class ProjectWorkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300, title="업무 명")
    description: str | None = Field(default=None, title="설명")
    start_date: date | None = Field(default=None, title="시작일")
    due_date: date | None = Field(default=None, title="기한")


class ProjectWorkCommand(ProjectWorkInput):
    project_id: UUID = Field(title="대상 프로젝트")
