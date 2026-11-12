from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm, Prompt

from goal_tracker.bootstrap import AppContext
from goal_tracker.cli.goal_views import show_goal, show_goal_draft
from goal_tracker.cli.prompts import (
    ask_date,
    ask_required_text,
    confirm_and_execute,
    show_application_error,
    show_validation_error,
)
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.goal_changes import PlanFinalize
from goal_tracker.domain.models import GoalCreate, MilestoneInput, PeriodSpec
from goal_tracker.goals.service import GoalService


def _ask_period(
    kind: PeriodKind,
    timezone: str,
    console: Console,
) -> PeriodSpec:
    today = datetime.now(ZoneInfo(timezone)).date()

    default_start = (
        today - timedelta(days=today.weekday())
        if kind == PeriodKind.WEEKLY
        else today.replace(day=1)
    )

    while True:
        start_date = ask_date(
            "Period start (YYYY-MM-DD)",
            console,
            default=default_start,
        )

        try:
            period = PeriodSpec(
                kind=kind,
                start_date=start_date,
                timezone=timezone,
            )
        except ValidationError as error:
            show_validation_error(error, console)
            continue

        if period.end_date < today:
            console.print(
                "Choose the current period or a future period.",
                style="yellow",
            )
            continue

        console.print(
            f"Selected period: {period.start_date} to {period.end_date}",
            markup=False,
        )
        return period


def _ask_milestones(
    period: PeriodSpec,
    deadline: date,
    console: Console,
) -> list[MilestoneInput]:
    milestones = []
    descriptions = set()

    while True:
        description = Prompt.ask(
            "Milestone description (Enter to finish)",
            console=console,
        ).strip()

        if not description:
            if milestones:
                return milestones

            console.print(
                "Add at least one milestone.",
                style="yellow",
            )
            continue

        normalized = description.casefold()

        if normalized in descriptions:
            console.print(
                "That milestone description is already included.",
                style="yellow",
            )
            continue

        while True:
            due_date = ask_date(
                f"Due date (Enter to use {deadline})",
                console,
                optional=True,
            )

            if (
                due_date is not None
                and not period.start_date <= due_date <= deadline
            ):
                console.print(
                    "The due date must fall within the period "
                    "and cannot exceed the goal deadline.",
                    style="yellow",
                )
                continue

            break

        milestones.append(
            MilestoneInput(
                description=description,
                due_date=due_date,
            )
        )
        descriptions.add(normalized)


def _ask_goal(period: PeriodSpec, console: Console) -> GoalCreate:
    title = ask_required_text("Goal title", console)
    description = Prompt.ask(
        "Description (optional)",
        console=console,
    ).strip()
    expected_outcome = Prompt.ask(
        "Expected outcome (optional)",
        console=console,
    ).strip()

    while True:
        target_date = ask_date(
            "Goal deadline (Enter to use the period end)",
            console,
            optional=True,
        )

        if (
            target_date is not None
            and not period.start_date <= target_date <= period.end_date
        ):
            console.print(
                "The goal deadline must fall within the selected period.",
                style="yellow",
            )
            continue

        break

    milestones = _ask_milestones(
        period,
        target_date or period.end_date,
        console,
    )

    return GoalCreate(
        period=period,
        title=title,
        description=description,
        expected_outcome=expected_outcome,
        target_date=target_date,
        milestones=milestones,
    )


def create_goals(
    context: AppContext,
    kind: PeriodKind,
    console: Console,
) -> None:
    """Collect, review and save goals without using the LLM."""
    try:
        service = GoalService(
            context.paths.database,
            timezone=context.settings.timezone,
        )
    except AppError as error:
        show_application_error(error, console)
        return

    console.print(
        f"\nCreate {kind.value.title()} Goals",
        style="bold cyan",
    )
    period = _ask_period(kind, context.settings.timezone, console)
    last_saved = None

    while True:
        try:
            draft = _ask_goal(period, console)
        except ValidationError as error:
            show_validation_error(error, console)
            continue

        show_goal_draft(draft, console)

        result = confirm_and_execute(
            lambda: service.create_goal(draft),
            console,
            confirmation="Save this goal?",
        )

        if result is not None:
            last_saved = result["goal"]
            console.print("Goal saved.", style="green")
            show_goal(last_saved, console)

        if not Confirm.ask(
            "Add another goal to this period?",
            default=False,
            console=console,
        ):
            break

    if last_saved is None:
        return

    if last_saved["period"]["planning_closed_at"] is not None:
        return

    console.print(
        "\nFinalizing identifies the goals in your initial period plan. "
        "You can still add goals later; they will be labelled additions."
    )

    request = PlanFinalize(period_id=last_saved["period"]["id"])

    result = confirm_and_execute(
        lambda: service.finalize_plan(request),
        console,
        confirmation="Finalize this period's initial plan?",
    )

    if result is not None:
        console.print("Initial plan finalized.", style="green")