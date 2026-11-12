from pathlib import Path

import pytest

from goal_tracker.db.backup import backup_database
from goal_tracker.db.connection import open_database, transaction


@pytest.fixture
def database(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"

    with open_database(database_path) as connection:
        with transaction(connection):
            connection.execute(
                "CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)"
            )
            connection.execute(
                "INSERT INTO items (title) VALUES (?)",
                ("Original record",),
            )

    return database_path


def test_backup_preserves_data_while_source_is_open(database, tmp_path):
    backup_dir = tmp_path / "backups"

    with open_database(database) as source:
        backup_path = backup_database(database, backup_dir)

        assert source.execute(
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0] == 1

    with open_database(backup_path) as connection:
        row = connection.execute(
            "SELECT title FROM items"
        ).fetchone()

        assert row["title"] == "Original record"

    assert backup_path.parent == backup_dir.resolve()
    assert backup_path.suffix == ".sqlite3"
    assert list(backup_dir.glob("*.tmp")) == []


def test_repeated_backups_preserve_separate_snapshots(database, tmp_path):
    backup_dir = tmp_path / "backups"
    first_backup = backup_database(database, backup_dir)

    with open_database(database) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO items (title) VALUES (?)",
                ("Later record",),
            )

    second_backup = backup_database(database, backup_dir)

    assert first_backup != second_backup

    with open_database(first_backup) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0] == 1

    with open_database(second_backup) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0] == 2


def test_missing_source_is_not_created(tmp_path):
    missing_database = tmp_path / "missing.sqlite3"
    backup_dir = tmp_path / "backups"

    with pytest.raises(FileNotFoundError):
        backup_database(missing_database, backup_dir)

    assert not missing_database.exists()
    assert not backup_dir.exists()


@pytest.mark.parametrize(
    "relative_argument",
    ["database", "backup_dir"],
)
def test_relative_paths_are_rejected(database, tmp_path, relative_argument):
    database_path = database
    backup_dir = tmp_path / "backups"

    if relative_argument == "database":
        database_path = Path("relative.sqlite3")
    else:
        backup_dir = Path("relative-backups")

    with pytest.raises(ValueError, match="must be absolute"):
        backup_database(database_path, backup_dir)


def test_failed_publication_removes_temporary_file(
    database,
    tmp_path,
    monkeypatch,
):
    backup_dir = tmp_path / "backups"
    original_rename = Path.rename

    def fail_backup_rename(path, target):
        if path.parent == backup_dir:
            raise OSError("Simulated rename failure")

        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_backup_rename)

    with pytest.raises(OSError, match="Simulated rename failure"):
        backup_database(database, backup_dir)

    assert list(backup_dir.iterdir()) == []

    with open_database(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0] == 1