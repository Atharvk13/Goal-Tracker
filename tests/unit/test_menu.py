import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

import goal_tracker.cli.menu as menu_module
from goal_tracker.domain.enums import PeriodKind


@pytest.fixture
def menu_environment(monkeypatch):
    """Test routing without opening real application workflows."""
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

    environment = SimpleNamespace(
        context=context,
        console=console,
        output=output,
        diagnostics=Mock(),
        creation=Mock(),
        management=Mock(),
    )

    monkeypatch.setattr(
        menu_module,
        "create_goals",
        environment.creation,
    )
    monkeypatch.setattr(
        menu_module,
        "manage_goals",
        environment.management,
    )

    return environment


def test_exit_closes_menu(monkeypatch, menu_environment):
    env = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("0\n"))

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    assert "WELCOME TO GOAL TRACKER AGENT" in env.output.getvalue()
    assert "Configured model: test-model" in env.output.getvalue()
    assert "Goodbye." in env.output.getvalue()
    env.diagnostics.assert_not_called()
    env.creation.assert_not_called()
    env.management.assert_not_called()


@pytest.mark.parametrize(
    ("choice", "expected_kind"),
    [
        ("1", PeriodKind.WEEKLY),
        ("2", PeriodKind.MONTHLY),
    ],
)
def test_creation_options_route_to_the_correct_period(
    monkeypatch,
    menu_environment,
    choice,
    expected_kind,
):
    env = menu_environment
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(f"{choice}\n0\n"),
    )

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    env.creation.assert_called_once_with(
        env.context,
        expected_kind,
        env.console,
    )
    env.diagnostics.assert_not_called()
    env.management.assert_not_called()
    assert env.output.getvalue().count("Main Menu") == 2


def test_management_option_opens_goal_browser(
    monkeypatch,
    menu_environment,
):
    env = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("4\n0\n"))

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    env.management.assert_called_once_with(env.context, env.console)
    env.creation.assert_not_called()
    env.diagnostics.assert_not_called()
    assert env.output.getvalue().count("Main Menu") == 2
    assert "Goodbye." in env.output.getvalue()


def test_diagnostics_runs_then_returns_to_menu(
    monkeypatch,
    menu_environment,
):
    env = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("9\n0\n"))

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    env.diagnostics.assert_called_once_with()
    env.creation.assert_not_called()
    env.management.assert_not_called()
    assert env.output.getvalue().count("Main Menu") == 2


def test_weekly_report_option_runs_report_workflow(
    monkeypatch,
    menu_environment,
):
    env = menu_environment
    calls = []

    monkeypatch.setattr(
        menu_module,
        "run_period_reports",
        lambda context, kind, console: calls.append(
            (context, kind, console)
        ),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("6\n0\n"))

    menu_module.run_menu(
        env.context,
        env.diagnostics,
        env.console,
    )

    assert calls == [
        (
            env.context,
            menu_module.PeriodKind.WEEKLY,
            env.console,
        )
    ]


def test_invalid_choice_prompts_again(monkeypatch, menu_environment):
    env = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("invalid\n0\n"))

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    assert env.output.getvalue().count("Choose an option") >= 2
    assert "Goodbye." in env.output.getvalue()
    env.diagnostics.assert_not_called()
    env.creation.assert_not_called()
    env.management.assert_not_called()


def test_closed_input_exits_gracefully(monkeypatch, menu_environment):
    env = menu_environment
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    assert "Goodbye." in env.output.getvalue()
    env.diagnostics.assert_not_called()
    env.creation.assert_not_called()
    env.management.assert_not_called()


def test_keyboard_interrupt_exits_gracefully(
    monkeypatch,
    menu_environment,
):
    env = menu_environment

    def interrupted_input(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted_input)

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    assert "Goodbye." in env.output.getvalue()
    env.diagnostics.assert_not_called()
    env.creation.assert_not_called()
    env.management.assert_not_called()

def test_progress_summary_option_runs_summary_workflow(
    monkeypatch,
    menu_environment,
):
    env = menu_environment
    calls = []

    monkeypatch.setattr(
        menu_module,
        "show_progress_summary",
        lambda context, console: calls.append((context, console)),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("5\n0\n"))

    menu_module.run_menu(env.context, env.diagnostics, env.console)

    assert calls == [(env.context, env.console)]