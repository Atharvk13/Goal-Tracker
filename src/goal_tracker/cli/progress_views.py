from rich.console import Console
from rich.table import Table
from rich.text import Text

from goal_tracker.domain.progress import DailyProgressInput


def _goal_names(activity: dict, goals: dict[str, dict]) -> str:
    names = [
        goals.get(goal_id, {}).get("title", "Goal unavailable")
        for goal_id in activity["goal_ids"]
    ]

    return "\n".join(names) if names else "Unlinked"


def _show_entry(
    entry: dict,
    goals: dict[str, dict],
    console: Console,
    *,
    title: str,
) -> None:
    console.print(f"\n{title}", style="bold cyan")
    console.print(f"Activity date: {entry['activity_date']}", markup=False)
    console.print(
        f"Source: {entry.get('source', 'manual').replace('_', ' ')}",
        markup=False,
    )
    console.print(Text(entry["raw_text"]))

    table = Table(title="Recorded Items")
    table.add_column("#")
    table.add_column("Kind")
    table.add_column("Description")
    table.add_column("State")
    table.add_column("Minutes")
    table.add_column("Goals")

    milestone_names = {
        milestone["id"]: milestone["description"]
        for goal in goals.values()
        for milestone in goal["milestones"]
    }
    milestone_changes = []

    for number, activity in enumerate(entry["activities"], start=1):
        description = activity["description"]

        if activity.get("updates_activity_id") is not None:
            description += " (status update)"

        state = activity["state"]
        state_label = (
            "—"
            if state == "not_applicable"
            else state.replace("_", " ").title()
        )

        table.add_row(
            str(number),
            activity["kind"].title(),
            Text(description),
            state_label,
            (
                str(activity["minutes"])
                if activity["minutes"] is not None
                else "Not provided"
            ),
            Text(_goal_names(activity, goals)),
        )

        milestone_changes.extend(activity["milestone_changes"])

    console.print(table)

    if milestone_changes:
        changes_table = Table(title="Explicit Milestone Changes")
        changes_table.add_column("Milestone")
        changes_table.add_column("New State")

        for change in milestone_changes:
            changes_table.add_row(
                Text(
                    milestone_names.get(
                        change["milestone_id"],
                        "Milestone unavailable",
                    )
                ),
                change["state"].replace("_", " ").title(),
            )

        console.print(changes_table)


def show_progress_draft(
    entry: DailyProgressInput,
    goals: dict[str, dict],
    console: Console,
) -> None:
    """Preview the exact structured entry before confirmation."""
    _show_entry(
        entry.model_dump(mode="json"),
        goals,
        console,
        title="Review Daily Progress",
    )


def show_progress_entry(
    entry: dict,
    goals: dict[str, dict],
    console: Console,
) -> None:
    """Display a saved journal entry."""
    _show_entry(
        entry,
        goals,
        console,
        title="Saved Daily Progress",
    )


def show_entry_list(entries: list[dict], console: Console) -> None:
    """Display short previews for selecting a saved entry."""
    if not entries:
        console.print("No entries match this date range.")
        return

    table = Table(title="Daily Entries")
    table.add_column("#")
    table.add_column("Date")
    table.add_column("Items")
    table.add_column("Preview")

    for number, entry in enumerate(entries, start=1):
        preview = " ".join(entry["raw_text"].split())

        if len(preview) > 80:
            preview = preview[:77] + "..."

        table.add_row(
            str(number),
            entry["activity_date"],
            str(len(entry["activities"])),
            Text(preview),
        )

    console.print(table)


def show_pending_activities(
    activities: list[dict],
    goals: dict[str, dict],
    console: Console,
) -> None:
    """Display original activities with their latest pending state."""
    if not activities:
        console.print("No pending or in-progress activities.")
        return

    table = Table(title="Pending Work")
    table.add_column("#")
    table.add_column("Description")
    table.add_column("State")
    table.add_column("First Recorded")
    table.add_column("Goals")

    for number, activity in enumerate(activities, start=1):
        table.add_row(
            str(number),
            Text(activity["description"]),
            activity["state"].replace("_", " ").title(),
            activity["activity_date"],
            Text(_goal_names(activity, goals)),
        )

    console.print(table)