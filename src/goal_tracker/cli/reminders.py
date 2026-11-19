from rich.console import Console

from goal_tracker.bootstrap import AppContext
from goal_tracker.domain.errors import AppError
from goal_tracker.mcp.services import get_report_service
from goal_tracker.reporting.periods import find_unreported_periods


def show_report_reminders(
    context: AppContext,
    console: Console,
) -> None:
    """Mention completed periods that do not have a saved report."""
    try:
        service = get_report_service(context)
        missing = find_unreported_periods(service)

    except AppError as error:
        context.logger.warning(
            "Report reminder lookup failed (%s).",
            type(error).__name__,
        )
        console.print(
            "Report reminders are temporarily unavailable.",
            style="yellow",
        )
        return

    if not missing:
        return

    console.print(
        "\nCompleted periods without saved reports:",
        style="bold yellow",
    )

    for period in missing[:5]:
        console.print(
            f"- {period['kind'].title()}: "
            f"{period['start_date']} to {period['end_date']}",
            markup=False,
        )

    if len(missing) > 5:
        console.print(
            f"Plus {len(missing) - 5} older periods."
        )

    console.print(
        "Use option 6 for weekly reports or option 7 for monthly reports."
    )