import hashlib
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from goal_tracker.db.backup import backup_database
from goal_tracker.db.connection import open_database, transaction


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    checksum: str
    sql: str


@dataclass(frozen=True)
class MigrationResult:
    applied_versions: tuple[int, ...]
    backup_path: Path | None


def _load_migrations(directory: Path) -> list[Migration]:
    """Load numbered SQL files in version order."""
    migrations = []

    for path in directory.iterdir():
        if not path.is_file() or path.suffix != ".sql":
            continue

        match = re.fullmatch(r"(\d{3})_[a-z0-9_]+\.sql", path.name)

        if match is None:
            raise ValueError(f"Invalid migration filename: {path.name}")

        content = path.read_bytes()

        migrations.append(
            Migration(
                version=int(match.group(1)),
                filename=path.name,
                checksum=hashlib.sha256(content).hexdigest(),
                sql=content.decode("utf-8-sig"),
            )
        )

    migrations.sort(key=lambda migration: migration.version)

    versions = [migration.version for migration in migrations]

    if not versions or versions != list(range(1, len(versions) + 1)):
        raise ValueError(
            "Migration versions must be unique and consecutive, starting at 001."
        )

    return migrations


def _sql_statements(script: str) -> Iterator[str]:
    """Split complete statements without splitting quoted semicolons."""
    buffer = []

    for character in script:
        buffer.append(character)

        if character == ";":
            statement = "".join(buffer)

            if sqlite3.complete_statement(statement):
                yield statement
                buffer.clear()

    remainder = "".join(buffer).strip()

    if remainder:
        comment_only = all(
            not line.strip() or line.lstrip().startswith("--")
            for line in remainder.splitlines()
        )

        if not comment_only:
            raise ValueError(
                "Every migration statement must end with a semicolon."
            )


def _read_history(
    connection: sqlite3.Connection,
) -> dict[int, tuple[str, str]]:
    """Read applied versions without creating the history table."""
    exists = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone()

    if exists is None:
        return {}

    rows = connection.execute(
        "SELECT version, filename, checksum FROM schema_migrations"
    ).fetchall()

    return {
        row["version"]: (row["filename"], row["checksum"])
        for row in rows
    }


def _authorize_migration(
    action: int,
    argument_one: str | None,
    argument_two: str | None,
    database_name: str | None,
    trigger_name: str | None,
) -> int:
    """Prevent migration scripts from controlling our transaction."""
    if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
        return sqlite3.SQLITE_DENY

    return sqlite3.SQLITE_OK


def migrate_database(
    database_path: Path,
    backup_dir: Path,
    migrations_dir: Path | None = None,
) -> MigrationResult:
    """Apply pending migrations atomically, backing up existing databases."""
    database_path = database_path.expanduser()
    backup_dir = backup_dir.expanduser()

    if not database_path.is_absolute() or not backup_dir.is_absolute():
        raise ValueError("Database and backup paths must be absolute.")

    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()

    if migrations_dir is None:
        migrations_dir = Path(__file__).resolve().parent / "migrations"

    migrations = _load_migrations(migrations_dir)
    available = {
        migration.version: migration
        for migration in migrations
    }

    had_existing_data = (
        database_path.is_file() and database_path.stat().st_size > 0
    )

    with open_database(database_path) as connection:
        applied = _read_history(connection)

        if sorted(applied) != list(range(1, len(applied) + 1)):
            raise ValueError("Database migration history has missing versions.")

        for version, history in applied.items():
            migration = available.get(version)

            if migration is None:
                raise ValueError(
                    "The database requires a newer application version."
                )

            if history != (migration.filename, migration.checksum):
                raise ValueError(
                    f"Applied migration {version:03d} has been changed. "
                    "Restore the original file and create a new migration."
                )

        pending = [
            migration
            for migration in migrations
            if migration.version not in applied
        ]

        if not pending:
            return MigrationResult(
                applied_versions=(),
                backup_path=None,
            )

        backup_path = None

        if had_existing_data:
            backup_path = backup_database(database_path, backup_dir)

        with transaction(connection):
            if _read_history(connection) != applied:
                raise RuntimeError(
                    "Migration history changed during startup. Retry migration."
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                )
                """
            )

            connection.set_authorizer(_authorize_migration)

            try:
                for migration in pending:
                    for statement in _sql_statements(migration.sql):
                        connection.execute(statement)

                    connection.execute(
                        """
                        INSERT INTO schema_migrations (
                            version, filename, checksum
                        )
                        VALUES (?, ?, ?)
                        """,
                        (
                            migration.version,
                            migration.filename,
                            migration.checksum,
                        ),
                    )
            finally:
                connection.set_authorizer(None)

        return MigrationResult(
            applied_versions=tuple(
                migration.version for migration in pending
            ),
            backup_path=backup_path,
        )