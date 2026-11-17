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