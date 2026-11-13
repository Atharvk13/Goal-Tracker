from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.table import Table

from goal_tracker.analysis.service import AnalysisService
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError, DomainValidationError
from goal_tracker.domain.models import PeriodSpec


def _today_in_timezone(timezone: str) -> date:
    """Return today's date in the application's configured timezone."""
    return datetime.now(ZoneInfo(timezone)).date()


def _current_period_start(kind: PeriodKind, today: date) -> date:
    """Find the start date of the current week or month."""
    if kind == PeriodKind.WEEKLY:
        return today - timedelta(days=today.weekday())

    return today.replace(day=1)


def _ask_period_start(
    console: Console,
    kind: PeriodKind,
    default_start: date,
    timezone: str,
) -> PeriodSpec | None:
    """Ask for a period start date and validate it."""
    while True:
        value = console.input(
            f"Period start date [default {default_start.isoformat()}, "
            "or 0 to go back]: "
        ).strip()

        if value == "0":
            return None

        if not value:
            start_date = default_start
        else:
            try:
                start_date = date.fromisoformat(value)
            except ValueError:
                console.print("Enter a date in YYYY-MM-DD format.")
                continue

        try:
            return PeriodSpec(
                kind=kind,
                start_date=start_date,
                timezone=timezone,
            )
        except ValueError as error:
            console.print(str(error))


def _format_percentage(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _show_goal_table(console: Console, goals: list[dict]) -> None:
    if not goals:
        console.print("No goals were active in this period.")
        return

    table = Table(title="Goal Progress")
    table.add_column("Goal")
    table.add_column("Status")
    table.add_column("Original", justify="right")
    table.add_column("Revised", justify="right")
    table.add_column("Milestones", justify="right")

    for goal in goals:
        revised = _format_percentage(goal["revised_completion"])
        original = _format_percentage(goal["original_completion"])
        milestones = (
            f'{goal["revised_completed"]}/{goal["revised_total"]}'
            if goal["revised_scored"]
            else "N/A"
        )

        table.add_row(
            goal["title"],
            goal["completion_status"].replace("_", " ").title(),
            original,
            revised,
            milestones,
        )

    console.print(table)


def _show_items(console: Console, title: str, items: list[dict]) -> None:
    console.print(f"\n[bold]{title}[/bold]")

    if not items:
        console.print("  None recorded.")
        return

    for item in items:
        description = item.get("description", "No description")
        console.print(f"  • {description}")


def _show_trend(console: Console, trend: list[dict]) -> None:
    if not trend:
        return

    table = Table(title="Completion Trend")
    table.add_column("Date")
    table.add_column("Completion", justify="right")
    table.add_column("Goals", justify="right")
    table.add_column("Entries", justify="right")

    for day in trend:
        table.add_row(
            day["date"],
            _format_percentage(day["completion"]),
            str(day["scored_goals"]),
            str(day["entries"]),
        )

    console.print(table)


def show_progress_summary(context, console: Console) -> None:
    """Choose a period, calculate its facts, and display the summary."""
    console.print("\n[bold]Progress Summary[/bold]")
    console.print("1. Weekly")
    console.print("2. Monthly")
    console.print("0. Back")

    choice = console.input("Choose a period: ").strip()

    if choice == "0":
        return

    if choice == "1":
        kind = PeriodKind.WEEKLY
    elif choice == "2":
        kind = PeriodKind.MONTHLY
    else:
        console.print("Choose 1, 2, or 0.")
        return

    try:
        today = _today_in_timezone(context.settings.timezone)
        default_start = _current_period_start(kind, today)

        period = _ask_period_start(
            console,
            kind,
            default_start,
            context.settings.timezone,
        )

        if period is None:
            return

        service = AnalysisService(
            context.paths.database,
            timezone=context.settings.timezone,
        )
        summary = service.analyze_period(period)

    except (AppError, DomainValidationError, ValueError) as error:
        console.print(f"[red]Could not calculate the summary:[/red] {error}")
        return

    details = summary["period"]
    console.print(
        f"\n[bold]{details['kind'].title()} period:[/bold] "
        f"{details['start_date']} to {details['end_date']}"
    )
    console.print(f"Summary calculated through: {summary['as_of']}")

    if summary["not_started"]:
        console.print("This period has not started yet.")

    if summary["period_has_ended"]:
        console.print("This period has ended.")

    console.print(
        "\n"
        f"Original plan completion: "
        f"{_format_percentage(summary['original_completion'])}\n"
        f"Current plan completion: "
        f"{_format_percentage(summary['revised_completion'])}\n"
        f"Goals in original plan: {summary['original_goal_count']}\n"
        f"Goals in current plan: {summary['revised_goal_count']}\n"
        f"Cancelled goals: {summary['cancelled_goal_count']}"
    )

    consistency = _format_percentage(summary["logging_consistency"])
    console.print(
        f"Logging coverage: {consistency} "
        f"({summary['recorded_days']} recorded days out of "
        f"{summary['elapsed_days']} elapsed days)"
    )

    minutes = summary["recorded_minutes"]
    if minutes is None:
        console.print("Recorded time: no time amounts were provided.")
    else:
        console.print(f"Recorded time: {minutes} minutes")

    if summary["items_without_time"]:
        console.print(
            f"Activities without time recorded: {summary['items_without_time']}"
        )

    console.print()
    _show_goal_table(console, summary["goals"])

    console.print(
        f"\nPlanned milestones due by the cutoff: {summary['planned_due']}\n"
        f"Those completed by the cutoff: {summary['completed_due']}"
    )

    _show_items(
        console,
        "Completed activities",
        summary["completed_activities"],
    )
    _show_items(
        console,
        "Pending activities",
        summary["pending_activities"],
    )

    carried_over = sum(
        1 for item in summary["pending_activities"]
        if item.get("carried_over")
    )
    if carried_over:
        console.print(
            f"Note: {carried_over} pending item(s) started before this "
            "period and are shown as carryover."
        )

    _show_items(console, "Learnings", summary["learnings"])
    _show_items(console, "Challenges", summary["challenges"])
    _show_items(console, "Achievements", summary["achievements"])
    _show_trend(console, summary["completion_trend"])