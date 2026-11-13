import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from goal_tracker.db.connection import open_database
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.operations import OperationRepository
from goal_tracker.domain.errors import (
    DuplicateReviewRequiredError,
    StorageError,
)
from goal_tracker.domain.models import GoalCreate
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.goals.service import GoalService
from goal_tracker.progress.service import ProgressService


DAY_ONE = date(2026, 11, 12)
DAY_TWO = date(2026, 11, 13)


@pytest.fixture
def setup(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    migrate_database(database_path, tmp_path / "backups")

    goal_service = GoalService(
        database_path,
        today=lambda: date(2026, 11, 11),
    )
    goal_input = GoalCreate(
        period={
            "kind": "weekly",
            "start_date": "2026-11-09",
        },
        title="Learn LangChain",
        milestones=[{"description": "Complete the tutorial"}],
    )
    goal = goal_service.create_goal(goal_input)["goal"]

    progress = ProgressService(
        database_path,
        today=lambda: DAY_ONE,
    )

    return SimpleNamespace(
        database_path=database_path,
        goal_service=goal_service,
        goal_input=goal_input,
        goal=goal,
        progress=progress,
    )


def test_pending_work_survives_restart_and_can_be_completed(setup):
    milestone_id = setup.goal["milestones"][0]["id"]

    pending = DailyProgressInput(
        activity_date=DAY_ONE,
        raw_text="Started the tutorial and spent 20 minutes.",
        activities=[
            {
                "description": "Study the tutorial",
                "state": "pending",
                "minutes": 20,
                "goal_ids": [setup.goal["id"]],
                "milestone_changes": [
                    {
                        "milestone_id": milestone_id,
                        "state": "not_done",
                    }
                ],
            }
        ],
    )

    first = setup.progress.record_progress(pending)
    original_activity_id = first["entry"]["activities"][0]["id"]

    restarted = ProgressService(
        setup.database_path,
        today=lambda: DAY_TWO,
    )

    assert restarted.get_entry(first["entry"]["id"]) == first["entry"]
    assert restarted.list_pending_activities()[0]["id"] == original_activity_id

    completed = DailyProgressInput(
        activity_date=DAY_TWO,
        raw_text="Finished the tutorial after another 30 minutes.",
        activities=[
            {
                "description": "Finish the tutorial",
                "state": "completed",
                "minutes": 30,
                "goal_ids": [setup.goal["id"]],
                "updates_activity_id": original_activity_id,
                "milestone_changes": [
                    {
                        "milestone_id": milestone_id,
                        "state": "done",
                    }
                ],
            }
        ],
    )

    second = restarted.record_progress(completed)

    assert restarted.record_progress(completed) == second
    assert restarted.list_pending_activities(as_of=DAY_TWO) == []
    assert len(restarted.list_pending_activities(as_of=DAY_ONE)) == 1
    assert len(restarted.get_daily_progress(DAY_ONE, DAY_TWO)) == 2

    original_entry = restarted.get_entry(first["entry"]["id"])
    assert original_entry["activities"][0]["state"] == "pending"
    assert second["entry"]["activities"][0]["state"] == "completed"

    with open_database(setup.database_path) as connection:
        latest = connection.execute(
            """
            SELECT state
            FROM milestone_events
            WHERE milestone_id = ?
            ORDER BY effective_date DESC, sequence DESC
            LIMIT 1
            """,
            (milestone_id,),
        ).fetchone()

    assert latest["state"] == "done"


def test_duplicate_review_and_retry_do_not_create_unwanted_entries(setup):
    first = DailyProgressInput(
        activity_date=DAY_ONE,
        raw_text="Completed tutorial.",
        activities=[
            {
                "description": "Tutorial practice",
                "state": "completed",
                "goal_ids": [setup.goal["id"]],
            }
        ],
    )
    setup.progress.record_progress(first)

    repeated = DailyProgressInput(
        activity_date=DAY_ONE,
        raw_text="  COMPLETED   tutorial.  ",
        activities=[
            {
                "description": "Another tutorial session",
                "state": "completed",
                "goal_ids": [setup.goal["id"]],
            }
        ],
    )

    with pytest.raises(DuplicateReviewRequiredError):
        setup.progress.record_progress(repeated)

    assert len(
        setup.progress.get_daily_progress(DAY_ONE, DAY_ONE)
    ) == 1

    confirmed = DailyProgressInput.model_validate(
        {
            **repeated.model_dump(),
            "allow_duplicate": True,
        }
    )

    result = setup.progress.record_progress(confirmed)

    assert setup.progress.record_progress(confirmed) == result
    assert len(
        setup.progress.get_daily_progress(DAY_ONE, DAY_ONE)
    ) == 2

    with open_database(setup.database_path) as connection:
        # Completed activity text alone must not complete a milestone.
        assert connection.execute(
            "SELECT COUNT(*) FROM milestone_events"
        ).fetchone()[0] == 0

        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE entity_type = 'entry'"
        ).fetchone()[0] == 2


def test_failed_save_rolls_back_entry_events_and_reserved_request(
    setup,
    monkeypatch,
):
    entry = DailyProgressInput(
        activity_date=DAY_ONE,
        raw_text="Completed the tutorial.",
        activities=[
            {
                "description": "Complete tutorial",
                "state": "completed",
                "goal_ids": [setup.goal["id"]],
                "milestone_changes": [
                    {
                        "milestone_id": setup.goal["milestones"][0]["id"],
                        "state": "done",
                    }
                ],
            }
        ],
    )

    def fail_audit(self, **kwargs):
        raise sqlite3.OperationalError("Simulated audit failure")

    monkeypatch.setattr(
        OperationRepository,
        "record_audit_event",
        fail_audit,
    )

    with pytest.raises(StorageError):
        setup.progress.record_progress(entry)

    with open_database(setup.database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM entries) AS entries,
                (SELECT COUNT(*) FROM activities) AS activities,
                (SELECT COUNT(*) FROM activity_events) AS activity_events,
                (SELECT COUNT(*) FROM milestone_events) AS milestone_events
            """
        ).fetchone()

        assert dict(counts) == {
            "entries": 0,
            "activities": 0,
            "activity_events": 0,
            "milestone_events": 0,
        }

        assert OperationRepository(connection).find_request(
            str(entry.request_id)
        ) is None

    assert setup.goal_service.get_goal(setup.goal["id"])["revision"] == 1


def test_one_activity_can_support_multiple_goals_without_duplicate_time(setup):
    second_goal = setup.goal_service.create_goal(
        GoalCreate(
            period=setup.goal_input.period,
            title="Learn local models",
            milestones=[{"description": "Connect Ollama"}],
        )
    )["goal"]

    entry = DailyProgressInput(
        activity_date=DAY_ONE,
        raw_text="Completed an integrated tutorial in 30 minutes.",
        activities=[
            {
                "description": "Complete integrated tutorial",
                "state": "completed",
                "minutes": 30,
                "goal_ids": [setup.goal["id"], second_goal["id"]],
                "milestone_changes": [
                    {
                        "milestone_id": setup.goal["milestones"][0]["id"],
                        "state": "done",
                    },
                    {
                        "milestone_id": second_goal["milestones"][0]["id"],
                        "state": "done",
                    },
                ],
            }
        ],
    )

    result = setup.progress.record_progress(entry)

    for goal in (setup.goal, second_goal):
        records = setup.progress.get_daily_progress(
            DAY_ONE,
            DAY_ONE,
            goal_id=goal["id"],
        )

        assert len(records) == 1
        assert records[0]["id"] == result["entry"]["id"]

    with open_database(setup.database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM activities) AS activities,
                (SELECT COUNT(*) FROM activity_goal_links) AS links,
                (SELECT COUNT(*) FROM milestone_events) AS milestone_events,
                (SELECT SUM(minutes) FROM activities) AS minutes
            """
        ).fetchone()

    assert dict(counts) == {
        "activities": 1,
        "links": 2,
        "milestone_events": 2,
        "minutes": 30,
    }