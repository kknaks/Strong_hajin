"""The dataset command owns repository safety and the one-folder import boundary."""

from pathlib import Path

from ax_workspace.entrypoints.dataset import initialize as dataset_tables
from ax_workspace.entrypoints.dataset import main
from ax_workspace.entrypoints.reset_demo import reset_database
from scenario_csv_support import make_plan


def test_dataset_import_refuses_a_repository_path_before_reading_it(capsys) -> None:
    inside = Path(__file__).resolve().parents[2]

    assert main(["import", str(inside)]) == 2
    assert "저장소 밖" in capsys.readouterr().err


def test_one_folder_is_one_import_command(tmp_path, monkeypatch, capsys) -> None:
    target = make_plan(tmp_path)
    dataset_tables(target, name="fixture", as_of="2026-09-02")
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AX_PROFILE", "development")
    reset_database(database_url)

    assert main(["import", str(target)]) == 0
    printed = capsys.readouterr().out
    assert '"scenario"' in printed
    assert "하지 않은 것" in printed
