from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import typer
from typer.testing import CliRunner

import goal_tracker.cli.app as cli_module


runner = CliRunner()


@pytest.fixture
def fake_startup(monkeypatch):
    """Keep startup command tests away from real files and services."""
    context = SimpleNamespace(
        settings=SimpleNamespace(llm_model="test-model"),
        logger=Mock(),
    )

    bootstrap_mock = Mock(return_value=context)
    doctor_mock = Mock()

    monkeypatch.setattr(cli_module, "bootstrap", bootstrap_mock)
    monkeypatch.setattr(cli_module, "doctor", doctor_mock)

    return context, bootstrap_mock, doctor_mock


def test_no_command_opens_menu(fake_startup):
    context, bootstrap_mock, doctor_mock = fake_startup

    result = runner.invoke(cli_module.app, [], input="0\n")

    assert result.exit_code == 0
    assert "WELCOME TO GOAL TRACKER AGENT" in result.output
    assert "Goodbye." in result.output
    bootstrap_mock.assert_called_once_with(None)
    doctor_mock.assert_not_called()


def test_start_passes_explicit_configuration(fake_startup, tmp_path):
    context, bootstrap_mock, doctor_mock = fake_startup
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    result = runner.invoke(
        cli_module.app,
        ["start", "--env-file", str(env_file)],
        input="0\n",
    )

    assert result.exit_code == 0
    assert "Goodbye." in result.output
    bootstrap_mock.assert_called_once_with(str(env_file))
    doctor_mock.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Invalid data directory"),
        OSError("Cannot open log file"),
    ],
)
def test_startup_failure_does_not_open_menu(fake_startup, error):
    context, bootstrap_mock, doctor_mock = fake_startup
    bootstrap_mock.side_effect = error

    result = runner.invoke(cli_module.app, ["start"])

    assert result.exit_code == 1
    assert "Startup failed" in result.output
    assert "WELCOME TO GOAL TRACKER AGENT" not in result.output
    doctor_mock.assert_not_called()


def test_failed_diagnostics_returns_to_menu(fake_startup):
    context, bootstrap_mock, doctor_mock = fake_startup
    doctor_mock.side_effect = typer.Exit(code=1)

    result = runner.invoke(
        cli_module.app,
        ["start"],
        input="9\n0\n",
    )

    assert result.exit_code == 0
    assert result.output.count("Main Menu") == 2
    assert "Goodbye." in result.output
    doctor_mock.assert_called_once_with(env_file=None, llm=False)
    context.logger.warning.assert_called_once_with(
        "Menu diagnostics reported a failure."
    )


def test_missing_configuration_file_stops_before_startup(
    fake_startup,
    tmp_path,
):
    context, bootstrap_mock, doctor_mock = fake_startup
    missing_file = tmp_path / "missing.env"

    result = runner.invoke(
        cli_module.app,
        ["start", "--env-file", str(missing_file)],
    )

    assert result.exit_code == 2
    bootstrap_mock.assert_not_called()
    doctor_mock.assert_not_called()