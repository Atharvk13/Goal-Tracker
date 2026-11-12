import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def open_database(
    database_path: Path,
) -> Iterator[sqlite3.Connection]:
    """Open a configured SQLite connection and close it afterward."""
    database_path = database_path.expanduser()

    if not database_path.is_absolute():
        raise ValueError("The database path must be absolute.")

    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(database_path),
        timeout=5,
        isolation_level=None,
    )

    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

        yield connection

    finally:
        connection.close()


@contextmanager
def transaction(
    connection: sqlite3.Connection,
    *,
    write: bool = True,
) -> Iterator[sqlite3.Connection]:
    """Commit a group of operations, or roll them back on failure."""
    if connection.in_transaction:
        raise ValueError("Nested transactions are not supported.")

    connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")

    try:
        yield connection

    except BaseException:
        connection.rollback()
        raise

    else:
        connection.commit()