from pathlib import Path

import pytest

import goal_tracker.config.paths as paths_module


def test_calculates_paths_without_creating_directories(tmp_path):
    data_dir = tmp_path / "application-data"

    paths = paths_module.get_app_paths(data_dir)

    assert paths.data_dir == data_dir.resolve()
    assert paths.database == data_dir.resolve() / "tracker.sqlite3"
    assert paths.logs == data_dir.resolve() / "logs"
    assert paths.backups == data_dir.resolve() / "backups"
    assert paths.reports == data_dir.resolve() / "reports"
    assert not data_dir.exists()


def test_uses_platform_default_location(monkeypatch, tmp_path):
    expected_dir = tmp_path / "GoalTracker"
    calls = []

    def fake_user_data_dir(**kwargs):
        calls.append(kwargs)
        return str(expected_dir)

    monkeypatch.setattr(
        paths_module,
        "user_data_dir",
        fake_user_data_dir,
    )

    paths = paths_module.get_app_paths()

    assert paths.data_dir == expected_dir.resolve()
    assert calls == [
        {
            "appname": "GoalTracker",
            "appauthor": False,
            "roaming": False,
        }
    ]
    assert not expected_dir.exists()


def test_rejects_relative_data_directory():
    with pytest.raises(ValueError, match="must be absolute"):
        paths_module.get_app_paths(Path("relative-data"))


def test_explicitly_creates_directories_without_database(tmp_path):
    paths = paths_module.get_app_paths(
        tmp_path / "nested" / "application-data"
    )

    paths.create_directories()

    assert paths.data_dir.is_dir()
    assert paths.logs.is_dir()
    assert paths.backups.is_dir()
    assert paths.reports.is_dir()
    assert not paths.database.exists()


def test_directory_creation_preserves_existing_files(tmp_path):
    paths = paths_module.get_app_paths(tmp_path / "application-data")
    paths.create_directories()

    existing_file = paths.reports / "existing.txt"
    existing_file.write_text("Keep this content.", encoding="utf-8")

    paths.create_directories()

    assert existing_file.read_text(encoding="utf-8") == "Keep this content."