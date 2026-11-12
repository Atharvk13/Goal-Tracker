from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from goal_tracker.bootstrap import AppContext
from goal_tracker.cli.goal_editing import ask_change_metadata, edit_goal
from goal_tracker.cli.goal_views import (
    show_goal,
    show_goal_history,
    show_goal_list,
)
from goal_tracker.cli.prompts import (
    confirm_and_execute,
    show_application_error,
    show_validation_error,
)
from goal_tracker.domain.enums import GoalLifecycle
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.goal_changes import GoalLifecycleChange, PlanFinalize
from goal_tracker.goals.service import GoalService


PAGE_SIZE = 20


def _choose_filter(console: Console) -> str:
    return Prompt.ask(
        "Show goals (0 to return)",
        choices=["active", "cancelled", "archived", "all", "0"],
        default="active",
        console=console,
    )


def _view_history(
    service: GoalService,
    goal_id: str,
    console: Console,
) -> None:
    offset = 0

    while True:
        try:
            history = service.get_goal_history(
                goal_id,
                audit_limit=PAGE_SIZE,
                audit_offset=offset,
            )
        except AppError as error:
            show_application_error(error, console)
            return

        events = history["audit_events"]

        if not events and offset > 0:
            console.print("No more audit events.")
            offset = max(0, offset - PAGE_SIZE)
            continue

        show_goal_history(history, console)

        choices = ["0"]

        if len(events) == PAGE_SIZE:
            choices.append("n")

        if offset > 0:
            choices.append("p")

        choice = Prompt.ask(
            "Audit history: n = next, p = previous, 0 = back",
            choices=choices,
            console=console,
        )

        if choice == "0":
            return

        offset += PAGE_SIZE if choice == "n" else -PAGE_SIZE


def _change_lifecycle(
    service: GoalService,
    goal: dict,
    action: str,
    console: Console,
) -> None:
    try:
        reason, effective_date = ask_change_metadata(goal, console)

        request = GoalLifecycleChange(
            goal_id=goal["id"],
            expected_revision=goal["revision"],
            action=action,
            reason=reason,
            effective_date=effective_date,
        )
    except ValidationError as error:
        show_validation_error(error, console)
        return

    target_status = "cancelled" if action == "cancel" else "archived"

    console.print(Text(f"\nReview: {goal['title']}", style="bold cyan"))
    console.print(
        f"Status: {goal['lifecycle_status']} → {target_status}",
        markup=False,
    )
    console.print(f"Reason: {reason}", markup=False)
    console.print(
        f"Effective date: {effective_date or 'Today or future period start'}",
        markup=False,
    )
    console.print("The goal and its history will be retained.")

    result = confirm_and_execute(
        lambda: service.change_lifecycle(request),
        console,
        confirmation=f"{action.capitalize()} this goal?",
    )

    if result is not None:
        console.print(f"Goal {target_status}.", style="green")


def _finalize_plan(
    service: GoalService,
    goal: dict,
    console: Console,
) -> None:
    period = goal["period"]

    console.print(
        f"\nFinalize initial planning for {period['kind']} "
        f"{period['start_date']} to {period['end_date']}.",
        markup=False,
    )
    console.print(
        "You can still add goals later; they will be labelled additions."
    )

    request = PlanFinalize(period_id=period["id"])

    result = confirm_and_execute(
        lambda: service.finalize_plan(request),
        console,
        confirmation="Finalize this initial plan?",
    )

    if result is not None:
        console.print("Initial plan finalized.", style="green")


def _manage_single_goal(
    service: GoalService,
    goal_id: str,
    console: Console,
) -> None:
    labels = {
        "1": "Edit goal",
        "2": "View history",
        "3": "Cancel goal",
        "4": "Archive goal",
        "5": "Finalize this period's initial plan",
        "0": "Back to goal list",
    }

    while True:
        try:
            goal = service.get_goal(goal_id)
        except AppError as error:
            show_application_error(error, console)
            return

        show_goal(goal, console)
        available = {"2", "0"}

        if goal["lifecycle_status"] == "active":
            available.update({"1", "3", "4"})
        elif goal["lifecycle_status"] == "cancelled":
            available.add("4")

        today = datetime.now(
            ZoneInfo(goal["period"]["timezone"])
        ).date()

        if (
            goal["period"]["planning_closed_at"] is None
            and today <= date.fromisoformat(goal["period"]["end_date"])
        ):
            available.add("5")

        for number, label in labels.items():
            if number in available:
                console.print(f"{number}. {label}")

        choice = Prompt.ask(
            "Choose an action",
            choices=[
                number for number in labels
                if number in available
            ],
            console=console,
        )

        if choice == "0":
            return

        if choice == "1":
            edit_goal(service, goal, console)
        elif choice == "2":
            _view_history(service, goal_id, console)
        elif choice in {"3", "4"}:
            _change_lifecycle(
                service,
                goal,
                "cancel" if choice == "3" else "archive",
                console,
            )
        elif choice == "5":
            _finalize_plan(service, goal, console)


def manage_goals(context: AppContext, console: Console) -> None:
    """Browse and manage saved goals without calling the LLM."""
    try:
        service = GoalService(
            context.paths.database,
            timezone=context.settings.timezone,
        )
    except AppError as error:
        show_application_error(error, console)
        return

    selected_filter = _choose_filter(console)
    offset = 0

    while selected_filter != "0":
        status = (
            None
            if selected_filter == "all"
            else GoalLifecycle(selected_filter)
        )

        try:
            goals = service.list_goals(
                lifecycle_status=status,
                limit=PAGE_SIZE,
                offset=offset,
            )
        except AppError as error:
            show_application_error(error, console)
            return

        if not goals and offset > 0:
            console.print("No more goals.")
            offset = max(0, offset - PAGE_SIZE)
            continue

        show_goal_list(goals, console)
        console.print(f"Page {offset // PAGE_SIZE + 1}")

        choices = [
            str(number)
            for number in range(1, len(goals) + 1)
        ]
        choices.extend(["f", "0"])

        if len(goals) == PAGE_SIZE:
            choices.append("n")

        if offset > 0:
            choices.append("p")

        choice = Prompt.ask(
            "Goal number, n = next, p = previous, f = filter, 0 = back",
            choices=choices,
            console=console,
        )

        if choice == "0":
            return

        if choice == "f":
            selected_filter = _choose_filter(console)
            offset = 0
            continue

        if choice == "n":
            offset += PAGE_SIZE
            continue

        if choice == "p":
            offset -= PAGE_SIZE
            continue

        selected_goal = goals[int(choice) - 1]
        _manage_single_goal(service, selected_goal["id"], console)