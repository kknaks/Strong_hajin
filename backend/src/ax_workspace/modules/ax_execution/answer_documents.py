"""Provider-neutral answer content. References bind to authorized observations, never titles."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Annotated, Any, Literal, Self

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnswerFormatError(ValueError):
    """The answer cannot safely be presented as a completed response."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Key = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")]
ResourceRef = Annotated[str, Field(pattern=r"^(task|meeting|work_request|material|report):[^\s:]+$", max_length=180)]


class ResourceReference(_StrictModel):
    key: Key
    type: Literal["resource_reference"]
    ref: ResourceRef


class ResourceListItem(_StrictModel):
    ref: ResourceRef
    description: str


class ResourceList(_StrictModel):
    key: Key
    type: Literal["resource_list"]
    ordered: bool
    items: Annotated[list[ResourceListItem], Field(max_length=500)]


class AnswerDocument(_StrictModel):
    body: Annotated[str, Field(min_length=1)]
    elements: Annotated[list[ResourceReference | ResourceList], Field(max_length=200)]

    @model_validator(mode="after")
    def validate_placements(self) -> Self:
        if not self.body.strip():
            raise AnswerFormatError("답변 내용이 비어 있습니다.")
        by_key = {element.key: element for element in self.elements}
        if len(by_key) != len(self.elements):
            raise AnswerFormatError("답변 요소의 key가 중복되었습니다.")
        used: set[str] = set()
        # Keep escaped braces separate from text. Code, raw HTML and link destinations are not insertion sites.
        tokens = MarkdownIt("commonmark").enable("table").disable("text_join").parse(self.body)
        for index, token in enumerate(tokens):
            link_depth = 0
            for child in token.children or []:
                if child.type == "link_open":
                    link_depth += 1
                elif child.type == "link_close":
                    link_depth -= 1
                if child.type != "text" or link_depth:
                    continue
                for match in re.finditer(r"\{\{([^{}\r\n]*)\}\}", child.content):
                    key = match[1]
                    if key not in by_key:
                        raise AnswerFormatError("답변의 참조 표시와 요소가 일치하지 않습니다.")
                    if isinstance(by_key[key], ResourceList) and (
                        token.content.strip() != match[0] or index == 0 or tokens[index - 1].type != "paragraph_open"
                    ):
                        raise AnswerFormatError("결과 목록은 독립된 문단에 배치해야 합니다.")
                    used.add(key)
        if used != by_key.keys():
            raise AnswerFormatError("답변에 배치되지 않은 요소가 있습니다.")
        for element in self.elements:
            if isinstance(element, ResourceList):
                for item in element.items:
                    if item.description.strip():
                        # Descriptions are Markdown-only leaves, not another element namespace.
                        AnswerDocument.model_validate({"body": item.description, "elements": []})
        return self


def bind_answer_resources(body: str, elements: Any, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bind provider refs to receipts from this conversation, already reauthorized by their owners."""
    document = AnswerDocument.model_validate({"body": body, "elements": elements})
    by_ref = {f"{row['resource_type']}:{row['resource_id']}": row["reference_id"] for row in resources}
    bound = [element.model_dump() for element in document.elements]
    for element in bound:
        for item in element["items"] if element["type"] == "resource_list" else [element]:
            if item["ref"] not in by_ref:
                raise AnswerFormatError("답변에 확인할 수 없는 참조가 있습니다. 다시 요청해 주세요.")
            item["ref"] = by_ref[item["ref"]]
    return bound


def project_answer_document(document: dict | None, readable_ids: set[str]) -> dict | None:
    """Keep saved placement while withholding metadata of references that are no longer readable."""
    if document is None:
        return None
    projected = deepcopy(document)
    for element in projected.get("elements", []):
        for item in element["items"] if element["type"] == "resource_list" else [element]:
            if item["ref"] not in readable_ids:
                item["ref"] = None
                if "description" in item:
                    item["description"] = ""
    return projected


def answer_context_excerpt(body: str, document: dict | None, resources: list[dict[str, Any]]) -> str:
    """Retell saved element order with current titles/ids when a provider checkpoint is unavailable."""
    if document is None:
        return body[:400]
    by_id = {row["reference_id"]: row for row in resources if row.get("reference_id")}
    projected = project_answer_document(document, set(by_id))
    for element in projected["elements"]:
        for item in element["items"] if element["type"] == "resource_list" else [element]:
            resource = by_id.get(item["ref"])
            if resource:
                item["ref"] = f"{resource['resource_type']}:{resource['resource_id']}"
                item["title"] = resource["title"]
    # This is a bounded context excerpt, not a new provider output document.
    return (body[:400] + "\n답변 요소: " + json.dumps(projected["elements"], ensure_ascii=False))[:4000]
