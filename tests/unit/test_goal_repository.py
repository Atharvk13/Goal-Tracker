from uuid import uuid4

import pytest

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.goals import GoalRepository
from goal_tracker.domain.enums import GoalLifecycle
from goal_tracker.domain.models import GoalCreate


@pytest.fixture
def repository(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    migrate_database(database_path, tmp_path / "backups")

    with open_database(database_path) as connection:
        yield GoalRepository(connection)


@pytest.fixture
def goal_input():
    return GoalCreate(
        period={
            "kind": "weekly",
            "start_date": "2026-11-09",
            "timezone": "Asia/Kolkata",
        },
        title="Learn LangChain",
        description="Practise local model integration.",
        expected_outcome="Build a working example.",
        milestones=[
            {
                "description": "Complete the tutorial",
                "due_date": "2026-11-10",
            },
            {
                "description": "Build a client",
            },
        ],
    )


def save_goal(repository, goal, *, is_original_plan=True):
    """Test setup helper; business rules will live in the service."""
    goal_id = str(uuid4())

    with transaction(repository.connection):
        period = repository.find_period(goal.period)

        if period is None:
            period_id = str(uuid4())
            repository.insert_period(period_id, goal.period)
        else:
            period_id = period["id"]

        repository.insert_goal(
            goal_id=goal_id,
            period_id=period_id,
            goal=goal,
            effective_date=goal.period.start_date,
            is_original_plan=is_original_plan,
        )

    return goal_id


def test_missing_records_return_none(repository, goal_input):
    assert repository.find_period(goal_input.period) is None
    assert repository.get_goal(str(uuid4())) is None
    assert repository.list_goals() == []


@pytest.mark.parametrize("is_original_plan", [True, False])
def test_saves_goal_and_initial_milestones(
    repository,
    goal_input,
    is_original_plan,
):
    goal_id = save_goal(
        repository,
        goal_input,
        is_original_plan=is_original_plan,
    )

    goal = repository.get_goal(goal_id)

    assert goal is not None
    assert goal["id"] == goal_id
    assert goal["title"] == "Learn LangChain"
    assert goal["description"] == goal_input.description
    assert goal["expected_outcome"] == goal_input.expected_outcome
    assert goal["revision"] == 1
    assert goal["lifecycle_status"] == "active"
    assert goal["is_original_plan"] is is_original_plan
    assert goal["period"]["start_date"] == "2026-11-09"
    assert goal["period"]["end_date"] == "2026-11-15"

    assert len(goal["milestones"]) == 2
    assert {
        milestone["description"]
        for milestone in goal["milestones"]
    } == {"Complete the tutorial", "Build a client"}

    assert all(
        milestone["is_baseline"] and milestone["is_included"]
        for milestone in goal["milestones"]
    )


@pytest.mark.parametrize(
    ("target_date", "expected_due_date"),
    [
        (None, "2026-11-15"),
        ("2026-11-12", "2026-11-12"),
    ],
)
def test_unspecified_milestone_deadline_uses_goal_or_period_end(
    repository,
    goal_input,
    target_date,
    expected_due_date,
):
    data = goal_input.model_dump()
    data["target_date"] = target_date
    goal = GoalCreate.model_validate(data)

    goal_id = save_goal(repository, goal)
    saved = repository.get_goal(goal_id)

    milestones = {
        milestone["description"]: milestone
        for milestone in saved["milestones"]
    }

    assert milestones["Build a client"]["due_date"] == expected_due_date
    assert milestones["Complete the tutorial"]["due_date"] == "2026-11-10"


def test_list_filters_by_period(repository, goal_input):
    weekly_id = save_goal(repository, goal_input)

    monthly_input = GoalCreate(
        period={
            "kind": "monthly",
            "start_date": "2026-11-01",
        },
        title="Monthly learning",
        milestones=[{"description": "Finish a project"}],
    )
    monthly_id = save_goal(repository, monthly_input)

    weekly = repository.get_goal(weekly_id)

    results = repository.list_goals(
        period_id=weekly["period"]["id"]
    )

    assert {goal["id"] for goal in results} == {weekly_id}
    assert {goal["id"] for goal in repository.list_goals()} == {
        weekly_id,
        monthly_id,
    }


def test_reads_current_goal_revision_and_filters_lifecycle(
    repository,
    goal_input,
):
    goal_id = save_goal(repository, goal_input)

    with transaction(repository.connection):
        repository.connection.execute(
            """
            INSERT INTO goal_revisions (
                goal_id,
                revision,
                title,
                description,
                expected_outcome,
                target_date,
                lifecycle_status,
                effective_date,
                change_reason
            )
            SELECT
                goal_id,
                2,
                title,
                description,
                expected_outcome,
                target_date,
                'cancelled',
                '2026-11-10',
                'Test cancellation'
            FROM goal_revisions
            WHERE goal_id = ? AND revision = 1
            """,
            (goal_id,),
        )
        repository.connection.execute(
            "UPDATE goals SET current_revision = 2 WHERE id = ?",
            (goal_id,),
        )

    saved = repository.get_goal(goal_id)

    assert saved["revision"] == 2
    assert saved["lifecycle_status"] == "cancelled"
    assert repository.list_goals(
        lifecycle_status=GoalLifecycle.ACTIVE
    ) == []
    assert [
        goal["id"]
        for goal in repository.list_goals(
            lifecycle_status=GoalLifecycle.CANCELLED
        )
    ] == [goal_id]

    original = repository.connection.execute(
        """
        SELECT lifecycle_status
        FROM goal_revisions
        WHERE goal_id = ? AND revision = 1
        """,
        (goal_id,),
    ).fetchone()

    assert original["lifecycle_status"] == "active"


def test_reads_latest_milestone_revision(repository, goal_input):
    goal_id = save_goal(repository, goal_input)
    saved = repository.get_goal(goal_id)
    milestone_id = saved["milestones"][0]["id"]

    with transaction(repository.connection):
        repository.connection.execute(
            """
            INSERT INTO milestone_revisions (
                milestone_id,
                revision,
                description,
                due_date,
                is_included,
                effective_date,
                change_reason
            )
            VALUES (?, 2, ?, ?, 0, ?, ?)
            """,
            (
                milestone_id,
                "Revised milestone",
                "2026-11-11",
                "2026-11-10",
                "Test scope change",
            ),
        )

    updated = repository.get_goal(goal_id)
    milestone = next(
        item
        for item in updated["milestones"]
        if item["id"] == milestone_id
    )

    assert milestone["revision"] == 2
    assert milestone["description"] == "Revised milestone"
    assert milestone["is_included"] is False
    assert milestone["is_baseline"] is True


@pytest.mark.parametrize(
    "pagination",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_rejects_invalid_pagination(repository, pagination):
    with pytest.raises(ValueError, match="Limit must be"):
        repository.list_goals(**pagination)


def test_pagination_returns_separate_pages(repository, goal_input):
    saved_ids = {
        save_goal(repository, goal_input)
        for _ in range(3)
    }

    first_page = repository.list_goals(limit=2, offset=0)
    second_page = repository.list_goals(limit=2, offset=2)

    first_ids = {goal["id"] for goal in first_page}
    second_ids = {goal["id"] for goal in second_page}

    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == saved_ids


def test_writes_require_explicit_transaction(repository, goal_input):
    with pytest.raises(RuntimeError, match="explicit transaction"):
        repository.insert_period(str(uuid4()), goal_input.period)

    with pytest.raises(RuntimeError, match="explicit transaction"):
        repository.insert_goal(
            goal_id=str(uuid4()),
            period_id=str(uuid4()),
            goal=goal_input,
            effective_date=goal_input.period.start_date,
            is_original_plan=True,
        )

    assert repository.list_goals() == []


def test_failed_transaction_leaves_no_partial_goal(repository, goal_input):
    period_id = str(uuid4())
    goal_id = str(uuid4())

    with pytest.raises(RuntimeError, match="Abort operation"):
        with transaction(repository.connection):
            repository.insert_period(period_id, goal_input.period)
            repository.insert_goal(
                goal_id=goal_id,
                period_id=period_id,
                goal=goal_input,
                effective_date=goal_input.period.start_date,
                is_original_plan=True,
            )
            raise RuntimeError("Abort operation")

    assert repository.get_goal(goal_id) is None
    assert repository.find_period(goal_input.period) is None