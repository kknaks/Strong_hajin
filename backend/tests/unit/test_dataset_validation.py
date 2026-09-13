from ax_workspace.modules.datasets.validation import cycles


def test_cycles_reports_only_the_members_of_an_organization_cycle() -> None:
    rows = [
        {"key": "a", "parent_key": "b"},
        {"key": "b", "parent_key": "a"},
        {"key": "c", "parent_key": ""},
    ]

    assert cycles(rows, key="key", parent="parent_key") == ["a", "b"]
