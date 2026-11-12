import logging
import time
from logging.handlers import RotatingFileHandler

from goal_tracker.config.paths import AppPaths


class RequestIdFilter(logging.Filter):
    """Supply a placeholder when an event has no request ID."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"

        return True


def configure_logging(
    paths: AppPaths,
    level: str = "INFO",
) -> logging.Logger:
    """Configure the application's logger without changing the root logger."""
    supported_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    normalized_level = level.upper()

    if normalized_level not in supported_levels:
        raise ValueError(
            "Log level must be DEBUG, INFO, WARNING, ERROR or CRITICAL."
        )

    paths.logs.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        filename=paths.logs / "goal_tracker.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)sZ %(levelname)s %(name)s "
            "request_id=%(request_id)s %(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime

    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    logger = logging.getLogger("goal_tracker")
    logger.setLevel(supported_levels[normalized_level])
    logger.propagate = False

    for existing_handler in list(logger.handlers):
        logger.removeHandler(existing_handler)
        existing_handler.close()

    logger.addHandler(handler)

    return logger