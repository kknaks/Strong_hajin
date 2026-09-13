from datetime import UTC, date, datetime

import pytest

from ax_workspace.modules.work.material_extraction import MAX_SEARCH_HITS
from ax_workspace.modules.work.material_search_policy import (
    RESOURCE_TYPES,
    MaterialProjectionContext,
    MaterialSearchQuery,
    is_registered_within,
    project_material,
)
from ax_workspace.modules.work.material_values import MaterialError


def _projection(**changes: object):
    values: dict[str, object] = {
        "source_kind": "file",
        "attachment_integrity_ref": "sha256:current",
        "extraction_status": "completed",
        "extraction_integrity_ref": "sha256:current",
        "projection_failed": False,
        "selected": False,
    }
    values.update(changes)
    return project_material(MaterialProjectionContext(**values))  # type: ignore[arg-type]


def test_material_search_query_normalizes_scope_anchor_and_limit() -> None:
    broad = MaterialSearchQuery.create(
        "  공급사   납기일  ",
        limit=500,
        resource_types=None,
        resource_type=None,
        resource_id=None,
    )
    anchored = MaterialSearchQuery.create(
        "계약서",
        limit=0,
        resource_types=["task", "meeting"],
        resource_type="meeting",
        resource_id="meeting-1",
    )

    assert broad.query == "공급사 납기일"
    assert broad.resource_types == RESOURCE_TYPES
    assert broad.limit == MAX_SEARCH_HITS
    assert anchored.resource_types == frozenset({"meeting"})
    assert (anchored.resource_type, anchored.resource_id, anchored.limit) == ("meeting", "meeting-1", 1)


def test_material_search_query_rejects_invalid_shapes() -> None:
    cases = [
        ({"query": "   "}, "query is required"),
        ({"resource_types": []}, "unknown material resource type"),
        ({"resource_types": ["unknown"]}, "unknown material resource type"),
        ({"resource_type": "task"}, "supplied together"),
        ({"resource_id": "task-1"}, "supplied together"),
        (
            {"resource_types": ["meeting"], "resource_type": "task", "resource_id": "task-1"},
            "outside resource_types",
        ),
    ]
    for changes, message in cases:
        values = {
            "query": "검색어",
            "limit": 5,
            "resource_types": None,
            "resource_type": None,
            "resource_id": None,
        }
        values.update(changes)

        with pytest.raises(MaterialError, match=message):
            MaterialSearchQuery.create(**values)  # type: ignore[arg-type]


def test_only_current_complete_projection_is_searchable_in_a_general_search() -> None:
    current = _projection()
    stale = _projection(extraction_integrity_ref="sha256:old")
    failed = _projection(projection_failed=True)

    assert current.searchable is True
    assert current.needs_backfill is False
    assert stale.searchable is False
    assert failed.searchable is False
    assert stale.unavailable_reason == "extraction"


def test_partial_projection_is_searchable_only_when_the_artifact_is_selected() -> None:
    general = _projection(source_kind="native_revision", extraction_status="partial")
    selected = _projection(source_kind="native_revision", extraction_status="partial", selected=True)

    assert general.searchable is False
    assert selected.searchable is True


def test_missing_file_projection_requests_backfill_but_links_stay_explainably_unavailable() -> None:
    missing_file = _projection(extraction_status=None, extraction_integrity_ref=None)
    link = _projection(
        source_kind="external_link",
        attachment_integrity_ref="url:https://example.com",
        extraction_status=None,
        extraction_integrity_ref=None,
    )

    assert (missing_file.needs_backfill, missing_file.searchable, missing_file.unavailable_reason) == (
        True,
        False,
        "extraction",
    )
    assert (link.needs_backfill, link.searchable, link.unavailable_reason) == (False, False, "external_link")


def test_registration_date_filter_is_inclusive_and_missing_dates_do_not_match() -> None:
    registered_at = datetime(2026, 9, 13, 3, 0, tzinfo=UTC)

    assert is_registered_within(registered_at, since=date(2026, 9, 13), until=date(2026, 9, 13)) is True
    assert is_registered_within(registered_at, since=date(2026, 9, 14), until=None) is False
    assert is_registered_within(registered_at, since=None, until=date(2026, 9, 12)) is False
    assert is_registered_within(None, since=date(2026, 9, 1), until=None) is False
