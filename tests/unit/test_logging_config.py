import logging

import pytest

from goal_tracker.config.paths import get_app_paths
from goal_tracker.logging_config import configure_logging


@pytest.fixture(autouse=True)
def isolate_application_logger():
    """Restore the application logger after each test."""
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


def test_writes_events_with_request_ids(tmp_path):
    paths = get_app_paths(tmp_path / "application-data")
    logger = configure_logging(paths)

    logger.info("Application started.")
    logger.info(
        "Operation finished.",
        extra={"request_id": "request-123"},
    )

    content = (paths.logs / "goal_tracker.log").read_text(
        encoding="utf-8"
    )

    assert "request_id=- Application started." in content
    assert "request_id=request-123 Operation finished." in content
    assert "Z INFO goal_tracker" in content
    assert not paths.database.exists()


def test_rejects_invalid_level_before_creating_directories(tmp_path):
    paths = get_app_paths(tmp_path / "application-data")

    with pytest.raises(ValueError, match="Log level must be"):
        configure_logging(paths, level="INVALID")

    assert not paths.data_dir.exists()


def test_repeated_setup_does_not_duplicate_messages(tmp_path):
    paths = get_app_paths(tmp_path / "application-data")

    configure_logging(paths)
    logger = configure_logging(paths)
    logger.info("Setup complete.")

    content = (paths.logs / "goal_tracker.log").read_text(
        encoding="utf-8"
    )

    assert content.count("Setup complete.") == 1


def test_respects_configured_level(tmp_path):
    paths = get_app_paths(tmp_path / "application-data")
    logger = configure_logging(paths, level="warning")

    logger.info("Informational event.")
    logger.warning("Warning event.")

    content = (paths.logs / "goal_tracker.log").read_text(
        encoding="utf-8"
    )

    assert "Informational event." not in content
    assert "Warning event." in content


def test_does_not_change_root_logger(tmp_path):
    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = list(root_logger.handlers)

    paths = get_app_paths(tmp_path / "application-data")
    logger = configure_logging(paths)

    assert root_logger.level == original_level
    assert root_logger.handlers == original_handlers
    assert logger.propagate is False