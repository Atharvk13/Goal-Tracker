from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from goal_tracker.cli.prompts import (
    ask_date,
    ask_required_text,
    confirm_and_execute,
    show_validation_error,
)
from goal_tracker.domain.goal_changes import GoalUpdate
from goal_tracker.domain.models import MilestoneInput
from goal_tracker.goals.service import GoalService


def ask_change_metadata(
    goal: dict,
    console: Console,
) -> tuple[str, date | None]:
    """Collect the reason and effective date for a reviewed change."""
    reason = ask_required_text("Reason for this change", console)

    today = datetime.now(
        ZoneInfo(goal["period"]["timezone"])
    ).date()
    period_end = date.fromisoformat(goal["period"]["end_date"])

    if today > period_end:
        effective_date = ask_date(
            "Correction effective date (YYYY-MM-DD)",
            console,
            default=period_end,
        )
    else:
        effective_date = ask_date(
            "Effective date (Enter to use today or the future period start)",
            console,
            optional=True,
        )

    return reason, effective_date


def _select_milestone(
    goal: dict,
    console: Console,
) -> dict | None:
    table = Table(title="Choose a Milestone")
    table.add_column("#")
    table.add_column("Description")
    table.add_column("Scope")

    for number, milestone in enumerate(goal["milestones"], start=1):
        table.add_row(
            str(number),
            Text(milestone["description"]),
            "Included" if milestone["is_included"] else "Removed",
        )

    console.print(table)

    choices = [
        str(number)
        for number in range(1, len(goal["milestones"]) + 1)
    ]

    choice = Prompt.ask(
        "Milestone number (0 to go back)",
        choices=[*choices, "0"],
        console=console,
    )

    if choice == "0":
        return None

    return goal["milestones"][int(choice) - 1]


def _collect_changes(
    goal: dict,
    choice: str,
    console: Console,
) -> dict | None:
    if choice == "1":
        title = ask_required_text(
            "New title",
            console,
            default=goal["title"],
        )
        return {"title": title} if title != goal["title"] else None

    if choice in {"2", "3"}:
        field = "description" if choice == "2" else "expected_outcome"
        label = "Description" if choice == "2" else "Expected outcome"

        value = Prompt.ask(
            f"{label} (Enter to clear)",
            console=console,
        ).strip()

        return {field: value} if value != goal[field] else None

    if choice == "4":
        target_date = ask_date(
            "New goal deadline (Enter to use the period end)",
            console,
            optional=True,
        )
        target_value = (
            target_date.isoformat() if target_date is not None else None
        )

        if target_value == goal["target_date"]:
            return None

        changes = {"target_date": target_date}
        deadline = target_value or goal["period"]["end_date"]

        affected = [
            milestone
            for milestone in goal["milestones"]
            if milestone["is_included"]
            and (
                milestone["due_date"]
                or goal["target_date"]
                or goal["period"]["end_date"]
            ) > deadline
        ]

        if affected:
            console.print(
                f"{len(affected)} included milestone(s) exceed this deadline.",
                markup=False,
            )

            if Confirm.ask(
                "Move those milestone deadlines to the new goal deadline?",
                default=False,
                console=console,
            ):
                changes["milestone_changes"] = [
                    {
                        "milestone_id": milestone["id"],
                        "due_date": date.fromisoformat(deadline),
                    }
                    for milestone in affected
                ]

        return changes

    if choice == "5":
        description = ask_required_text(
            "New milestone description",
            console,
        )
        due_date = ask_date(
            "Due date (Enter to use the goal deadline)",
            console,
            optional=True,
        )

        return {
            "add_milestones": [
                MilestoneInput(
                    description=description,
                    due_date=due_date,
                )
            ]
        }

    milestone = _select_milestone(goal, console)

    if milestone is None:
        return None

    if choice == "6":
        description = ask_required_text(
            "Milestone description",
            console,
            default=milestone["description"],
        )

        fields = {"milestone_id": milestone["id"]}

        if description != milestone["description"]:
            fields["description"] = description

        if Confirm.ask(
            "Change this milestone's deadline too?",
            default=False,
            console=console,
        ):
            fields["due_date"] = ask_date(
                "New due date (Enter to use the goal deadline)",
                console,
                optional=True,
            )

        if len(fields) == 1:
            return None

        return {"milestone_changes": [fields]}

    if milestone["is_included"]:
        included_count = sum(
            item["is_included"] for item in goal["milestones"]
        )

        if included_count == 1:
            console.print(
                "Keep at least one included milestone. "
                "Cancel the goal instead if you want to stop tracking it.",
                style="yellow",
            )
            return None

    return {
        "milestone_changes": [
            {
                "milestone_id": milestone["id"],
                "is_included": not milestone["is_included"],
            }
        ]
    }


def _show_edit_preview(
    goal: dict,
    request: GoalUpdate,
    console: Console,
) -> None:
    console.print("\nReview Goal Changes", style="bold cyan")

    table = Table()
    table.add_column("Item")
    table.add_column("Current")
    table.add_column("Proposed")

    labels = {
        "title": "Title",
        "description": "Description",
        "expected_outcome": "Expected outcome",
        "target_date": "Goal deadline",
    }

    deadline = (
        request.target_date.isoformat()
        if "target_date" in request.model_fields_set
        and request.target_date is not None
        else goal["period"]["end_date"]
        if "target_date" in request.model_fields_set
        else goal["target_date"] or goal["period"]["end_date"]
    )

    for field, label in labels.items():
        if field not in request.model_fields_set:
            continue

        old_value = goal[field]
        new_value = getattr(request, field)

        if field == "target_date":
            old_value = old_value or goal["period"]["end_date"]
            new_value = deadline

        table.add_row(
            label,
            Text(str(old_value) if old_value else "(empty)"),
            Text(str(new_value) if new_value else "(empty)"),
        )

    milestones = {
        milestone["id"]: milestone
        for milestone in goal["milestones"]
    }

    for change in request.milestone_changes:
        original = milestones[str(change.milestone_id)]

        for field in ("description", "due_date", "is_included"):
            if field not in change.model_fields_set:
                continue

            old_value = original[field]
            new_value = getattr(change, field)

            if field == "due_date":
                old_value = old_value or deadline
                new_value = new_value or deadline

            if field == "is_included":
                old_value = "Included" if old_value else "Removed"
                new_value = "Included" if new_value else "Removed"

            table.add_row(
                Text(f"{original['description']} / {field}"),
                Text(str(old_value)),
                Text(str(new_value)),
            )

    for addition in request.add_milestones:
        table.add_row(
            "New milestone",
            "—",
            Text(
                f"{addition.description} "
                f"(due {addition.due_date or deadline})"
            ),
        )

    console.print(table)
    console.print(f"Reason: {request.reason}", markup=False)
    console.print(
        f"Effective date: {request.effective_date or 'Today or future period start'}",
        markup=False,
    )


def edit_goal(
    service: GoalService,
    goal: dict,
    console: Console,
) -> dict | None:
    """Collect one reviewed goal-edit operation."""
    if goal["lifecycle_status"] != "active":
        console.print("Only active goals can be edited.", style="yellow")
        return None

    console.print(
        "\n1. Title\n"
        "2. Description\n"
        "3. Expected outcome\n"
        "4. Goal deadline\n"
        "5. Add milestone\n"
        "6. Edit milestone\n"
        "7. Remove / restore milestone\n"
        "0. Back"
    )

    choice = Prompt.ask(
        "Choose what to edit",
        choices=["1", "2", "3", "4", "5", "6", "7", "0"],
        console=console,
    )

    if choice == "0":
        return None

    try:
        fields = _collect_changes(goal, choice, console)

        if not fields:
            console.print("No changes selected.")
            return None

        reason, effective_date = ask_change_metadata(goal, console)

        request = GoalUpdate(
            goal_id=goal["id"],
            expected_revision=goal["revision"],
            reason=reason,
            effective_date=effective_date,
            **fields,
        )

    except ValidationError as error:
        show_validation_error(error, console)
        return None

    _show_edit_preview(goal, request, console)

    result = confirm_and_execute(
        lambda: service.update_goal(request),
        console,
        confirmation="Save these goal changes?",
    )

    if result is None:
        return None

    console.print("Goal updated.", style="green")
    return result["goal"]