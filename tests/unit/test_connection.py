import sqlite3
from pathlib import Path

import pytest

from goal_tracker.db.connection import open_database, transaction


def test_committed_data_survives_reopening(tmp_path):
    database_path = tmp_path / "data" / "tracker.sqlite3"

    with open_database(database_path) as connection:
        with transaction(connection):
            connection.execute(
                "CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)"
            )
            connection.execute(
                "INSERT INTO items (title) VALUES (?)",
                ("First milestone",),
            )

    with open_database(database_path) as connection:
        row = connection.execute(
            "SELECT id, title FROM items"
        ).fetchone()

        assert row["id"] == 1
        assert row["title"] == "First milestone"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Simulated failure"),
        KeyboardInterrupt(),
    ],
)
def test_failure_rolls_back_all_transaction_writes(tmp_path, error):
    database_path = tmp_path / "tracker.sqlite3"

    with open_database(database_path) as connection:
        with transaction(connection):
            connection.execute(
                "CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)"
            )

        with pytest.raises(type(error)):
            with transaction(connection):
                connection.execute(
                    "INSERT INTO items (title) VALUES (?)",
                    ("First item",),
                )
                connection.execute(
                    "INSERT INTO items (title) VALUES (?)",
                    ("Second item",),
                )
                raise error

        row = connection.execute(
            "SELECT COUNT(*) AS total FROM items"
        ).fetchone()

        assert row["total"] == 0
        assert connection.in_transaction is False


def test_foreign_keys_prevent_orphan_records(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"

    with open_database(database_path) as connection:
        with transaction(connection):
            connection.execute(
                "CREATE TABLE parents (id INTEGER PRIMARY KEY)"
            )
            connection.execute(
                """
                CREATE TABLE children (
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES parents(id)
                )
                """
            )

        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute(
                    "INSERT INTO children (parent_id) VALUES (?)",
                    (999,),
                )

        row = connection.execute(
            "SELECT COUNT(*) AS total FROM children"
        ).fetchone()

        assert row["total"] == 0

    with open_database(database_path) as connection:
        assert connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0] == 1


def test_connection_is_closed_after_context_exits(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"

    with open_database(database_path) as connection:
        connection.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_nested_transaction_is_rejected(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"

    with open_database(database_path) as connection:
        with transaction(connection):
            with pytest.raises(ValueError, match="Nested transactions"):
                with transaction(connection):
                    pytest.fail("Nested transaction should not start.")

            assert connection.in_transaction is True

        assert connection.in_transaction is False


def test_relative_database_path_is_rejected():
    with pytest.raises(ValueError, match="must be absolute"):
        with open_database(Path("relative.sqlite3")):
            pytest.fail("A relative database path should be rejected.")