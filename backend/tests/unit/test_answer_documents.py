"""Answer content binds explicit references; titles are never identifiers."""
import pytest

from ax_workspace.modules.ax_execution.answer_documents import AnswerDocument, bind_answer_resources


def test_mixed_answer_binds_each_item_to_observed_resources_without_copying_titles():
    task = {"reference_id": "task-receipt", "resource_type": "task", "resource_id": "t1", "title": "동일한 제목"}
    meeting = {"reference_id": "meeting-receipt", "resource_type": "meeting", "resource_id": "m1", "title": "동일한 제목"}
    elements = [
        {"key": "results", "type": "resource_list", "ordered": True, "items": [
            {"ref": "task:t1", "description": "먼저 정리합니다."},
            {"ref": "meeting:m1", "description": "그다음 논의합니다."},
        ]},
        {"key": "meeting", "type": "resource_reference", "ref": "meeting:m1"},
    ]
    result = bind_answer_resources("## 진행 순서\n\n{{results}}\n\n배경은 {{meeting}}에서 확인하세요.", elements, [task, meeting])
    assert result[0]["items"] == [
        {"ref": "task-receipt", "description": "먼저 정리합니다."},
        {"ref": "meeting-receipt", "description": "그다음 논의합니다."},
    ]
    assert result[1]["ref"] == "meeting-receipt"
    assert elements[1]["ref"] == "meeting:m1"


@pytest.mark.parametrize("body,elements", [
    ("{{missing}}", []),
    ("설명", [{"key": "unused", "type": "resource_reference", "ref": "task:t1"}]),
    ("{{a}}", [{"key": "a", "type": "resource_reference", "ref": "task:t1"}] * 2),
    ("먼저 {{a}}를 보세요", [{"key": "a", "type": "resource_list", "ordered": True, "items": []}]),
    ("{{a}}", [{"key": "a", "type": "execute", "command": "approve"}]),
    ("{{a}}", [{"key": "a", "type": "resource_reference", "ref": "https://evil.test"}]),
    ("{{a}}", [{"key": "a", "type": "resource_reference", "ref": "task:t1", "title": "모델이 만든 제목"}]),
    (" ", []),
    ("{{a}}", [{"key": "a", "type": "resource_list", "ordered": True, "items": [
        {"ref": "task:t1", "description": "설명 안의 {{nested}}"},
    ]}]),
])
def test_invalid_answer_contract_cannot_be_final(body, elements):
    with pytest.raises(ValueError):
        AnswerDocument.model_validate({"body": body, "elements": elements})


def test_markdown_literals_do_not_require_or_activate_elements():
    body = "\\{{escaped}} `{{inline}}`\n\n```json\n{{fenced}}\n```\n\n    {{indented}}\n\n**문단**"
    assert AnswerDocument.model_validate({"body": body, "elements": []}).body == body


def test_unobserved_reference_is_not_guessed_from_a_matching_title():
    with pytest.raises(ValueError):
        bind_answer_resources("{{a}}", [{"key": "a", "type": "resource_reference", "ref": "task:missing"}],
                              [{"reference_id": "other", "resource_type": "task", "resource_id": "t1", "title": "missing"}])


def test_a_project_is_a_reference_an_answer_can_bind():
    """E2E-12 — 프로젝트를 가리킬 형식이 없으면 모델은 `task:<프로젝트 id>` 를 지어내고 턴 전체가 실패한다."""
    project = {"reference_id": "project-receipt", "resource_type": "project", "resource_id": "p1", "title": "하반기 제품 개편"}
    elements = [{"key": "project", "type": "resource_reference", "ref": "project:p1"}]

    assert bind_answer_resources("연결할 곳은 {{project}}입니다.", elements, [project]) == [
        {"key": "project", "type": "resource_reference", "ref": "project-receipt"},
    ]


def test_the_provider_output_schema_admits_project_references():
    from ax_workspace.platform.codex_cli import _CONVERSATION_OUTPUT_SCHEMA

    pattern = _CONVERSATION_OUTPUT_SCHEMA["$defs"]["ResourceReference"]["properties"]["ref"]["pattern"]
    assert "project" in pattern
