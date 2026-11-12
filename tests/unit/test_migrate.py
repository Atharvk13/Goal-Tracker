import sqlite3

import pytest

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.migrate import migrate_database


@pytest.fixture
def migration_project(tmp_path):
    """Prepare a small migration project in temporary storage."""
    database_path = tmp_path / "tracker.sqlite3"
    backup_dir = tmp_path / "backups"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()

    (migrations_dir / "001_initial.sql").write_text(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        );
        """,
        encoding="utf-8",
    )

    return database_path, backup_dir, migrations_dir


def test_bundled_migration_creates_application_schema(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    backup_dir = tmp_path / "backups"

    result = migrate_database(database_path, backup_dir)

    assert result.applied_versions == (1,)
    assert result.backup_path is None
    assert not backup_dir.exists()

    expected_tables = {
        "schema_migrations",
        "periods",
        "mutation_requests",
        "goals",
        "goal_revisions",
        "milestones",
        "milestone_revisions",
        "entries",
        "activities",
        "activity_goal_links",
        "activity_events",
        "milestone_events",
        "audit_events",
        "reports",
    }

    with open_database(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

        assert expected_tables.issubset(
            {row["name"] for row in rows}
        )

        history = connection.execute(
            "SELECT version, filename FROM schema_migrations"
        ).fetchall()

        assert len(history) == 1
        assert history[0]["version"] == 1
        assert history[0]["filename"] == "001_initial.sql"


def test_repeated_migration_is_a_no_op(migration_project):
    database_path, backup_dir, migrations_dir = migration_project

    migrate_database(database_path, backup_dir, migrations_dir)
    result = migrate_database(database_path, backup_dir, migrations_dir)

    assert result.applied_versions == ()
    assert result.backup_path is None
    assert not backup_dir.exists()

    with open_database(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 1


def test_upgrade_preserves_data_and_backs_up_previous_schema(
    migration_project,
):
    database_path, backup_dir, migrations_dir = migration_project
    migrate_database(database_path, backup_dir, migrations_dir)

    with open_database(database_path) as connection:
        with transaction(connection):
            connection.execute(
                "INSERT INTO items (title) VALUES (?)",
                ("Keep this record",),
            )

    (migrations_dir / "002_add_archived.sql").write_text(
        "ALTER TABLE items ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;",
        encoding="utf-8",
    )

    result = migrate_database(database_path, backup_dir, migrations_dir)

    assert result.applied_versions == (2,)
    assert result.backup_path is not None
    assert result.backup_path.is_file()

    with open_database(database_path) as connection:
        row = connection.execute(
            "SELECT title, archived FROM items"
        ).fetchone()

        assert row["title"] == "Keep this record"
        assert row["archived"] == 0

    with open_database(result.backup_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(items)"
            ).fetchall()
        }

        assert "archived" not in columns
        assert connection.execute(
            "SELECT title FROM items"
        ).fetchone()["title"] == "Keep this record"


def test_failed_upgrade_rolls_back_all_pending_migrations(
    migration_project,
):
    database_path, backup_dir, migrations_dir = migration_project
    migrate_database(database_path, backup_dir, migrations_dir)

    (migrations_dir / "002_add_extra.sql").write_text(
        """
        CREATE TABLE extra (id INTEGER PRIMARY KEY);
        INSERT INTO items (title) VALUES ('Must roll back');
        """,
        encoding="utf-8",
    )
    (migrations_dir / "003_invalid.sql").write_text(
        "THIS IS NOT VALID SQL;",
        encoding="utf-8",
    )

    with pytest.raises(sqlite3.DatabaseError):
        migrate_database(database_path, backup_dir, migrations_dir)

    with open_database(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM items"
        ).fetchone()[0] == 0

        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'extra'"
        ).fetchone() is None

        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

        assert versions == [1]

    assert len(list(backup_dir.glob("*.sqlite3"))) == 1


def test_changed_applied_migration_is_rejected(migration_project):
    database_path, backup_dir, migrations_dir = migration_project
    migrate_database(database_path, backup_dir, migrations_dir)

    (migrations_dir / "001_initial.sql").write_text(
        "CREATE TABLE changed_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="has been changed"):
        migrate_database(database_path, backup_dir, migrations_dir)

    assert not backup_dir.exists()


def test_missing_migration_version_is_rejected(migration_project):
    database_path, backup_dir, migrations_dir = migration_project

    (migrations_dir / "003_skipped_version.sql").write_text(
        "CREATE TABLE extra (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique and consecutive"):
        migrate_database(database_path, backup_dir, migrations_dir)

    assert not database_path.exists()


def test_quoted_semicolon_does_not_split_a_statement(migration_project):
    database_path, backup_dir, migrations_dir = migration_project

    (migrations_dir / "001_initial.sql").write_text(
        """
        CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO items (title) VALUES ('learn; build');
        """,
        encoding="utf-8",
    )

    migrate_database(database_path, backup_dir, migrations_dir)

    with open_database(database_path) as connection:
        assert connection.execute(
            "SELECT title FROM items"
        ).fetchone()["title"] == "learn; build"


def test_script_cannot_commit_the_runners_transaction(migration_project):
    database_path, backup_dir, migrations_dir = migration_project

    (migrations_dir / "001_initial.sql").write_text(
        """
        CREATE TABLE items (id INTEGER PRIMARY KEY);
        COMMIT;
        """,
        encoding="utf-8",
    )

    with pytest.raises(sqlite3.DatabaseError):
        migrate_database(database_path, backup_dir, migrations_dir)

    with open_database(database_path) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        assert "items" not in tables
        assert "schema_migrations" not in tables


def test_incomplete_statement_rolls_back_initial_setup(migration_project):
    database_path, backup_dir, migrations_dir = migration_project

    (migrations_dir / "001_initial.sql").write_text(
        """
        CREATE TABLE first_table (id INTEGER PRIMARY KEY);
        CREATE TABLE incomplete_table (
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must end with a semicolon"):
        migrate_database(database_path, backup_dir, migrations_dir)

    with open_database(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'first_table'"
        ).fetchone() is None

        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'"
        ).fetchone() is None