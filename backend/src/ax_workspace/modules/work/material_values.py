"""Pure values accepted by Work material commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from ax_workspace.modules.work.errors import TaskError


MATERIAL_KINDS = frozenset({"input", "output"})
LINK_SCHEMES = frozenset({"http", "https"})
REFERENCE_TYPES = frozenset({"meeting"})
MAX_MATERIAL_BYTES = 25 * 1024 * 1024


class MaterialError(TaskError):
    pass


class MaterialNotFound(MaterialError):
    pass


class MaterialRole(StrEnum):
    INPUT = "input"
    OUTPUT = "output"

    @classmethod
    def create(cls, value: str) -> MaterialRole:
        try:
            return cls(value)
        except ValueError as error:
            raise MaterialError("material kind must be input or output") from error


@dataclass(frozen=True, slots=True)
class MaterialLink:
    url: str
    label: str

    @classmethod
    def create(cls, url: str, label: str) -> MaterialLink:
        clean_url = normalize_material_link(url)
        clean_label = label.strip()[:300]
        if not clean_label:
            raise MaterialError("material label is required")
        return cls(url=clean_url, label=clean_label)


@dataclass(frozen=True, slots=True)
class MaterialReference:
    resource_type: str
    resource_id: str

    @classmethod
    def create(cls, resource_type: str, resource_id: str) -> MaterialReference:
        if resource_type == "task":
            raise MaterialError("업무는 자료가 아니라 참고 업무로 연결하세요")
        if resource_type not in REFERENCE_TYPES:
            raise MaterialError(f"material reference type must be one of {sorted(REFERENCE_TYPES)}")
        return cls(resource_type=resource_type, resource_id=str(resource_id))


def normalize_material_link(url: str) -> str:
    """A material link must be openable and must carry no secret of its own."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise MaterialError("material url is required")
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in LINK_SCHEMES or not parts.netloc:
        raise MaterialError("material url must be an http(s) address")
    if "@" in parts.netloc:
        raise MaterialError("material url must not carry credentials")
    if len(cleaned) > 500:
        raise MaterialError("material url is too long")
    return cleaned
