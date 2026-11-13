from uuid import uuid4

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from goal_tracker.bootstrap import AppContext
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError, StorageError
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.reporting.service import ReportService
from goal_tracker.reporting.terminal import (
    show_report,
    show_report_list,
)


PAGE_SIZE = 20


def _show_error(console: Console, error: AppError) -> None:
    console.print(
        Text(f"{error.code}: {error.message}", style="red")
    )


def _select_completed_period(
    service: ReportService,
    kind: PeriodKind,
    console: Console,
) -> tuple[str, PeriodSpec] | None:
    """Select a stored period that has already ended."""
    offset = 0

    while True:
        periods = service.list_completed_periods(
            kind=kind,
            limit=PAGE_SIZE,
            offset=offset,
        )

        if not periods:
            console.print(
                "No completed periods found on this page."
            )

            if offset == 0:
                console.print(
                    "Reports become available after a period ends."
                )
                return None

        table = Table(
            title=f"Completed {kind.value.title()} Periods"
        )
        table.add_column("Number", justify="right")
        table.add_column("Start")
        table.add_column("End")

        for number, period in enumerate(periods, start=1):
            table.add_row(
                str(number),
                period["start_date"],
                period["end_date"],
            )

        console.print(table)
        console.print(
            "Enter a number, n for next page, "
            "p for previous page, or 0 to go back."
        )

        choice = Prompt.ask(
            "Select period",
            console=console,
        ).strip().lower()

        if choice == "0":
            return None

        if choice == "n":
            if len(periods) == PAGE_SIZE:
                offset += PAGE_SIZE
            else:
                console.print("There are no more periods.")
            continue

        if choice == "p":
            offset = max(0, offset - PAGE_SIZE)
            continue

        if not choice.isdigit():
            console.print("Enter a valid option.")
            continue

        index = int(choice) - 1

        if not 0 <= index < len(periods):
            console.print("Choose a number from this page.")
            continue

        selected = periods[index]

        return (
            selected["id"],
            PeriodSpec(
                kind=selected["kind"],
                start_date=selected["start_date"],
                timezone=selected["timezone"],
            ),
        )


def _generate_report(
    service: ReportService,
    period: PeriodSpec,
    console: Console,
) -> dict | None:
    """Keep the same request ID if a storage retry is needed."""
    request_id = uuid4()

    while True:
        try:
            with console.status("Generating the factual report..."):
                result = service.generate_report(
                    period,
                    request_id=request_id,
                )

            return result["report"]

        except StorageError as error:
            _show_error(console, error)

            if not Confirm.ask(
                "Retry this report request?",
                default=False,
                console=console,
            ):
                return None

def _offer_pdf_export(
    context: AppContext,
    service: ReportService,
    report: dict,
    console: Console,
) -> None:
    """Offer PDF export after displaying a saved report."""
    if not Confirm.ask(
        "Export this report as PDF?",
        default=False,
        console=console,
    ):
        return

    with console.status("Exporting PDF..."):
        path = service.export_pdf(
            report["id"],
            context.paths.reports,
        )

    console.print("PDF available at:", style="green")
    console.print(Text(str(path)))

def run_period_reports(
    context: AppContext,
    kind: PeriodKind,
    console: Console,
) -> None:
    """View or generate reports for a completed week or month."""
    service = ReportService(
        context.paths.database,
        timezone=context.settings.timezone,
    )

    try:
        selected = _select_completed_period(
            service,
            kind,
            console,
        )

        if selected is None:
            return

        period_id, period = selected

        existing = service.list_reports(
            period_id=period_id,
            limit=1,
        )

        if existing:
            latest = existing[0]
            console.print(
                f"Latest saved report: revision {latest['revision']}."
            )

            action = Prompt.ask(
                "Choose view, generate a new revision, or back",
                choices=["view", "generate", "back"],
                default="view",
                console=console,
            )

            if action == "back":
                return

            if action == "view":
                report = service.get_report(latest["id"])
                show_report(report, console)
                _offer_pdf_export(
                    context, service, report, console
                )
                return
        if report is not None:
            console.print("Report saved.", style="green")
            show_report(report, console)
            _offer_pdf_export(
                context, service, report, console
            )

        if not Confirm.ask(
            "Generate and save a new report revision?",
            default=False,
            console=console,
        ):
            return

        report = _generate_report(service, period, console)

        if report is not None:
            console.print("Report saved.", style="green")
            show_report(report, console)

    except AppError as error:
        _show_error(console, error)


def browse_reports(
    context: AppContext,
    console: Console,
) -> None:
    """Browse saved report revisions without recalculating them."""
    service = ReportService(
        context.paths.database,
        timezone=context.settings.timezone,
    )
    offset = 0

    try:
        while True:
            reports = service.list_reports(
                limit=PAGE_SIZE,
                offset=offset,
            )

            show_report_list(reports, console)

            if not reports and offset == 0:
                return

            console.print(
                "Enter a report number, n for next page, "
                "p for previous page, or 0 to go back."
            )

            choice = Prompt.ask(
                "Select report",
                console=console,
            ).strip().lower()

            if choice == "0":
                return

            if choice == "n":
                if len(reports) == PAGE_SIZE:
                    offset += PAGE_SIZE
                else:
                    console.print("There are no more reports.")
                continue

            if choice == "p":
                offset = max(0, offset - PAGE_SIZE)
                continue

            if not choice.isdigit():
                console.print("Enter a valid option.")
                continue

            index = int(choice) - 1

            if not 0 <= index < len(reports):
                console.print("Choose a number from this page.")
                continue

            report = service.get_report(reports[index]["id"])
            show_report(report, console)
            _offer_pdf_export(context, service, report, console
                        )

    except AppError as error:
        _show_error(console, error)