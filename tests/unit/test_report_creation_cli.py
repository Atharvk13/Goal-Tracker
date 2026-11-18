import io
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from rich.console import Console

import goal_tracker.cli.reports as reports_module
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.models import PeriodSpec

import goal_tracker.cli.reminders as reminders_module
from goal_tracker.domain.errors import StorageError

@pytest.fixture
def report_environment(monkeypatch):
    context = SimpleNamespace()
    console = Console(
        file=io.StringIO(),
        force_terminal=False,
    )
    period = PeriodSpec(
        kind="weekly",
        start_date=date(2026, 11, 9),
    )
    period_id = str(uuid4())
    report = {"id": str(uuid4()), "revision": 1}

    service = Mock()
    service.list_reports.return_value = []

    generate = Mock(return_value=report)
    display = Mock()
    export = Mock()

    monkeypatch.setattr(
        reports_module,
        "get_report_service",
        Mock(return_value=service),
    )
    monkeypatch.setattr(
        reports_module,
        "_select_completed_period",
        Mock(return_value=(period_id, period)),
    )
    monkeypatch.setattr(
        reports_module,
        "_generate_report",
        generate,
    )
    monkeypatch.setattr(
        reports_module,
        "show_report",
        display,
    )
    monkeypatch.setattr(
        reports_module,
        "_offer_pdf_export",
        export,
    )

    return SimpleNamespace(
        context=context,
        console=console,
        period=period,
        report=report,
        service=service,
        generate=generate,
        display=display,
        export=export,
    )


def test_first_report_is_generated_before_display(
    monkeypatch,
    report_environment,
):
    env = report_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    reports_module.run_period_reports(
        env.context,
        PeriodKind.WEEKLY,
        env.console,
    )

    env.generate.assert_called_once_with(
        env.context,
        env.service,
        env.period,
        env.console,
    )
    env.display.assert_called_once_with(
        env.report,
        env.console,
    )
    env.export.assert_called_once_with(
        env.context,
        env.service,
        env.report,
        env.console,
    )


def test_declining_generation_does_not_display_a_report(
    monkeypatch,
    report_environment,
):
    env = report_environment
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    reports_module.run_period_reports(
        env.context,
        PeriodKind.WEEKLY,
        env.console,
    )

    env.generate.assert_not_called()
    env.display.assert_not_called()
    env.export.assert_not_called()


def test_cancelled_generation_does_not_display_a_report(
    monkeypatch,
    report_environment,
):
    env = report_environment
    env.generate.return_value = None
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    reports_module.run_period_reports(
        env.context,
        PeriodKind.WEEKLY,
        env.console,
    )

    env.generate.assert_called_once()
    env.display.assert_not_called()
    env.export.assert_not_called()

@pytest.mark.parametrize("has_report", [False, True])
def test_reminder_only_appears_for_missing_reports(
    monkeypatch,
    has_report,
):
    output = io.StringIO()
    console = Console(file=output, force_terminal=False)
    context = SimpleNamespace(logger=Mock())

    service = Mock()
    service.list_completed_periods.return_value = [
        {
            "id": "period-1",
            "kind": "weekly",
            "start_date": "2026-11-09",
            "end_date": "2026-11-15",
        }
    ]
    service.list_reports.return_value = (
        [{"id": "report-1"}] if has_report else []
    )

    monkeypatch.setattr(
        reminders_module,
        "get_report_service",
        Mock(return_value=service),
    )

    reminders_module.show_report_reminders(context, console)

    if has_report:
        assert output.getvalue() == ""
    else:
        assert "Completed periods without saved reports" in output.getvalue()
        assert "2026-11-09" in output.getvalue()

    service.generate_report.assert_not_called()


def test_reminder_lookup_failure_does_not_stop_startup(
    monkeypatch,
):
    output = io.StringIO()
    console = Console(file=output, force_terminal=False)
    context = SimpleNamespace(logger=Mock())

    service = Mock()
    service.list_completed_periods.side_effect = StorageError(
        "Simulated lookup failure."
    )

    monkeypatch.setattr(
        reminders_module,
        "get_report_service",
        Mock(return_value=service),
    )

    reminders_module.show_report_reminders(context, console)

    assert "reminders are temporarily unavailable" in output.getvalue()
    context.logger.warning.assert_called_once()