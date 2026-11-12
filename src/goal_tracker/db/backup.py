import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


def backup_database(
    database_path: Path,
    backup_dir: Path,
) -> Path:
    """Create a uniquely named SQLite backup and return its location."""
    database_path = database_path.expanduser()
    backup_dir = backup_dir.expanduser()

    if not database_path.is_absolute() or not backup_dir.is_absolute():
        raise ValueError("Database and backup paths must be absolute.")

    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()

    if not database_path.is_file():
        raise FileNotFoundError("The source database does not exist.")

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / (
        f"tracker_{timestamp}_{uuid4().hex}.sqlite3"
    )

    with NamedTemporaryFile(
        dir=backup_dir,
        prefix=".backup-",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        source_uri = database_path.as_uri() + "?mode=ro"

        with closing(
            sqlite3.connect(source_uri, uri=True, timeout=5)
        ) as source:
            with closing(
                sqlite3.connect(str(temporary_path))
            ) as destination:
                source.backup(destination)

        temporary_path.rename(backup_path)

        return backup_path

    finally:
        temporary_path.unlink(missing_ok=True)