"""Inputs for AI goal suggestions and the Monday-to-Friday week plan.

Everything here only reads records; the model's output is advice that the
user applies (or ignores) through the normal reviewed workflows.
"""

import hashlib
import json
from datetime import date, timedelta

from goal_tracker.domain.errors import AppError
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.llm.assist_schemas import WEEKDAYS
from goal_tracker.progress.workflow import load_all_pending, load_goal_lookup


GOAL_REVIEW = "goal_review"
WEEK_PLAN = "week_plan"


def evidence_hash(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def goal_review_input(goal: dict) -> dict:
    """The facts about one goal the model may use."""
    period = goal["period"]
    return {
        "period_kind": period["kind"],
        "period_start": period["start_date"],
        "period_end": period["end_date"],
        "deadline": goal["target_date"] or period["end_date"],
        "title": goal["title"],
        "description": goal["description"],
        "expected_outcome": goal["expected_outcome"],
        "milestones": [
            milestone["description"]
            for milestone in goal["milestones"]
            if milestone["is_included"]
        ],
    }


def week_to_plan(today: date, requested: date | None = None) -> tuple[date, list[str]]:
    """Return the week's Monday and the working days still to plan.

    On Saturday and Sunday the next week is planned. A requested future
    week is planned in full; past weeks are not offered.
    """
    current_monday = today - timedelta(days=today.weekday())

    if today.weekday() >= 5:
        current_monday += timedelta(days=7)

    monday = current_monday
    if requested is not None:
        requested_monday = requested - timedelta(days=requested.weekday())
        if requested_monday > monday:
            monday = requested_monday

    if monday > today:
        return monday, list(WEEKDAYS)

    return monday, list(WEEKDAYS[today.weekday():])


def _overlaps_week(goal: dict, monday: date) -> bool:
    period = goal["period"]
    friday = monday + timedelta(days=4)
    return (
        date.fromisoformat(period["start_date"]) <= friday
        and date.fromisoformat(period["end_date"]) >= monday
    )


def _completed_milestones(analysis, goals: list[dict], today: date) -> set[str]:
    """IDs of milestones already marked done, from each goal's period."""
    completed: set[str] = set()
    periods = {
        (goal["period"]["kind"], goal["period"]["start_date"], goal["period"]["timezone"])
        for goal in goals
    }

    for kind, start, timezone in periods:
        period = PeriodSpec(kind=kind, start_date=start, timezone=timezone)
        if period.start_date > today:
            continue  # nothing can be done yet
        try:
            summary = analysis.analyze_period(period, as_of=min(today, period.end_date))
        except AppError:
            continue
        for goal in summary["goals"]:
            completed.update(
                milestone["id"]
                for milestone in goal["milestones"]
                if milestone["completed"]
            )

    return completed


def week_plan_input(
    goal_service,
    analysis_service,
    progress_service,
    *,
    monday: date,
    today: date,
) -> dict:
    """Goals with open milestones and pending work for the week."""
    goals = [
        goal
        for goal in load_goal_lookup(goal_service).values()
        if not goal.get("is_independent")
        and goal["lifecycle_status"] == "active"
        and _overlaps_week(goal, monday)
    ]
    done = _completed_milestones(analysis_service, goals, today)

    planned_goals = []
    for goal in sorted(goals, key=lambda item: (item["target_date"] or item["period"]["end_date"], item["title"])):
        open_milestones = [
            {"milestone": milestone["description"], "due": milestone["due_date"]}
            for milestone in goal["milestones"]
            if milestone["is_included"] and milestone["id"] not in done
        ]
        if open_milestones:
            planned_goals.append(
                {
                    "goal": goal["title"],
                    "period": goal["period"]["kind"],
                    "deadline": goal["target_date"] or goal["period"]["end_date"],
                    "expected_outcome": goal["expected_outcome"],
                    "open_milestones": sorted(open_milestones, key=lambda item: item["due"]),
                }
            )

    pending = [
        {"task": activity["description"], "status": activity["state"]}
        for activity in load_all_pending(progress_service, today)
    ]

    return {
        "week_start": monday.isoformat(),
        "week_end": (monday + timedelta(days=4)).isoformat(),
        "goals": planned_goals,
        "pending_work": pending,
    }
