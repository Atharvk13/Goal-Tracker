"""Build a daily status mail from one day's progress entries.

The template is plain text. An optional first line "Subject: ..." becomes the
mail subject; everything after it is the body. Placeholders look like
{{goal_tasks}} and are replaced with text only — templates cannot run code.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("daily_template.txt")

NONE_TEXT = "- None"

PLACEHOLDERS: dict[str, str] = {
    "date": "Day in YYYY-MM-DD format",
    "date_long": "Day written out, e.g. 19 November 2026",
    "weekday": "Day of the week, e.g. Thursday",
    "goal_tasks": "Work linked to your goals, grouped under each goal title",
    "other_tasks": "Independent work and work not linked to any goal",
    "completed_tasks": "All work completed that day",
    "in_progress_tasks": "All work still in progress",
    "pending_tasks": "All work marked pending",
    "open_tasks": "Pending and in-progress work recorded that day",
    "all_open_tasks": "All work still pending or in progress as of that day, "
    "including earlier days",
    "learnings": "Learnings recorded that day",
    "challenges": "Challenges recorded that day",
    "achievements": "Achievements recorded that day",
    "observations": "Observations recorded that day",
    "total_minutes": "Total recorded minutes",
    "total_hours": "Total recorded time, e.g. 2 h 15 min",
    "journal": "Your original journal text",
    "entry_count": "Number of entries saved that day",
    "week_completion": "This week's plan completion percentage",
}

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_SUBJECT = re.compile(r"^\s*subject\s*:\s*(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class DailyMail:
    subject: str
    body: str
    unknown_placeholders: list[str] = field(default_factory=list)


def load_template(path: Path | None) -> tuple[str, Path]:
    """Read the configured template, falling back to the bundled one."""
    chosen = path.expanduser() if path is not None else DEFAULT_TEMPLATE_PATH
    return chosen.read_text(encoding="utf-8"), chosen


def _duration(minutes: int | None) -> str:
    if minutes is None:
        return "Not recorded"
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} h {rest} min"
    if hours:
        return f"{hours} h"
    return f"{rest} min"


def _task_line(activity: dict, indent: str = "") -> str:
    details = []

    if activity["state"] != "not_applicable":
        details.append(activity["state"].replace("_", " ").capitalize())

    if activity.get("minutes") is not None:
        details.append(f"{activity['minutes']} min")

    text = activity["description"]

    if activity.get("updates_activity_id"):
        text += " (status update)"

    suffix = f" ({', '.join(details)})" if details else ""
    return f"{indent}- {text}{suffix}"


def _lines(activities: list[dict]) -> str:
    return "\n".join(_task_line(a) for a in activities) or NONE_TEXT


def date_values(day: date) -> dict[str, str]:
    return {
        "date": day.isoformat(),
        "date_long": f"{day.day} {day:%B %Y}",
        "weekday": f"{day:%A}",
    }


def build_daily_context(
    day: date,
    entries: list[dict],
    goal_lookup: dict[str, dict],
    week_completion: float | None = None,
    open_work: list[dict] | None = None,
) -> dict[str, str]:
    """Turn a day's saved entries into placeholder values.

    open_work is every activity still pending or in progress as of the day
    (from ProgressService.list_pending_activities), used for plans ahead.
    """
    activities = [a for entry in entries for a in entry["activities"]]
    work = [a for a in activities if a["kind"] == "activity"]

    independent_goals = {
        goal_id for goal_id, goal in goal_lookup.items() if goal.get("is_independent")
    }

    by_goal: dict[str, list[dict]] = {}
    other: list[dict] = []

    for activity in work:
        planned = [g for g in activity["goal_ids"] if g not in independent_goals]

        if not planned:
            other.append(activity)
            continue

        for goal_id in planned:
            title = goal_lookup.get(goal_id, {}).get("title", "Goal unavailable")
            by_goal.setdefault(title, []).append(activity)

    goal_tasks = "\n".join(
        f"{title}:\n" + "\n".join(_task_line(a, "  ") for a in items)
        for title, items in by_goal.items()
    ) or NONE_TEXT

    def of_kind(kind: str) -> str:
        return "\n".join(
            f"- {a['description']}" for a in activities if a["kind"] == kind
        ) or NONE_TEXT

    def with_state(*states: str) -> list[dict]:
        return [a for a in work if a["state"] in states]

    known_minutes = [a["minutes"] for a in activities if a.get("minutes") is not None]
    total = sum(known_minutes) if known_minutes else None

    return {
        **date_values(day),
        "goal_tasks": goal_tasks,
        "other_tasks": _lines(other),
        "completed_tasks": _lines(with_state("completed")),
        "in_progress_tasks": _lines(with_state("in_progress")),
        "pending_tasks": _lines(with_state("pending")),
        "open_tasks": _lines(with_state("pending", "in_progress")),
        "all_open_tasks": _lines(open_work or []),
        "learnings": of_kind("learning"),
        "challenges": of_kind("challenge"),
        "achievements": of_kind("achievement"),
        "observations": of_kind("observation"),
        "total_minutes": str(total) if total is not None else "Not recorded",
        "total_hours": _duration(total),
        "journal": "\n\n".join(entry["raw_text"].strip() for entry in entries)
        or "No journal recorded.",
        "entry_count": str(len(entries)),
        "week_completion": (
            f"{week_completion:.1f}%" if week_completion is not None else "N/A"
        ),
    }


def render_daily_mail(template: str, context: dict[str, str]) -> DailyMail:
    """Fill placeholders; unknown ones are left in place and reported."""
    unknown: list[str] = []

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name in context:
            return context[name]
        if name not in unknown:
            unknown.append(name)
        return match.group(0)

    lines = template.replace("\r\n", "\n").split("\n")
    subject = ""

    if lines and (header := _SUBJECT.match(lines[0])):
        subject = _PLACEHOLDER.sub(replace, header.group(1)).strip()
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]

    body = _PLACEHOLDER.sub(replace, "\n".join(lines)).rstrip() + "\n"

    return DailyMail(subject=subject, body=body, unknown_placeholders=unknown)
