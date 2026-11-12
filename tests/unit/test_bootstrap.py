import logging
import os
import sqlite3

import pytest
from pydantic import ValidationError

import goal_tracker.bootstrap as bootstrap_module
from goal_tracker.db.connection import open_database, transaction


@pytest.fixture(autouse=True)
def isolate_startup(monkeypatch):
    """Isolate environment variables and restore logging after each test."""
    for name in list(os.environ):
        if name.upper().startswith("GT_"):
            monkeypatch.delenv(name)

    logger = logging.getLogger("goal_tracker")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate

    for handler in original_handlers:
        logger.removeHandler(handler)

    try:
        yield
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

        for handler in original_handlers:
            logger.addHandler(handler)

        logger.setLevel(original_level)
        logger.propagate = original_propagate


def test_startup_initializes_database_and_logging(tmp_path):
    data_dir = tmp_path / "application-data"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GT_DATA_DIR='{data_dir.as_posix()}'\n"
        "GT_LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )

    context = bootstrap_module.bootstrap(str(env_file))

    assert context.paths.data_dir == data_dir.resolve()
    assert context.settings.log_level == "INFO"
    assert context.logger.level == logging.INFO
    assert context.paths.database.is_file()

    with open_database(context.paths.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 1

        assert connection.execute(
            "SELECT COUNT(*) FROM goals"
        ).fetchone()[0] == 0

    content = (context.paths.logs / "goal_tracker.log").read_text(
        encoding="utf-8"
    )
    assert "Database ready" in content
    assert "Application startup completed." in content


def test_environment_overrides_startup_configuration(
    tmp_path,
    monkeypatch,
):
    file_data_dir = tmp_path / "file-data"
    environment_data_dir = tmp_path / "environment-data"

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GT_DATA_DIR='{file_data_dir.as_posix()}'\n"
        "GT_LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("GT_DATA_DIR", str(environment_data_dir))
    monkeypatch.setenv("GT_LOG_LEVEL", "WARNING")

    context = bootstrap_module.bootstrap(str(env_file))

    assert context.paths.data_dir == environment_data_dir.resolve()
    assert context.logger.level == logging.WARNING
    assert context.paths.database.is_file()
    assert not file_data_dir.exists()


def test_invalid_settings_do_not_create_data_directory(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "application-data"
    monkeypatch.setenv("GT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GT_LOG_LEVEL", "INVALID")

    with pytest.raises(ValidationError):
        bootstrap_module.bootstrap()

    assert not data_dir.exists()


def test_relative_data_directory_is_rejected(monkeypatch):
    monkeypatch.setenv("GT_DATA_DIR", "relative-data")

    with pytest.raises(ValueError, match="must be absolute"):
        bootstrap_module.bootstrap()


def test_restarting_preserves_records(tmp_path, monkeypatch):
    data_dir = tmp_path / "application-data"
    monkeypatch.setenv("GT_DATA_DIR", str(data_dir))

    first_context = bootstrap_module.bootstrap()

    with open_database(first_context.paths.database) as connection:
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO periods (
                    id, kind, start_date, end_date, timezone
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "test-period",
                    "weekly",
                    "2026-11-09",
                    "2026-11-15",
                    "Asia/Kolkata",
                ),
            )

    second_context = bootstrap_module.bootstrap()

    with open_database(second_context.paths.database) as connection:
        assert connection.execute(
            "SELECT id FROM periods"
        ).fetchone()["id"] == "test-period"

        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 1

    assert not second_context.paths.backups.exists()


def test_database_failure_stops_startup(tmp_path, monkeypatch):
    data_dir = tmp_path / "application-data"
    monkeypatch.setenv("GT_DATA_DIR", str(data_dir))

    def fail_migration(*args, **kwargs):
        raise sqlite3.DatabaseError("Simulated database failure")

    monkeypatch.setattr(
        bootstrap_module,
        "migrate_database",
        fail_migration,
    )

    with pytest.raises(ValueError, match="Database initialization failed") as error:
        bootstrap_module.bootstrap()

    assert isinstance(error.value.__cause__, sqlite3.DatabaseError)

    content = (data_dir / "logs" / "goal_tracker.log").read_text(
        encoding="utf-8"
    )

    assert "Database initialization failed (DatabaseError)." in content
    assert "Application startup completed." not in content