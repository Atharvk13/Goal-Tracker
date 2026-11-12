import sqlite3
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.goal_changes import GoalChangeRepository
from goal_tracker.db.repositories.goals import GoalRepository
from goal_tracker.domain.errors import ConflictError
from goal_tracker.domain.models import GoalCreate


@pytest.fixture
def repositories(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    migrate_database(database_path, tmp_path / "backups")

    goal_input = GoalCreate(
        period={
            "kind": "weekly",
            "start_date": "2026-11-09",
        },
        title="Learn LangChain",
        milestones=[
            {"description": "Complete the tutorial"},
            {"description": "Build a client"},
        ],
    )

    with open_database(database_path) as connection:
        goals = GoalRepository(connection)
        changes = GoalChangeRepository(connection)
        period_id = str(uuid4())
        goal_id = str(uuid4())

        with transaction(connection):
            goals.insert_period(period_id, goal_input.period)
            goals.insert_goal(
                goal_id=goal_id,
                period_id=period_id,
                goal=goal_input,
                effective_date=date(2026, 11, 9),
                is_original_plan=True,
            )

        yield SimpleNamespace(
            connection=connection,
            goals=goals,
            changes=changes,
            goal_id=goal_id,
            period_id=period_id,
        )


def test_missing_period_and_history(repositories):
    missing_id = str(uuid4())

    assert repositories.changes.get_period(missing_id) is None
    assert repositories.changes.get_goal_history(missing_id) == {
        "goal_revisions": [],
        "milestone_revisions": [],
    }


def test_goal_edit_preserves_original_revision(repositories):
    saved = repositories.goals.get_goal(repositories.goal_id)
    revised = {**saved, "title": "Build a LangChain project"}

    with transaction(repositories.connection):
        revision = repositories.changes.append_goal_revision(
            goal_id=repositories.goal_id,
            expected_revision=1,
            details=revised,
            effective_date=date(2026, 11, 10),
            reason="Make the outcome specific.",
        )

    current = repositories.goals.get_goal(repositories.goal_id)
    history = repositories.changes.get_goal_history(repositories.goal_id)

    assert revision == 2
    assert current["revision"] == 2
    assert current["title"] == "Build a LangChain project"
    assert current["is_original_plan"] is True

    assert [
        item["title"] for item in history["goal_revisions"]
    ] == ["Learn LangChain", "Build a LangChain project"]


def test_stale_goal_revision_is_rejected(repositories):
    details = repositories.goals.get_goal(repositories.goal_id)

    with transaction(repositories.connection):
        repositories.changes.append_goal_revision(
            goal_id=repositories.goal_id,
            expected_revision=1,
            details=details,
            effective_date=date(2026, 11, 10),
            reason="First edit.",
        )

    with pytest.raises(ConflictError):
        with transaction(repositories.connection):
            repositories.changes.append_goal_revision(
                goal_id=repositories.goal_id,
                expected_revision=1,
                details=details,
                effective_date=date(2026, 11, 11),
                reason="Outdated edit.",
            )

    history = repositories.changes.get_goal_history(repositories.goal_id)

    assert len(history["goal_revisions"]) == 2


def test_failed_revision_insert_restores_current_pointer(repositories):
    details = repositories.goals.get_goal(repositories.goal_id)
    details["title"] = ""

    with pytest.raises(sqlite3.IntegrityError):
        with transaction(repositories.connection):
            repositories.changes.append_goal_revision(
                goal_id=repositories.goal_id,
                expected_revision=1,
                details=details,
                effective_date=date(2026, 11, 10),
                reason="Invalid title.",
            )

    saved = repositories.goals.get_goal(repositories.goal_id)
    history = repositories.changes.get_goal_history(repositories.goal_id)

    assert saved["revision"] == 1
    assert saved["title"] == "Learn LangChain"
    assert len(history["goal_revisions"]) == 1


def test_added_milestone_is_not_in_creation_baseline(repositories):
    with transaction(repositories.connection):
        milestone_id = repositories.changes.insert_milestone(
            goal_id=repositories.goal_id,
            description="Write integration tests",
            due_date=date(2026, 11, 15),
            effective_date=date(2026, 11, 10),
            reason="Expand the plan.",
        )

    saved = repositories.goals.get_goal(repositories.goal_id)
    added = next(
        item for item in saved["milestones"]
        if item["id"] == milestone_id
    )

    assert len(saved["milestones"]) == 3
    assert added["is_baseline"] is False
    assert added["is_included"] is True
    assert added["revision"] == 1


def test_milestone_edit_preserves_original_revision(repositories):
    saved = repositories.goals.get_goal(repositories.goal_id)
    original = saved["milestones"][0]
    revised = {
        **original,
        "description": "Revised milestone",
        "is_included": False,
    }

    with transaction(repositories.connection):
        revision = repositories.changes.append_milestone_revision(
            milestone_id=original["id"],
            expected_revision=1,
            details=revised,
            effective_date=date(2026, 11, 10),
            reason="Remove from revised scope.",
        )

    history = repositories.changes.get_goal_history(repositories.goal_id)
    versions = [
        item for item in history["milestone_revisions"]
        if item["milestone_id"] == original["id"]
    ]

    assert revision == 2
    assert [item["revision"] for item in versions] == [1, 2]
    assert versions[0]["description"] == original["description"]
    assert versions[0]["is_included"] is True
    assert versions[1]["description"] == "Revised milestone"
    assert versions[1]["is_included"] is False
    assert all(item["is_baseline"] for item in versions)


def test_stale_milestone_revision_is_rejected(repositories):
    saved = repositories.goals.get_goal(repositories.goal_id)
    milestone = saved["milestones"][0]

    with transaction(repositories.connection):
        repositories.changes.append_milestone_revision(
            milestone_id=milestone["id"],
            expected_revision=1,
            details=milestone,
            effective_date=date(2026, 11, 10),
            reason="First edit.",
        )

    with pytest.raises(ConflictError):
        with transaction(repositories.connection):
            repositories.changes.append_milestone_revision(
                milestone_id=milestone["id"],
                expected_revision=1,
                details=milestone,
                effective_date=date(2026, 11, 11),
                reason="Outdated edit.",
            )


def test_initial_planning_can_be_finalized_only_once(repositories):
    first_timestamp = "2026-11-10T09:00:00Z"

    with transaction(repositories.connection):
        repositories.changes.finalize_period_planning(
            repositories.period_id,
            first_timestamp,
        )

    with pytest.raises(ConflictError):
        with transaction(repositories.connection):
            repositories.changes.finalize_period_planning(
                repositories.period_id,
                "2026-11-11T09:00:00Z",
            )

    period = repositories.changes.get_period(repositories.period_id)

    assert period["planning_closed_at"] == first_timestamp


@pytest.mark.parametrize(
    "operation",
    ["goal_revision", "milestone", "milestone_revision", "finalize"],
)
def test_writes_require_a_transaction(repositories, operation):
    saved = repositories.goals.get_goal(repositories.goal_id)
    milestone = saved["milestones"][0]

    actions = {
        "goal_revision": lambda: repositories.changes.append_goal_revision(
            goal_id=repositories.goal_id,
            expected_revision=1,
            details=saved,
            effective_date=date(2026, 11, 10),
            reason="Test edit.",
        ),
        "milestone": lambda: repositories.changes.insert_milestone(
            goal_id=repositories.goal_id,
            description="Additional milestone",
            due_date=date(2026, 11, 15),
            effective_date=date(2026, 11, 10),
            reason="Test addition.",
        ),
        "milestone_revision": lambda: (
            repositories.changes.append_milestone_revision(
                milestone_id=milestone["id"],
                expected_revision=1,
                details=milestone,
                effective_date=date(2026, 11, 10),
                reason="Test edit.",
            )
        ),
        "finalize": lambda: repositories.changes.finalize_period_planning(
            repositories.period_id,
            "2026-11-10T09:00:00Z",
        ),
    }

    with pytest.raises(RuntimeError, match="explicit transaction"):
        actions[operation]()


def test_failed_operation_rolls_back_all_changes(repositories):
    original = repositories.goals.get_goal(repositories.goal_id)

    with pytest.raises(RuntimeError, match="Abort operation"):
        with transaction(repositories.connection):
            repositories.changes.append_goal_revision(
                goal_id=repositories.goal_id,
                expected_revision=1,
                details={**original, "title": "Temporary title"},
                effective_date=date(2026, 11, 10),
                reason="Temporary edit.",
            )
            repositories.changes.insert_milestone(
                goal_id=repositories.goal_id,
                description="Temporary milestone",
                due_date=date(2026, 11, 15),
                effective_date=date(2026, 11, 10),
                reason="Temporary addition.",
            )
            repositories.changes.finalize_period_planning(
                repositories.period_id,
                "2026-11-10T09:00:00Z",
            )

            raise RuntimeError("Abort operation")

    assert repositories.goals.get_goal(repositories.goal_id) == original