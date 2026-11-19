from datetime import date, timedelta

import pytest

from goal_tracker.analysis.service import AnalysisService
from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.migrate import migrate_database
from goal_tracker.domain.goal_changes import GoalLifecycleChange
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.goals.service import INDEPENDENT_GOAL_TITLE, GoalService
from goal_tracker.progress.service import ProgressService
from goal_tracker.progress.workflow import link_independent_work

TIMEZONE = "Asia/Kolkata"
WEDNESDAY = date(2026, 11, 18)
MONDAY = date(2026, 11, 16)
WEEK = PeriodSpec(kind="weekly", start_date=MONDAY, timezone=TIMEZONE)


@pytest.fixture
def services(tmp_path):
    paths = get_app_paths(tmp_path / "data")
    migrate_database(paths.database, paths.backups)
    clock = lambda: WEDNESDAY  # noqa: E731
    return (
        GoalService(paths.database, timezone=TIMEZONE, today=clock),
        ProgressService(paths.database, timezone=TIMEZONE, today=clock),
        AnalysisService(paths.database, timezone=TIMEZONE, today=clock),
    )


def _work(day: date, text: str, goal_ids=(), **activity) -> DailyProgressInput:
    return DailyProgressInput(
        activity_date=day,
        raw_text=text,
        activities=[
            {
                "description": text,
                "state": "completed",
                "goal_ids": list(goal_ids),
                **activity,
            }
        ],
    )


def test_one_shared_goal_per_week(services):
    goals, _, _ = services

    first = goals.ensure_independent_goal(WEEK)
    second = goals.ensure_independent_goal(WEEK)

    assert first["id"] == second["id"]
    assert first["title"] == INDEPENDENT_GOAL_TITLE
    assert first["is_independent"] is True
    assert first["is_original_plan"] is False
    assert first["effective_date"] == MONDAY.isoformat()
    assert len(goals.list_goals()) == 1


def test_earlier_days_of_the_week_can_link(services):
    goals, progress, _ = services

    draft = _work(MONDAY, "Answered support emails")
    activity_id = str(draft.activities[0].id)
    linked = link_independent_work(draft, {activity_id}, goals, TIMEZONE)

    saved = progress.record_progress(linked)["entry"]

    independent = goals.ensure_independent_goal(WEEK)
    assert saved["activities"][0]["goal_ids"] == [independent["id"]]


def test_ended_week_returns_none_and_work_stays_unlinked(services):
    goals, _, _ = services
    last_week = PeriodSpec(
        kind="weekly", start_date=MONDAY - timedelta(days=7), timezone=TIMEZONE
    )

    assert goals.ensure_independent_goal(last_week) is None

    draft = _work(MONDAY - timedelta(days=5), "Old admin work")
    linked = link_independent_work(
        draft, {str(draft.activities[0].id)}, goals, TIMEZONE
    )
    assert linked.activities[0].goal_ids == []


def test_inactive_independent_goal_is_not_reused(services):
    goals, _, _ = services
    goal = goals.ensure_independent_goal(WEEK)

    goals.change_lifecycle(
        GoalLifecycleChange(
            goal_id=goal["id"],
            expected_revision=goal["revision"],
            action="cancel",
            reason="Not needed",
        )
    )

    assert goals.ensure_independent_goal(WEEK) is None


def test_independent_goal_never_changes_completion(services):
    goals, progress, analysis = services
    planned = goals.create_goal(
        GoalCreate(
            period=WEEK,
            title="Ship feature",
            milestones=[{"description": "Build"}, {"description": "Test"}],
        )
    )["goal"]

    progress.record_progress(
        _work(
            WEDNESDAY,
            "Built it",
            goal_ids=[planned["id"]],
            milestone_changes=[
                {"milestone_id": planned["milestones"][0]["id"], "state": "done"}
            ],
        )
    )
    before = analysis.analyze_period(WEEK)

    draft = _work(WEDNESDAY, "Helped a colleague")
    progress.record_progress(
        link_independent_work(draft, {str(draft.activities[0].id)}, goals, TIMEZONE)
    )
    after = analysis.analyze_period(WEEK)

    assert before["revised_completion"] == after["revised_completion"] == 50.0
    assert after["revised_goal_count"] == before["revised_goal_count"] == 1
    assert after["planned_due"] == before["planned_due"]

    independent = next(g for g in after["goals"] if g["is_independent"])
    assert independent["completion_status"] == "independent"
    assert independent["revised_scored"] is False
    assert independent["overdue"] is False
    assert all(not day["scored_goals"] > 1 for day in after["completion_trend"])
