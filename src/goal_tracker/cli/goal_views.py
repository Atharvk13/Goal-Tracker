from rich.console import Console
from rich.table import Table
from rich.text import Text

from goal_tracker.domain.models import GoalCreate


def show_goal_draft(goal: GoalCreate, console: Console) -> None:
    """Display a goal for review before saving."""
    console.print("\nReview Goal", style="bold cyan")
    console.print(f"Title: {goal.title}", markup=False)
    console.print(f"Description: {goal.description}", markup=False)
    console.print(f"Expected outcome: {goal.expected_outcome}", markup=False)
    console.print(
        f"Period: {goal.period.kind.value} "
        f"{goal.period.start_date} to {goal.period.end_date}",
        markup=False,
    )
    console.print(f"Timezone: {goal.period.timezone}", markup=False)

    deadline = goal.target_date or goal.period.end_date
    console.print(f"Goal deadline: {deadline}", markup=False)

    table = Table(title="Proposed Milestones")
    table.add_column("#")
    table.add_column("Description")
    table.add_column("Due")

    for number, milestone in enumerate(goal.milestones, start=1):
        due_date = milestone.due_date or deadline

        table.add_row(
            str(number),
            Text(milestone.description),
            str(due_date),
        )

    console.print(table)


def show_goal(goal: dict, console: Console) -> None:
    """Display saved goal details and its current milestone scope."""
    console.print()
    console.print(Text(goal["title"], style="bold cyan"))
    console.print(
        f"Status: {goal['lifecycle_status']} | Revision: {goal['revision']}",
        markup=False,
    )

    period = goal["period"]
    console.print(
        f"Period: {period['kind']} "
        f"{period['start_date']} to {period['end_date']}",
        markup=False,
    )

    membership = (
        "Original period plan"
        if goal["is_original_plan"]
        else "Added after initial planning"
    )
    planning = (
        "Finalized"
        if period["planning_closed_at"] is not None
        else "Still open"
    )

    console.print(f"Plan membership: {membership}", markup=False)
    console.print(f"Initial planning: {planning}", markup=False)
    console.print(f"Description: {goal['description']}", markup=False)
    console.print(
        f"Expected outcome: {goal['expected_outcome']}",
        markup=False,
    )
    console.print(
        f"Goal deadline: {goal['target_date'] or period['end_date']}",
        markup=False,
    )

    table = Table(title="Milestones")
    table.add_column("#")
    table.add_column("Description")
    table.add_column("Due")
    table.add_column("Current Scope")
    table.add_column("Origin")

    for number, milestone in enumerate(goal["milestones"], start=1):
        table.add_row(
            str(number),
            Text(milestone["description"]),
            str(milestone["due_date"] or "Default"),
            "Included" if milestone["is_included"] else "Removed",
            "Creation baseline" if milestone["is_baseline"] else "Added later",
        )

    console.print(table)


def show_goal_list(goals: list[dict], console: Console) -> None:
    """Display numbered goals for selection."""
    if not goals:
        console.print("No goals match this selection.")
        return

    table = Table(title="Goals")
    table.add_column("#")
    table.add_column("Title")
    table.add_column("Period")
    table.add_column("Status")
    table.add_column("Revision")

    for number, goal in enumerate(goals, start=1):
        period = goal["period"]

        table.add_row(
            str(number),
            Text(goal["title"]),
            f"{period['kind']} {period['start_date']}",
            goal["lifecycle_status"],
            str(goal["revision"]),
        )

    console.print(table)


def show_goal_history(history: dict, console: Console) -> None:
    """Display saved revisions and the supplied audit page."""
    goal_table = Table(title="Goal Revision History")
    goal_table.add_column("Revision")
    goal_table.add_column("Title")
    goal_table.add_column("Status")
    goal_table.add_column("Effective Date")
    goal_table.add_column("Reason")

    for revision in history["goal_revisions"]:
        goal_table.add_row(
            str(revision["revision"]),
            Text(revision["title"]),
            revision["lifecycle_status"],
            revision["effective_date"],
            Text(revision["change_reason"]),
        )

    console.print(goal_table)

    milestone_table = Table(title="Milestone Revision History")
    milestone_table.add_column("Description")
    milestone_table.add_column("Revision")
    milestone_table.add_column("Due")
    milestone_table.add_column("Scope")
    milestone_table.add_column("Effective Date")
    milestone_table.add_column("Reason")

    for revision in history["milestone_revisions"]:
        milestone_table.add_row(
            Text(revision["description"]),
            str(revision["revision"]),
            str(revision["due_date"] or "Default"),
            "Included" if revision["is_included"] else "Removed",
            revision["effective_date"],
            Text(revision["change_reason"]),
        )

    console.print(milestone_table)

    audit_table = Table(title="Audit Events")
    audit_table.add_column("Action")
    audit_table.add_column("Recorded At")
    audit_table.add_column("Reason")

    for event in history["audit_events"]:
        audit_table.add_row(
            event["action"],
            event["recorded_at"],
            Text(event["reason"]),
        )

    console.print(audit_table)

    pagination = history["audit_pagination"]
    console.print(
        f"Audit page: offset {pagination['offset']}, "
        f"up to {pagination['limit']} events.",
        markup=False,
    )