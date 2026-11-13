from rich.console import Console
from rich.table import Table
from rich.text import Text
import json


def _percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def _line(console: Console, value: str) -> None:
    """Display stored text without interpreting Rich markup."""
    console.print(Text(value))


def _show_items(
    console: Console,
    title: str,
    items: list[dict],
) -> None:
    console.print()
    console.print(title, style="bold")

    if not items:
        console.print("None recorded.")
        return

    for item in items:
        description = item.get("description", "")
        state = item.get("state")
        minutes = item.get("minutes")

        details = []

        if state and state != "not_applicable":
            details.append(state.replace("_", " "))

        if minutes is not None:
            details.append(f"{minutes} minutes")

        suffix = f" ({'; '.join(details)})" if details else ""
        _line(console, f"- {description}{suffix}")


def _show_goals(console: Console, goals: list[dict]) -> None:
    console.print()
    console.print("Goals and milestone results", style="bold")

    if not goals:
        console.print("No goals were effective during this period.")
        return

    table = Table()
    table.add_column("Goal")
    table.add_column("Lifecycle")
    table.add_column("Original", justify="right")
    table.add_column("Revised", justify="right")
    table.add_column("Completed", justify="right")

    for goal in goals:
        counts = (
            f"{goal['revised_completed']}/{goal['revised_total']}"
            if goal["revised_scored"]
            else "N/A"
        )

        table.add_row(
            Text(goal["title"]),
            Text(goal["lifecycle_status"]),
            _percentage(goal["original_completion"]),
            _percentage(goal["revised_completion"]),
            counts,
        )

    console.print(table)

    for number, goal in enumerate(goals, start=1):
        console.print()
        _line(console, f"{number}. {goal['title']}")

        if goal["is_original_plan"]:
            _line(
                console,
                f"Original title: {goal['original_title']}",
            )
            _line(
                console,
                "Original expected outcome: "
                f"{goal['original_expected_outcome'] or 'Not provided'}",
            )
        else:
            console.print("Added after initial planning.")

        _line(
            console,
            f"Current expected outcome: "
            f"{goal['expected_outcome'] or 'Not provided'}",
        )

        if goal.get("description"):
            _line(console, f"Description: {goal['description']}")

        _line(console, f"Deadline: {goal['deadline']}")

        if goal["overdue"]:
            console.print("Incomplete after a deadline.", style="yellow")

        for milestone in goal["milestones"]:
            marker = "x" if milestone["completed"] else " "
            labels = []

            if not milestone["is_baseline"]:
                labels.append("added")

            if not milestone["is_included"]:
                labels.append("removed from revised plan")

            if milestone["overdue"]:
                labels.append("overdue")

            suffix = f" [{', '.join(labels)}]" if labels else ""

            _line(
                console,
                f"  [{marker}] {milestone['description']}{suffix}",
            )

            if (
                milestone["is_baseline"]
                and milestone["creation_description"]
                != milestone["description"]
            ):
                _line(
                    console,
                    "      Original description: "
                    f"{milestone['creation_description']}",
                )

        console.print(
            f"Scope changes: {goal['added_milestones']} added, "
            f"{goal['removed_milestones']} removed."
        )

def _show_advisory(report: dict, console: Console) -> None:
    """Display saved suggestions and their supporting evidence."""
    console.print()
    console.print("AI recommendations", style="bold")

    advisory = report["advisory"]

    if advisory is None:
        console.print(
            "AI recommendations are not included in this revision."
        )
        return

    provenance = report.get("model_provenance") or {}

    if provenance.get("model"):
        _line(console, f"Model: {provenance['model']}")

    console.print(
        "These are suggestions. Recommended goals have not "
        "been created automatically."
    )

    sections = (
        ("Areas for improvement", "areas_for_improvement"),
        ("Next-period suggestions", "next_period_suggestions"),
        ("Recommended future goals", "recommended_goals"),
    )
    evidence = advisory["evidence"]

    for title, key in sections:
        console.print()
        console.print(title, style="bold")
        recommendations = advisory[key]

        if not recommendations:
            console.print("No suggestions.")
            continue

        for item in recommendations:
            _line(console, f"- {item['text']}")

            for evidence_id in item["evidence_ids"]:
                record = json.loads(evidence[evidence_id])

                description = (
                    record.get("description")
                    or record.get("title")
                    or (
                        f"{record.get('kind', 'Reporting')} period: "
                        f"{record.get('start_date', '')} to "
                        f"{record.get('end_date', '')}"
                    )
                )

                _line(
                    console,
                    f"  Source [{evidence_id}]: {description}",
                )

    omitted = advisory.get("omitted_count", 0)

    if omitted:
        console.print(
            f"\nAdvice used a limited context: {omitted} records "
            "were omitted from the model input."
        )

def show_report(report: dict, console: Console) -> None:
    """Render the facts stored in a saved report revision."""
    facts = report["facts"]
    period = facts["period"]

    console.print()
    console.print(
        f"{period['kind'].title()} Progress Report",
        style="bold cyan",
    )

    _line(
        console,
        f"Period: {period['start_date']} to {period['end_date']}",
    )
    _line(console, f"Timezone: {period['timezone']}")
    _line(console, f"Report ID: {report['id']}")
    console.print(f"Revision: {report['revision']}")
    _line(console, f"Cutoff: {report['cutoff_date']}")
    _line(console, f"Generated: {report['generated_at']}")

    console.print()
    console.print("Overall results", style="bold")
    console.print(
        "Original-plan completion: "
        f"{_percentage(facts['original_completion'])}"
    )
    console.print(
        "Revised-plan completion: "
        f"{_percentage(facts['revised_completion'])}"
    )
    console.print(
        f"Original goals: {facts['original_goal_count']} | "
        f"Revised scored goals: {facts['revised_goal_count']} | "
        f"Cancelled goals: {facts['cancelled_goal_count']}"
    )
    console.print("Overall completion gives each scored goal equal weight.")

    console.print()
    console.print("Data coverage and time", style="bold")
    console.print(
        f"Days with entries: {facts['recorded_days']} "
        f"of {facts['elapsed_days']}"
    )
    console.print(
        f"Logging consistency: "
        f"{_percentage(facts['logging_consistency'])}"
    )
    console.print(
        "Days without entries represent missing information."
    )

    minutes = facts["recorded_minutes"]
    console.print(
        "Recorded time: "
        + (
            f"{minutes} minutes"
            if minutes is not None
            else "Not provided"
        )
    )
    console.print(
        f"Recorded items without time: {facts['items_without_time']}"
    )

    console.print()
    console.print("Planned versus actual", style="bold")
    console.print(
        f"Milestones due by cutoff: {facts['planned_due']}"
    )
    console.print(
        "Those milestones completed by cutoff: "
        f"{facts['completed_due']}"
    )

    _show_goals(console, facts["goals"])

    _show_items(
        console,
        "Completed activities recorded during the period",
        facts["completed_activities"],
    )

    completed_milestones = [
        {
            "description": (
                f"{goal['title']}: {milestone['description']}"
            )
        }
        for goal in facts["goals"]
        for milestone in goal["milestones"]
        if milestone["completed"] and milestone["is_included"]
    ]
    _show_items(
        console,
        "Completed milestones as of cutoff",
        completed_milestones,
    )

    _show_items(
        console,
        "User-recorded achievements",
        facts["achievements"],
    )
    _show_items(console, "Learnings", facts["learnings"])
    _show_items(console, "Challenges", facts["challenges"])
    _show_items(console, "Observations", facts["observations"])

    _show_items(
        console,
        "Open work across tracked goals as of cutoff",
        facts["pending_activities"],
    )
    console.print(
        "Open work may include carryover from earlier periods "
        "and goals belonging to other periods."
    )

    trend = facts["completion_trend"]

    if trend:
        table = Table(title="Daily Completion Trend")
        table.add_column("Date")
        table.add_column("Revised completion", justify="right")
        table.add_column("Entries", justify="right")

        for day in trend:
            table.add_row(
                day["date"],
                _percentage(day["completion"]),
                str(day["entries"]),
            )

        console.print(table)

        _show_advisory(report, console)

    console.print()
    console.print(
        "This is a saved snapshot. Later corrections appear "
        "in a newly generated report revision."
    )


def show_report_list(
    reports: list[dict],
    console: Console,
) -> None:
    """Display a numbered page of saved reports."""
    if not reports:
        console.print("No saved reports found.")
        return

    table = Table(title="Saved Reports")
    table.add_column("Number", justify="right")
    table.add_column("Period")
    table.add_column("Dates")
    table.add_column("Revision", justify="right")
    table.add_column("PDF status")

    for number, report in enumerate(reports, start=1):
        table.add_row(
            str(number),
            report["kind"],
            f"{report['start_date']} to {report['end_date']}",
            str(report["revision"]),
            report["export_status"],
        )

    console.print(table)