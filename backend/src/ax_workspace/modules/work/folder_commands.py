"""Owner-bound folder inputs and command receipts."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


class FolderCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["personal", "team"] = Field(title="자료함 종류")
    title: str = Field(min_length=1, max_length=300, title="자료함 명")
    organization_id: str | None = Field(default=None, title="소유 조직")


class FolderArchiveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: UUID = Field(title="대상 자료함")


class FolderMaterialDetachCommand(FolderArchiveCommand):
    material_id: UUID = Field(title="대상 자료")


class FolderView(TypedDict):
    folder_id: str
    kind: Literal["personal", "team"]
    title: str
    owner_member_id: str | None
    organization_id: str | None


class FolderArchiveResult(TypedDict):
    folder_id: str
    archived: bool


class FolderDetachResult(TypedDict):
    folder_id: str
    material_id: str
    detached: bool
