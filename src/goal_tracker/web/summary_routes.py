"""Progress summary for the current or a chosen week or month."""

from datetime import date

from fastapi import APIRouter, Request
from pydantic import ValidationError

from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.domain.periods import current_period_start
from goal_tracker.web.dependencies import (
    get_analysis_service,
    get_context,
    templates,
    today,
    validation_messages,
)


router = APIRouter()


@router.get("/summary", include_in_schema=False)
def progress_summary(
    request: Request,
    kind: str = PeriodKind.WEEKLY.value,
    start: str | None = None,
):
    errors: list[str] = []
    summary = None

    try:
        period_kind = PeriodKind(kind)
    except ValueError:
        period_kind = PeriodKind.WEEKLY
        errors.append("Choose a weekly or monthly period.")

    start_value = start or current_period_start(period_kind, today(request)).isoformat()

    if not errors:
        try:
            period = PeriodSpec(
                kind=period_kind,
                start_date=date.fromisoformat(start_value),
                timezone=get_context(request).settings.timezone,
            )
            summary = get_analysis_service(request).analyze_period(period)
        except ValidationError as error:
            errors.extend(validation_messages(error))
        except ValueError:
            errors.append("Enter the period start in YYYY-MM-DD format.")
        except AppError as error:
            errors.append(f"Could not calculate the summary: {error}")

    carried_over = (
        sum(1 for item in summary["pending_activities"] if item.get("carried_over"))
        if summary
        else 0
    )

    return templates.TemplateResponse(
        request=request,
        name="summary.html",
        context={
            "kind": period_kind.value,
            "start": start_value,
            "summary": summary,
            "carried_over": carried_over,
            "errors": errors,
        },
        status_code=422 if errors else 200,
    )
