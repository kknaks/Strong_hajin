from uuid import UUID

import pytest

from ax_workspace.modules.actions.domain import ActionError
from ax_workspace.modules.actions.payloads import (
    attachment_draft_ids,
    payload_diff,
    proposed_changes,
    revision_changes,
    suggested_changes,
    validate_task_progress_batch_edit,
)


def test_attachment_draft_ids_deduplicates_valid_ids_without_reordering() -> None:
    first = "00000000-0000-0000-0000-000000000001"
    second = "00000000-0000-0000-0000-000000000002"

    assert attachment_draft_ids([first, UUID(second), first]) == [first, second]


def test_attachment_draft_ids_rejects_non_uuid_values() -> None:
    with pytest.raises(ActionError, match="must contain UUID values"):
        attachment_draft_ids(["not-an-id"])


def test_task_progress_edit_preserves_targets_and_version_guards() -> None:
    base = {
        "operations": [
            {"kind": "progress.set", "task_id": "task-1", "expected_version": 2, "progress": 20},
            {
                "kind": "checklist.update",
                "task_id": "task-2",
                "item_id": "item-1",
                "expected_version": 3,
                "done": False,
            },
        ]
    }
    validate_task_progress_batch_edit(
        base,
        {"operations": [{"kind": "progress.set", "task_id": "task-1", "expected_version": 2, "progress": 80}]},
    )

    with pytest.raises(ActionError, match="원안에 없던 업무"):
        validate_task_progress_batch_edit(
            base,
            {"operations": [{"kind": "progress.set", "task_id": "task-3", "expected_version": 1}]},
        )
    with pytest.raises(ActionError, match="업무 대상이나 기준 버전"):
        validate_task_progress_batch_edit(
            base,
            {
                "operations": [
                    {
                        "kind": "checklist.update",
                        "task_id": "task-2",
                        "item_id": "item-2",
                        "expected_version": 3,
                        "done": True,
                    }
                ]
            },
        )


def test_payload_diff_reports_only_changed_fields_in_key_order() -> None:
    assert payload_diff({"z": 1, "same": 2}, {"a": 3, "same": 2}) == {
        "a": {"before": None, "after": 3},
        "z": {"before": 1, "after": None},
    }


def test_work_request_changes_share_the_domain_allow_list_and_date_contract() -> None:
    assert proposed_changes({"title": "  새 제목 ", "description": ""}) == {"title": "새 제목"}
    assert revision_changes({"description": " ", "clear_due_date": True}) == {
        "description": "",
        "clear_due_date": True,
    }
    with pytest.raises(ActionError, match="변경 제안할 수 없는 항목"):
        proposed_changes({"assignee_id": "mina"})
    with pytest.raises(ActionError, match="YYYY-MM-DD"):
        proposed_changes({"due_date": "tomorrow"})
    with pytest.raises(ActionError, match="clear_due_date"):
        revision_changes({"due_date": ""})


def test_suggested_changes_reads_only_the_structured_changes_condition() -> None:
    assert suggested_changes({"note": "늦춰 주세요", "changes": {"due_date": "2026-10-01"}}) == {
        "due_date": "2026-10-01"
    }
    assert suggested_changes(None) == {}
