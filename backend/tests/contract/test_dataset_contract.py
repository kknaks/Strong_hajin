"""dataset은 사람이 채우는 계약이고, 검증은 값을 보여 주지 않고 자리만 말한다.

The repository owns the shape and the commands; the data lives in a folder outside it. Validation reads the person's
own CSVs and reports where a problem is — file, row, column — never what the cell said.
"""
import csv
import json
from pathlib import Path

from ax_workspace.entrypoints.dataset import initialize, main, read_tables
from ax_workspace.modules.datasets.schema import SCHEMA_VERSION, TABLES
from ax_workspace.modules.datasets.validation import cycles, validate


def _write(target: Path, table: str, rows: list[dict[str, str]]) -> None:
    header = next(item for item in TABLES if item.name == table).header
    with (target / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _organization(target: Path) -> None:
    initialize(target, name="fixture", as_of="2026-09-02")
    _write(target, "organization_units", [
        {"key": "company", "name": "회사", "unit_type": "company", "parent_key": "", "display_order": "0"},
        {"key": "sales", "name": "영업본부", "unit_type": "division", "parent_key": "company", "display_order": "1"},
    ])
    _write(target, "grades", [{"key": "manager", "name": "과장", "display_order": "2"}])
    _write(target, "jobs", [{"key": "planning", "name": "기획"}])
    _write(target, "positions", [{"key": "head", "name": "본부장", "unit_type": "division", "slot": "head", "role_key": "team-lead"}])
    _write(target, "members", [
        {"key": "a", "display_name": "가", "employment_state": "active", "primary_unit_key": "sales", "role_key": "member", "grade_key": "manager", "employed_from": "", "employed_until": ""},
    ])
    _write(target, "memberships", [{"member_key": "a", "unit_key": "sales", "kind": "primary", "valid_from": "", "valid_until": ""}])
    _write(target, "appointments", [{"member_key": "a", "unit_key": "sales", "position_key": "head", "kind": "primary", "valid_from": "", "valid_until": ""}])
    _write(target, "job_assignments", [{"member_key": "a", "job_key": "planning", "kind": "primary"}])
    _write(target, "logins", [{"member_key": "a", "email": "a@example.test"}])


def test_init_writes_the_empty_shape_a_person_can_fill(tmp_path) -> None:
    target = tmp_path / "dataset"
    target.mkdir()
    initialize(target, name="더에쓰씨 조직", as_of="2026-09-02")

    assert (target / "files").is_dir()
    manifest = (target / "manifest.yaml").read_text(encoding="utf-8")
    assert f"schema_version: {SCHEMA_VERSION}" in manifest and "2026-09-02" in manifest
    for table in TABLES:
        header = (target / table.filename).read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",") == table.header
    # The README says the rules that matter while someone is filling it in.
    readme = (target / "README.md").read_text(encoding="utf-8")
    assert "원문에 없는 날짜는 비워 둡니다" in readme and "비밀번호" in readme

    # Running it again keeps what is already there.
    _write(target, "grades", [{"key": "manager", "name": "과장", "display_order": "2"}])
    initialize(target, name="더에쓰씨 조직", as_of="2026-09-02")
    assert len(read_tables(target)["grades"]) == 1


def test_a_dataset_that_holds_together_passes_and_one_that_does_not_says_where(tmp_path) -> None:
    target = tmp_path / "dataset"
    target.mkdir()
    _organization(target)
    assert validate(read_tables(target)).ok

    _write(target, "members", [
        {"key": "a", "display_name": "가", "employment_state": "active", "primary_unit_key": "sales", "role_key": "member", "grade_key": "manager", "employed_from": "", "employed_until": ""},
        {"key": "a", "display_name": "또 가", "employment_state": "active", "primary_unit_key": "sales", "role_key": "member", "grade_key": "", "employed_from": "", "employed_until": ""},
        {"key": "b", "display_name": "나", "employment_state": "퇴사", "primary_unit_key": "없는팀", "role_key": "member", "grade_key": "", "employed_from": "어제", "employed_until": ""},
    ])
    report = validate(read_tables(target))
    kinds = {(problem.table, problem.column, problem.kind) for problem in report.problems}
    assert ("members", "key", "duplicate") in kinds
    assert ("members", "employment_state", "unknown_value") in kinds
    assert ("members", "primary_unit_key", "unknown_reference") in kinds
    assert ("members", "employed_from", "not_a_date") in kinds
    # Where and what kind, never the cell itself.
    assert all("또 가" not in str(problem) and "없는팀" not in str(problem) for problem in report.problems)
    assert all(problem.row is not None for problem in report.problems)


def test_an_organization_that_contains_itself_is_a_mistake() -> None:
    rows = [
        {"key": "a", "parent_key": "b"},
        {"key": "b", "parent_key": "a"},
        {"key": "c", "parent_key": ""},
    ]
    assert cycles(rows, key="key", parent="parent_key") == ["a", "b"]


def test_the_commands_refuse_to_put_a_dataset_inside_the_repository(tmp_path, capsys) -> None:
    repository = Path(__file__).resolve().parents[3]
    assert main(["init", str(repository / "datasets" / "actual")]) == 2
    assert "저장소 밖" in capsys.readouterr().err
    assert not (repository / "datasets" / "actual").exists()


def test_validate_reports_counts_and_fails_when_something_is_wrong(tmp_path, capsys) -> None:
    target = tmp_path / "dataset"
    target.mkdir()
    _organization(target)
    assert main(["validate", str(target)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["rows"]["members"] == 1 and answer["problem_count"] == 0

    _write(target, "memberships", [{"member_key": "없는사람", "unit_key": "sales", "kind": "primary", "valid_from": "", "valid_until": ""}])
    assert main(["validate", str(target)]) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["problem_count"] == 1 and "members에 없는 키" in failed["problems"][0]
