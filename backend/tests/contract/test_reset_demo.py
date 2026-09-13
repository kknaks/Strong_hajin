import pytest

from ax_workspace.entrypoints.reset_demo import main, reset_database


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://ax:ax@db.example.test/ax_demo",
        "postgresql+psycopg://ax:ax@localhost/production",
        "sqlite:////tmp/production.db",
    ],
)
def test_reset_rejects_non_demo_or_nonlocal_database_urls(database_url: str) -> None:
    with pytest.raises(ValueError, match="safe local demo database"):
        reset_database(database_url)


def test_reset_allows_an_explicit_demo_sqlite_database(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"

    reset_database(database_url)


def test_reset_allows_an_explicit_test_sqlite_database(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'ax_test.db'}"

    reset_database(database_url)


def test_reset_command_is_forbidden_in_production_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_PROFILE", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="development and test"):
        # 인자를 명시해 pytest 자신의 argv가 이 명령의 것으로 읽히지 않게 한다.
        main([])
