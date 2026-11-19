"""Completed-period lookups shared by the terminal and web interfaces."""

_PAGE_SIZE = 50


def _completed_periods(service):
    offset = 0

    while True:
        periods = service.list_completed_periods(
            limit=_PAGE_SIZE,
            offset=offset,
        )
        yield from periods

        if len(periods) < _PAGE_SIZE:
            return

        offset += _PAGE_SIZE


def find_unreported_periods(service) -> list[dict]:
    """Return completed periods, newest first, that have no saved report."""
    return [
        period
        for period in _completed_periods(service)
        if not service.list_reports(period_id=period["id"], limit=1)
    ]


def find_completed_period(service, period_id: str) -> dict | None:
    """Return one completed period by ID, or None if it has not ended."""
    for period in _completed_periods(service):
        if period["id"] == str(period_id):
            return period

    return None
