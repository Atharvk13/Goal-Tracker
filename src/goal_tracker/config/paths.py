from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir


@dataclass(frozen=True)
class AppPaths:
    """Locations for the application's persistent data."""

    data_dir: Path

    @property
    def database(self) -> Path:
        return self.data_dir / "tracker.sqlite3"

    @property
    def logs(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups(self) -> Path:
        return self.data_dir / "backups"

    @property
    def reports(self) -> Path:
        return self.data_dir / "reports"

    def create_directories(self) -> None:
        """Create directories only when explicitly requested."""
        for directory in (
            self.data_dir,
            self.logs,
            self.backups,
            self.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def get_app_paths(data_dir: Path | None = None) -> AppPaths:
    """Resolve application paths without creating files or directories."""
    if data_dir is None:
        data_dir = Path(
            user_data_dir(
                appname="GoalTracker",
                appauthor=False,
                roaming=False,
            )
        )

    data_dir = data_dir.expanduser()

    if not data_dir.is_absolute():
        raise ValueError("The application data directory must be absolute.")

    return AppPaths(data_dir=data_dir.resolve())
