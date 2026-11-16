from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.migrate import migrate_database
from goal_tracker.domain.errors import DomainValidationError
from goal_tracker.domain.goal_changes import GoalUpdate
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.mcp.runtime import open_mcp_runtime
from goal_tracker.mcp.services import (
    MCPAnalysisService,
    MCPGoalService,
    MCPProgressService,
    MCPReportService,
)


def test_goal_and_progress_round_trip_through_stdio(tmp_path):
    timezone = "Asia/Kolkata"
    today = datetime.now(ZoneInfo(timezone)).date()

    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    period = PeriodSpec(
        kind="weekly",
        start_date=today - timedelta(days=today.weekday()),
        timezone=timezone,
    )

    with open_mcp_runtime(
        paths.database,
        timezone=timezone,
    ) as runtime:
        goals = MCPGoalService(runtime)
        progress = MCPProgressService(runtime)
        analysis = MCPAnalysisService(runtime)
        reports = MCPReportService(runtime)

        created = goals.create_goal(
            GoalCreate(
                period=period,
                title="Learn local agents",
                expected_outcome="Build and understand a small agent.",
                target_date=period.end_date,
                milestones=[
                    {"description": "Complete the tutorial"},
                    {"description": "Build the example"},
                ],
            )
        )
        goal = created["goal"]

        # A title-only edit must preserve the existing deadline.
        edited = goals.update_goal(
            GoalUpdate(
                goal_id=goal["id"],
                expected_revision=goal["revision"],
                title="Build a local agent",
                reason="Make the title more specific.",
            )
        )
        goal = edited["goal"]

        assert goal["title"] == "Build a local agent"
        assert goal["target_date"] == period.end_date.isoformat()
        assert (
            goal["expected_outcome"]
            == "Build and understand a small agent."
        )

        # Explicit None must still mean "clear this deadline".
        cleared = goals.update_goal(
            GoalUpdate(
                goal_id=goal["id"],
                expected_revision=goal["revision"],
                target_date=None,
                reason="Use the period end instead of a goal deadline.",
            )
        )
        goal = cleared["goal"]

        assert goal["target_date"] is None

        # Verify the history tool accepts pagination arguments.
        history = goals.get_goal_history(
            goal["id"],
            audit_limit=1,
            audit_offset=0,
        )
        assert isinstance(history, dict)

        entry = DailyProgressInput(
            activity_date=today,
            raw_text="I completed the tutorial.",
            activities=[
                {
                    "kind": "activity",
                    "description": "Completed the tutorial",
                    "state": "completed",
                    "minutes": 25,
                    "goal_ids": [goal["id"]],
                    "milestone_changes": [
                        {
                            "milestone_id": (
                                goal["milestones"][0]["id"]
                            ),
                            "state": "done",
                        }
                    ],
                }
            ],
        )

        first_save = progress.record_progress(entry)

        # Reusing the request must not create another entry.
        repeated_save = progress.record_progress(entry)
        assert repeated_save == first_save

        saved_entry = progress.get_entry(
            first_save["entry"]["id"]
        )
        assert saved_entry["raw_text"] == entry.raw_text

        entries = progress.get_daily_progress(today, today)
        assert len(entries) == 1

        facts = analysis.analyze_period(period)

        assert facts["original_completion"] == 50.0
        assert facts["revised_completion"] == 50.0
        assert facts["recorded_minutes"] == 25
        assert facts["recorded_days"] == 1

        # An unfinished reporting period must still be rejected.
        with pytest.raises(
            DomainValidationError,
            match="after the period has ended",
        ):
            reports.generate_report(period)

    # Reopen the connection to verify persistent data.
    with open_mcp_runtime(
        paths.database,
        timezone=timezone,
    ) as runtime:
        saved_goal = MCPGoalService(runtime).get_goal(goal["id"])

        assert saved_goal["title"] == "Build a local agent"
        assert saved_goal["target_date"] is None