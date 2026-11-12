import logging
import sqlite3
from dataclasses import dataclass

from goal_tracker.config.paths import AppPaths, get_app_paths
from goal_tracker.config.settings import Settings, load_settings
from goal_tracker.db.migrate import migrate_database
from goal_tracker.logging_config import configure_logging


@dataclass(frozen=True)
class AppContext:
    """Shared components prepared during application startup."""

    settings: Settings
    paths: AppPaths
    logger: logging.Logger


def bootstrap(env_file: str | None = None) -> AppContext:
    """Prepare configuration, logging and the database without calling Ollama."""
    settings = load_settings(env_file)
    paths = get_app_paths(settings.data_dir)
    logger = configure_logging(paths, level=settings.log_level)

    try:
        result = migrate_database(
            database_path=paths.database,
            backup_dir=paths.backups,
        )

    except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
        logger.error(
            "Database initialization failed (%s).",
            type(error).__name__,
        )
        raise ValueError(
            "Database initialization failed. "
            "Check database access and migration files."
        ) from error

    logger.info(
        "Database ready; applied migration versions: %s.",
        result.applied_versions,
    )
    logger.info("Application startup completed.")

    return AppContext(
        settings=settings,
        paths=paths,
        logger=logger,
    )