"""Calendar helpers shared by the terminal and web interfaces."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from goal_tracker.domain.enums import PeriodKind


def today_in(timezone: str) -> date:
    """Return today's date in the given timezone."""
    return datetime.now(ZoneInfo(timezone)).date()


def current_period_start(kind: PeriodKind | str, today: date) -> date:
    """Return the first day of the week (Monday) or month containing today."""
    if PeriodKind(kind) == PeriodKind.WEEKLY:
        return today - timedelta(days=today.weekday())

    return today.replace(day=1)
