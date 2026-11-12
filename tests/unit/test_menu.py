import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

from goal_tracker.cli.menu import run_menu


@pytest.fixture
def menu_environment():
    """Prepare a menu without real startup, logging or model calls."""
    output = io.StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        width=120,
    )

    context = SimpleNamespace(
        settings=SimpleNamespace(llm_model="test-model"),
        logger=Mock(),
    )

    diagnostics = Mock()

    return context, console, output, diagnostics


def test_exit_closes_menu(monkeypatch, menu_environment):
    context, console, output, diagnostics = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("0\n"))

    run_menu(context, diagnostics, console)

    assert "WELCOME TO GOAL TRACKER AGENT" in output.getvalue()
    assert "Configured model: test-model" in output.getvalue()
    assert "Goodbye." in output.getvalue()
    diagnostics.assert_not_called()


def test_diagnostics_runs_then_returns_to_menu(
    monkeypatch,
    menu_environment,
):
    context, console, output, diagnostics = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("9\n0\n"))

    run_menu(context, diagnostics, console)

    diagnostics.assert_called_once_with()
    assert output.getvalue().count("Main Menu") == 2
    assert "Goodbye." in output.getvalue()


def test_planned_feature_does_not_run_diagnostics(
    monkeypatch,
    menu_environment,
):
    context, console, output, diagnostics = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n0\n"))

    run_menu(context, diagnostics, console)

    assert "This feature is planned but not implemented yet." in output.getvalue()
    assert "Goodbye." in output.getvalue()
    diagnostics.assert_not_called()


def test_invalid_choice_prompts_again(monkeypatch, menu_environment):
    context, console, output, diagnostics = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("invalid\n0\n"))

    run_menu(context, diagnostics, console)

    assert output.getvalue().count("Choose an option") >= 2
    assert "Goodbye." in output.getvalue()
    diagnostics.assert_not_called()


def test_closed_input_exits_gracefully(monkeypatch, menu_environment):
    context, console, output, diagnostics = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    run_menu(context, diagnostics, console)

    assert "Goodbye." in output.getvalue()
    diagnostics.assert_not_called()


def test_keyboard_interrupt_exits_gracefully(
    monkeypatch,
    menu_environment,
):
    context, console, output, diagnostics = menu_environment

    def interrupted_input(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted_input)

    run_menu(context, diagnostics, console)

    assert "Goodbye." in output.getvalue()
    diagnostics.assert_not_called()