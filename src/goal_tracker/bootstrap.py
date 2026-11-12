import logging
from dataclasses import dataclass

from goal_tracker.config.paths import AppPaths, get_app_paths
from goal_tracker.config.settings import Settings, load_settings
from goal_tracker.logging_config import configure_logging


@dataclass(frozen=True)
class AppContext:
    """Shared components prepared during application startup."""

    settings: Settings
    paths: AppPaths
    logger: logging.Logger


def bootstrap(env_file: str | None = None) -> AppContext:
    """Prepare the application without contacting Ollama."""
    settings = load_settings(env_file)
    paths = get_app_paths(settings.data_dir)
    logger = configure_logging(paths, level=settings.log_level)

    logger.info("Application startup completed.")

    return AppContext(
        settings=settings,
        paths=paths,
        logger=logger,
    )