"""Follow-ups on pending work and milestone deadlines (rules, no model).

Each finding becomes a proposal the user confirms or dismisses. Dedupe keys
include the ISO week, so a dismissed follow-up is asked again at most once a
week.
"""

from datetime import date, timedelta

from goal_tracker.assist.planning import completed_milestones
from goal_tracker.assist.proposals import AgentServices, ProposalStore, ids
from goal_tracker.progress.workflow import (
    load_all_pending,
    load_goal_lookup,
    updatable_pending,
)


STALE_DAYS = 3


def next_working_day(day: date) -> date:
    """The next Monday-to-Friday day after `day`."""
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


def _week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def run_followups(services: AgentServices, store: ProposalStore, today: date) -> int:
    """Create follow-up proposals; returns how many are new."""
    created = 0
    week = _week(today)

    # Work pending or in progress for several days: is it done?
    recordable = {g["id"]: g for g in services.progress.list_recordable_goals(today)}
    for activity in updatable_pending(load_all_pending(services.progress, today), recordable):
        recorded = date.fromisoformat(activity["activity_date"])
        age = (today - recorded).days
        if age < STALE_DAYS:
            continue
        status = activity["state"].replace("_", " ")
        added = store.add(
            kind="update_work_status",
            source="followup",
            summary=f"“{activity['description']}” has been {status} for {age} days. Done?",
            details=[
                ("Work", activity["description"]),
                ("Recorded on", activity["activity_date"]),
                ("If you confirm", "It is marked completed today"),
                ("Still working on it?", "Dismiss this suggestion"),
            ],
            payload={
                **ids(),
                "activity_id": activity["id"],
                "description": activity["description"],
                "state": "completed",
                "goal_ids": list(activity["goal_ids"]),
            },
            dedupe_key=f"stale:{activity['id']}:{week}",
        )
        created += added is not None

    # Milestones that are overdue or due by the next working day.
    goals = [
        goal
        for goal in load_goal_lookup(services.goals).values()
        if goal["lifecycle_status"] == "active"
        and not goal.get("is_independent")
        and goal["period"]["start_date"] <= today.isoformat() <= goal["period"]["end_date"]
    ]
    done = completed_milestones(services.analysis, goals, today)
    soon = next_working_day(today)

    for goal in goals:
        period_end = goal["period"]["end_date"]
        for milestone in goal["milestones"]:
            if not milestone["is_included"] or milestone["id"] in done:
                continue
            due = date.fromisoformat(milestone["due_date"])
            if due > soon:
                continue

            overdue = due < today
            when = f"was due {due.isoformat()}" if overdue else f"is due {due.isoformat()}"
            added = store.add(
                kind="complete_milestone",
                source="followup",
                summary=f"Milestone “{milestone['description']}” {when}. Already done?",
                details=[("Milestone", milestone["description"]), ("Goal", goal["title"])],
                payload={
                    **ids(),
                    "activity_date": today.isoformat(),
                    "milestone_id": milestone["id"],
                    "milestone": milestone["description"],
                    "goal_id": goal["id"],
                },
                dedupe_key=f"done:{milestone['id']}:{week}",
            )
            created += added is not None

            # Overdue and the period still has a working day: offer a new date.
            if overdue and soon.isoformat() <= period_end:
                added = store.add(
                    kind="move_milestone",
                    source="followup",
                    summary=f"Move overdue “{milestone['description']}” to {soon.isoformat()}?",
                    details=[
                        ("Milestone", milestone["description"]),
                        ("Goal", goal["title"]),
                        ("Due date", f"{due.isoformat()} → {soon.isoformat()}"),
                    ],
                    payload={
                        **ids(),
                        "goal_id": goal["id"],
                        "expected_revision": goal["revision"],
                        "milestone_id": milestone["id"],
                        "due_date": soon.isoformat(),
                    },
                    dedupe_key=f"move:{milestone['id']}:{soon.isoformat()}",
                )
                created += added is not None

    return created
