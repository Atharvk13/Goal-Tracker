from datetime import date

import pytest
from pydantic import ValidationError

from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.models import GoalCreate, PeriodSpec


@pytest.fixture
def goal_data():
    """Return fresh, valid input for each goal test."""
    return {
        "period": {
            "kind": "weekly",
            "start_date": "2026-11-09",
            "timezone": "Asia/Kolkata",
        },
        "title": "Learn LangChain",
        "description": "Practise local model integration.",
        "expected_outcome": "Build a working example.",
        "milestones": [
            {"description": "Complete the tutorial"},
        ],
    }


@pytest.mark.parametrize(
    ("start_date", "expected_end"),
    [
        ("2026-11-09", date(2026, 11, 15)),
        ("2026-12-28", date(2027, 1, 3)),
    ],
)
def test_weekly_period_boundaries(start_date, expected_end):
    period = PeriodSpec(
        kind=PeriodKind.WEEKLY,
        start_date=start_date,
    )

    assert period.end_date == expected_end


@pytest.mark.parametrize(
    ("start_date", "expected_end"),
    [
        ("2026-02-01", date(2026, 2, 28)),
        ("2028-02-01", date(2028, 2, 29)),
        ("2026-12-01", date(2026, 12, 31)),
    ],
)
def test_monthly_period_boundaries(start_date, expected_end):
    period = PeriodSpec(
        kind=PeriodKind.MONTHLY,
        start_date=start_date,
    )

    assert period.end_date == expected_end


@pytest.mark.parametrize(
    ("kind", "start_date"),
    [
        ("weekly", "2026-11-10"),
        ("monthly", "2026-11-02"),
    ],
)
def test_rejects_non_calendar_start(kind, start_date):
    with pytest.raises(ValidationError, match="must start"):
        PeriodSpec(kind=kind, start_date=start_date)


def test_rejects_unknown_timezone():
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        PeriodSpec(
            kind="weekly",
            start_date="2026-11-09",
            timezone="Not/A_Real_Timezone",
        )


@pytest.mark.parametrize("title", ["", "   "])
def test_rejects_empty_goal_title(goal_data, title):
    goal_data["title"] = title

    with pytest.raises(ValidationError, match="title"):
        GoalCreate.model_validate(goal_data)


def test_requires_at_least_one_milestone(goal_data):
    goal_data["milestones"] = []

    with pytest.raises(ValidationError, match="milestones"):
        GoalCreate.model_validate(goal_data)


def test_rejects_blank_milestone_description(goal_data):
    goal_data["milestones"] = [{"description": "   "}]

    with pytest.raises(ValidationError, match="description"):
        GoalCreate.model_validate(goal_data)


def test_rejects_duplicate_milestones_after_normalization(goal_data):
    goal_data["milestones"] = [
        {"description": "Build a client"},
        {"description": "  BUILD A CLIENT  "},
    ]

    with pytest.raises(ValidationError, match="must be unique"):
        GoalCreate.model_validate(goal_data)


@pytest.mark.parametrize(
    "target_date",
    ["2026-11-08", "2026-11-16"],
)
def test_rejects_goal_deadline_outside_period(goal_data, target_date):
    goal_data["target_date"] = target_date

    with pytest.raises(ValidationError, match="target date"):
        GoalCreate.model_validate(goal_data)


@pytest.mark.parametrize(
    ("due_date", "target_date"),
    [
        ("2026-11-08", None),
        ("2026-11-16", None),
        ("2026-11-13", "2026-11-12"),
    ],
)
def test_rejects_invalid_milestone_deadline(
    goal_data,
    due_date,
    target_date,
):
    goal_data["target_date"] = target_date
    goal_data["milestones"] = [
        {
            "description": "Complete the tutorial",
            "due_date": due_date,
        }
    ]

    with pytest.raises(ValidationError, match="Milestone due dates"):
        GoalCreate.model_validate(goal_data)


def test_accepts_inclusive_deadline_boundaries(goal_data):
    goal_data["target_date"] = "2026-11-12"
    goal_data["milestones"] = [
        {
            "description": "Start the tutorial",
            "due_date": "2026-11-09",
        },
        {
            "description": "Finish the tutorial",
            "due_date": "2026-11-12",
        },
    ]

    goal = GoalCreate.model_validate(goal_data)

    assert goal.target_date == date(2026, 11, 12)
    assert goal.milestones[0].due_date == date(2026, 11, 9)
    assert goal.milestones[1].due_date == date(2026, 11, 12)


def test_json_round_trip_preserves_request_identity(goal_data):
    original = GoalCreate.model_validate(goal_data)

    restored = GoalCreate.model_validate_json(
        original.model_dump_json()
    )

    assert restored.request_id == original.request_id
    assert restored.period.kind == PeriodKind.WEEKLY
    assert restored.period.start_date == date(2026, 11, 9)
    assert restored.model_dump() == original.model_dump()


def test_rejects_unexpected_completion_field(goal_data):
    goal_data["completion_percentage"] = 100

    with pytest.raises(ValidationError, match="completion_percentage"):
        GoalCreate.model_validate(goal_data)