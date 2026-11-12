import os

import pytest
from typer.testing import CliRunner

import goal_tracker.cli.database as database_cli
from goal_tracker.cli.app import app
from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.connection import open_database, transaction


runner = CliRunner()


@pytest.fixture
def local_configuration(tmp_path, monkeypatch):
    """Keep every command inside temporary application storage."""
    for name in list(os.environ):
        if name.upper().startswith("GT_"):
            monkeypatch.delenv(name)

    data_dir = tmp_path / "application-data"
    paths = get_app_paths(data_dir)

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GT_DATA_DIR='{data_dir.as_posix()}'\n",
        encoding="utf-8",
    )

    return env_file, paths


def test_command_creates_usable_backup_without_migrating(
    local_configuration,
):
    env_file, paths = local_configuration

    with open_database(paths.database) as connection:
        with transaction(connection):
            connection.execute(
                "CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)"
            )
            connection.execute(
                "INSERT INTO items (title) VALUES (?)",
                ("Preserve this record",),
            )

    result = runner.invoke(
        app,
        ["db", "backup", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Backup created." in result.output

    backups = list(paths.backups.glob("*.sqlite3"))
    assert len(backups) == 1

    for database_path in (paths.database, backups[0]):
        with open_database(database_path) as connection:
            assert connection.execute(
                "SELECT title FROM items"
            ).fetchone()["title"] == "Preserve this record"

            assert connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE name = 'schema_migrations'
                """
            ).fetchone() is None


def test_missing_database_is_reported_without_creating_it(
    local_configuration,
):
    env_file, paths = local_configuration

    result = runner.invoke(
        app,
        ["db", "backup", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "source database does not exist" in result.output
    assert not paths.database.exists()
    assert not paths.backups.exists()


def test_invalid_configuration_is_reported(local_configuration):
    env_file, paths = local_configuration
    env_file.write_text(
        "GT_LOG_LEVEL=INVALID\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["db", "backup", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Backup failed" in result.output
    assert not paths.data_dir.exists()


def test_backup_failure_is_reported(local_configuration, monkeypatch):
    env_file, paths = local_configuration

    def fail_backup(*args, **kwargs):
        raise OSError("Simulated storage failure")

    monkeypatch.setattr(
        database_cli,
        "backup_database",
        fail_backup,
    )

    result = runner.invoke(
        app,
        ["db", "backup", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Backup failed (OSError)" in result.output
    assert "Backup created." not in result.output
    assert not paths.data_dir.exists()


def test_missing_env_file_is_rejected(local_configuration, tmp_path):
    env_file, paths = local_configuration
    missing_file = tmp_path / "missing.env"

    result = runner.invoke(
        app,
        ["db", "backup", "--env-file", str(missing_file)],
    )

    assert result.exit_code == 2
    assert not paths.data_dir.exists()