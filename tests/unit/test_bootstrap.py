import logging
import os

import pytest
from pydantic import ValidationError

from goal_tracker.bootstrap import bootstrap


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


def test_startup_uses_explicit_configuration(tmp_path):
    data_dir = tmp_path / "application-data"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GT_DATA_DIR='{data_dir.as_posix()}'\n"
        "GT_LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )

    context = bootstrap(str(env_file))

    assert context.paths.data_dir == data_dir.resolve()
    assert context.settings.log_level == "INFO"
    assert context.logger.level == logging.INFO
    assert not context.paths.database.exists()

    content = (context.paths.logs / "goal_tracker.log").read_text(
        encoding="utf-8"
    )
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

    context = bootstrap(str(env_file))

    assert context.paths.data_dir == environment_data_dir.resolve()
    assert context.logger.level == logging.WARNING
    assert not file_data_dir.exists()
    assert not context.paths.database.exists()


def test_invalid_settings_do_not_create_data_directory(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "application-data"
    monkeypatch.setenv("GT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GT_LOG_LEVEL", "INVALID")

    with pytest.raises(ValidationError):
        bootstrap()

    assert not data_dir.exists()


def test_relative_data_directory_is_rejected(monkeypatch):
    monkeypatch.setenv("GT_DATA_DIR", "relative-data")

    with pytest.raises(ValueError, match="must be absolute"):
        bootstrap()