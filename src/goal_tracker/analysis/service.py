import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from goal_tracker.analysis.metrics import (
    completion_percentage,
    logging_consistency,
    overall_completion,
)
from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.repositories.goal_changes import GoalChangeRepository
from goal_tracker.db.repositories.goals import GoalRepository
from goal_tracker.db.repositories.progress import ProgressRepository
from goal_tracker.domain.errors import DomainValidationError, StorageError
from goal_tracker.domain.models import PeriodSpec


def _missed_deadline(
    due_date: date,
    completed: bool,
    cutoff: date,
    today: date,
) -> bool:
    if completed:
        return False

    return due_date < cutoff or (
        cutoff < today and due_date == cutoff
    )


def _summarize_goal(
    goal: dict,
    history: dict,
    events: list[dict],
    cutoff: date,
    today: date,
    worked_goal_ids: set[str],
) -> dict | None:
    cutoff_text = cutoff.isoformat()

    eligible = [
        row for row in history["goal_revisions"]
        if row["effective_date"] <= cutoff_text
    ]

    if not eligible:
        return None

    revision = max(
        eligible,
        key=lambda row: (row["effective_date"], row["revision"]),
    )
    original = min(
        history["goal_revisions"],
        key=lambda row: row["revision"],
    )

    latest_milestones = {}
    first_milestones = {}

    for row in history["milestone_revisions"]:
        if row["effective_date"] > cutoff_text:
            continue

        milestone_id = row["milestone_id"]

        if row["revision"] == 1:
            first_milestones[milestone_id] = row

        previous = latest_milestones.get(milestone_id)

        if previous is None or (
            row["effective_date"], row["revision"]
        ) > (
            previous["effective_date"], previous["revision"]
        ):
            latest_milestones[milestone_id] = row

    latest_events = {}

    for event in events:
        if event["effective_date"] > cutoff_text:
            continue

        previous = latest_events.get(event["milestone_id"])

        if previous is None or (
            event["effective_date"], event["sequence"]
        ) > (
            previous["effective_date"], previous["sequence"]
        ):
            latest_events[event["milestone_id"]] = event

    period_end = goal["period"]["end_date"]
    deadline = revision["target_date"] or period_end
    original_deadline = original["target_date"] or period_end

    milestones = []

    for milestone_id, current in latest_milestones.items():
        first = first_milestones[milestone_id]
        event = latest_events.get(milestone_id)
        completed = event is not None and event["state"] == "done"
        due_date = current["due_date"] or deadline

        milestones.append(
            {
                "id": milestone_id,
                "description": current["description"],
                "creation_description": first["description"],
                "due_date": due_date,
                "original_due_date": (
                    first["due_date"] or original_deadline
                ),
                "is_baseline": bool(current["is_baseline"]),
                "is_included": bool(current["is_included"]),
                "completed": completed,
                "state": "done" if completed else "not_done",
                "overdue": (
                    bool(current["is_included"])
                    and revision["lifecycle_status"] != "cancelled"
                    and _missed_deadline(
                        date.fromisoformat(due_date),
                        completed,
                        cutoff,
                        today,
                    )
                ),
                "evidence_entry_id": (
                    event["source_entry_id"] if event else None
                ),
                "evidence_event_id": event["id"] if event else None,
            }
        )

    baseline = [
        milestone for milestone in milestones
        if milestone["is_baseline"]
    ]
    included = [
        milestone for milestone in milestones
        if milestone["is_included"]
    ]

    baseline_done = sum(item["completed"] for item in baseline)
    revised_done = sum(item["completed"] for item in included)

    revised_scored = (
        revision["lifecycle_status"] != "cancelled"
        and bool(included)
    )

    revised_percentage = (
        completion_percentage(revised_done, len(included))
        if revised_scored
        else None
    )

    if revised_percentage is None:
        completion_status = "unscored"
    elif revised_percentage == 100:
        completion_status = "complete"
    elif revised_done or goal["id"] in worked_goal_ids:
        completion_status = "in_progress"
    else:
        completion_status = "not_started"

    planned = [
        item for item in included
        if item["due_date"] <= cutoff_text
    ] if revised_scored else []

    return {
        "id": goal["id"],
        "title": revision["title"],
        "description": revision["description"],
        "expected_outcome": revision["expected_outcome"],
        "revision": revision["revision"],
        "lifecycle_status": revision["lifecycle_status"],
        "completion_status": completion_status,
        "is_original_plan": bool(goal["is_original_plan"]),
        "original_title": original["title"],
        "original_expected_outcome": original["expected_outcome"],
        "deadline": deadline,
        "original_deadline": original_deadline,
        "baseline_completed": baseline_done,
        "baseline_total": len(baseline),
        "original_completion": (
            completion_percentage(baseline_done, len(baseline))
            if goal["is_original_plan"]
            else None
        ),
        "creation_completion": completion_percentage(
            baseline_done,
            len(baseline),
        ),
        "revised_completed": revised_done,
        "revised_total": len(included),
        "revised_scored": revised_scored,
        "revised_completion": revised_percentage,
        "planned_due": len(planned),
        "completed_due": sum(item["completed"] for item in planned),
        "added_milestones": sum(
            not item["is_baseline"] for item in milestones
        ),
        "removed_milestones": sum(
            not item["is_included"] for item in milestones
        ),
        "overdue": (
            revised_scored
            and revised_percentage != 100
            and (
                any(item["overdue"] for item in milestones)
                or _missed_deadline(
                    date.fromisoformat(deadline),
                    False,
                    cutoff,
                    today,
                )
            )
        ),
        "milestones": milestones,
    }


class AnalysisService:
    """Build factual progress summaries from SQLite records."""

    def __init__(
        self,
        database_path: Path,
        *,
        timezone: str = "Asia/Kolkata",
        today: Callable[[], date] | None = None,
    ):
        database_path = database_path.expanduser()

        if not database_path.is_absolute():
            raise DomainValidationError(
                "The database path must be absolute."
            )

        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise DomainValidationError(
                "The application timezone is invalid."
            ) from error

        self.database_path = database_path.resolve()
        self.timezone = timezone
        self._today = (
            today if today is not None
            else lambda: datetime.now(zone).date()
        )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise StorageError("The application database is not initialized.")

        try:
            with open_database(self.database_path) as connection:
                with transaction(connection, write=False):
                    yield connection

        except (sqlite3.Error, OSError, ValueError) as error:
            raise StorageError(
                "Progress analysis could not read the stored records."
            ) from error

    @staticmethod
    def _entries(
        repository: ProgressRepository,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        if end_date < start_date:
            return []

        entries = []
        offset = 0

        while True:
            page = repository.list_entries(
                start_date,
                end_date,
                limit=100,
                offset=offset,
            )
            entries.extend(page)

            if len(page) < 100:
                return entries

            offset += 100

    @staticmethod
    def _pending(
        repository: ProgressRepository,
        cutoff: date,
    ) -> list[dict]:
        activities = []
        offset = 0

        while True:
            page = repository.list_pending_activities(
                as_of=cutoff,
                limit=100,
                offset=offset,
            )
            activities.extend(page)

            if len(page) < 100:
                return activities

            offset += 100

    def analyze_period(
        self,
        period: PeriodSpec,
        *,
        as_of: date | None = None,
    ) -> dict:
        if period.timezone != self.timezone:
            raise DomainValidationError(
                "The reporting timezone must match the application timezone."
            )

        today = self._today()
        requested_cutoff = as_of or today

        if requested_cutoff > today:
            raise DomainValidationError(
                "Progress cannot be analyzed for a future cutoff date."
            )

        cutoff = min(requested_cutoff, period.end_date)
        elapsed_days = max(
            0,
            (cutoff - period.start_date).days + 1,
        )

        with self._read() as connection:
            goals_repository = GoalRepository(connection)
            changes_repository = GoalChangeRepository(connection)
            progress_repository = ProgressRepository(connection)

            stored_period = goals_repository.find_period(period)

            entries = self._entries(
                progress_repository,
                period.start_date,
                cutoff,
            )

            activities = {
                activity["id"]: activity
                for entry in entries
                for activity in entry["activities"]
            }

            worked_goal_ids = {
                goal_id
                for activity in activities.values()
                if activity["kind"] == "activity"
                for goal_id in activity["goal_ids"]
            }

            goal_records = []

            if stored_period is not None:
                offset = 0

                while True:
                    page = goals_repository.list_goals(
                        period_id=stored_period["id"],
                        limit=100,
                        offset=offset,
                    )

                    for goal in page:
                        history = changes_repository.get_goal_history(
                            goal["id"]
                        )

                        events = [
                            dict(row)
                            for row in connection.execute(
                                """
                                SELECT event.*
                                FROM milestone_events AS event
                                JOIN milestones AS milestone
                                  ON milestone.id = event.milestone_id
                                WHERE milestone.goal_id = ?
                                  AND event.effective_date <= ?
                                ORDER BY
                                    event.effective_date,
                                    event.sequence
                                """,
                                (goal["id"], cutoff.isoformat()),
                            ).fetchall()
                        ]

                        goal_records.append((goal, history, events))

                    if len(page) < 100:
                        break

                    offset += 100

            summaries = []

            for goal, history, events in goal_records:
                summary = _summarize_goal(
                    goal,
                    history,
                    events,
                    cutoff,
                    today,
                    worked_goal_ids,
                )

                if summary is not None:
                    summaries.append(summary)

            pending = (
                self._pending(progress_repository, cutoff)
                if elapsed_days > 0
                else []
            )

            for activity in pending:
                activity["carried_over"] = (
                    activity["activity_date"]
                    < period.start_date.isoformat()
                )

            original_counts = [
                (goal["baseline_completed"], goal["baseline_total"])
                for goal in summaries
                if goal["is_original_plan"]
            ]
            revised_counts = [
                (goal["revised_completed"], goal["revised_total"])
                for goal in summaries
                if goal["revised_scored"]
            ]

            recorded_days = len({
                entry["activity_date"] for entry in entries
            })
            known_minutes = [
                activity["minutes"]
                for activity in activities.values()
                if activity["minutes"] is not None
            ]

            trend = []

            for day_offset in range(elapsed_days):
                day = period.start_date + timedelta(days=day_offset)
                day_goals = []

                for goal, history, events in goal_records:
                    summary = _summarize_goal(
                        goal,
                        history,
                        events,
                        day,
                        today,
                        set(),
                    )

                    if summary is not None:
                        day_goals.append(summary)

                scored = [
                    goal for goal in day_goals
                    if goal["revised_scored"]
                ]

                trend.append(
                    {
                        "date": day.isoformat(),
                        "completion": overall_completion(
                            (
                                goal["revised_completed"],
                                goal["revised_total"],
                            )
                            for goal in scored
                        ),
                        "scored_goals": len(scored),
                        "scored_milestones": sum(
                            goal["revised_total"] for goal in scored
                        ),
                        "entries": sum(
                            entry["activity_date"] == day.isoformat()
                            for entry in entries
                        ),
                    }
                )

            items = list(activities.values())

            return {
                "period": {
                    "id": stored_period["id"] if stored_period else None,
                    "kind": period.kind.value,
                    "start_date": period.start_date.isoformat(),
                    "end_date": period.end_date.isoformat(),
                    "timezone": period.timezone,
                },
                "as_of": cutoff.isoformat(),
                "period_has_ended": today > period.end_date,
                "not_started": cutoff < period.start_date,
                "original_completion": overall_completion(original_counts),
                "revised_completion": overall_completion(revised_counts),
                "original_goal_count": len(original_counts),
                "revised_goal_count": len(revised_counts),
                "cancelled_goal_count": sum(
                    goal["lifecycle_status"] == "cancelled"
                    for goal in summaries
                ),
                "recorded_days": recorded_days,
                "elapsed_days": elapsed_days,
                "logging_consistency": logging_consistency(
                    recorded_days,
                    elapsed_days,
                ),
                "recorded_minutes": (
                    sum(known_minutes) if known_minutes else None
                ),
                "items_without_time": sum(
                    activity["minutes"] is None for activity in items
                ),
                "planned_due": sum(
                    goal["planned_due"] for goal in summaries
                ),
                "completed_due": sum(
                    goal["completed_due"] for goal in summaries
                ),
                "goals": summaries,
                "entries": entries,
                "completed_activities": [
                    activity for activity in items
                    if activity["kind"] == "activity"
                    and activity["state"] == "completed"
                ],
                "pending_activities": pending,
                "learnings": [
                    activity for activity in items
                    if activity["kind"] == "learning"
                ],
                "challenges": [
                    activity for activity in items
                    if activity["kind"] == "challenge"
                ],
                "achievements": [
                    activity for activity in items
                    if activity["kind"] == "achievement"
                ],
                "observations": [
                    activity for activity in items
                    if activity["kind"] == "observation"
                ],
                "completion_trend": trend,
            }