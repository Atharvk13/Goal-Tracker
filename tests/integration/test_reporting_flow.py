from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.operations import calculate_payload_hash
from goal_tracker.domain.errors import ConflictError
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.goals.service import GoalService
from goal_tracker.llm.recommendations import (
    Recommendation,
    ReportRecommendations,
)
from goal_tracker.mcp.runtime import open_mcp_runtime
from goal_tracker.mcp.services import (
    MCPAnalysisService,
    MCPProgressService,
    MCPReportService,
)
from goal_tracker.progress.service import ProgressService


def _completion_entry(
    goal: dict,
    activity_date: date,
    milestone_index: int,
    minutes: int,
) -> DailyProgressInput:
    milestone = goal["milestones"][milestone_index]
    description = f"Completed: {milestone['description']}"

    return DailyProgressInput(
        activity_date=activity_date,
        raw_text=description,
        activities=[
            {
                "kind": "activity",
                "description": description,
                "state": "completed",
                "minutes": minutes,
                "goal_ids": [goal["id"]],
                "milestone_changes": [
                    {
                        "milestone_id": milestone["id"],
                        "state": "done",
                    }
                ],
            }
        ],
    )


def test_saved_reports_advice_and_pdf_remain_consistent(tmp_path):
    timezone = "Asia/Kolkata"
    today = datetime.now(ZoneInfo(timezone)).date()
    current_monday = today - timedelta(days=today.weekday())

    period = PeriodSpec(
        kind="weekly",
        start_date=current_monday - timedelta(days=7),
        timezone=timezone,
    )
    seed_date = period.start_date + timedelta(days=2)

    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    # Seed historical records with a controlled clock.
    goal = GoalService(
        paths.database,
        timezone=timezone,
        today=lambda: seed_date,
    ).create_goal(
        GoalCreate(
            period=period,
            title="Learn local agents",
            milestones=[
                {"description": "Complete the tutorial"},
                {"description": "Explain <schemas> & validation"},
            ],
        )
    )["goal"]

    ProgressService(
        paths.database,
        timezone=timezone,
        today=lambda: seed_date,
    ).record_progress(
        _completion_entry(goal, seed_date, 0, 20)
    )

    with open_mcp_runtime(
        paths.database,
        timezone=timezone,
    ) as runtime:
        analysis = MCPAnalysisService(runtime)
        progress = MCPProgressService(runtime)
        reports = MCPReportService(runtime)

        facts = analysis.analyze_period(
            period,
            as_of=period.end_date,
        )
        evidence_hash = calculate_payload_hash(facts)

        assert facts["revised_completion"] == 50.0

        # Simulate validated advice without invoking a model.
        advice = ReportRecommendations(
            areas_for_improvement=[
                Recommendation(
                    text="Practise the remaining validation milestone.",
                    evidence_ids=[
                        f"milestone:{goal['milestones'][1]['id']}"
                    ],
                )
            ],
            next_period_suggestions=[],
            recommended_goals=[],
        )

        request_id = uuid4()

        first = reports.generate_report(
            period,
            request_id=request_id,
            advisory=advice,
            model_provenance={
                "provider": "test",
                "model": "simulated-advice",
            },
            expected_evidence_hash=evidence_hash,
        )["report"]

        assert first["facts"]["revised_completion"] == 50.0
        assert first["facts"]["recorded_minutes"] == 20
        assert first["advisory"]["areas_for_improvement"]
        assert first["evidence_hash"] == evidence_hash

        first_pdf = reports.export_pdf(
            first["id"],
            paths.reports,
        )
        original_pdf_bytes = first_pdf.read_bytes()

        assert original_pdf_bytes.startswith(b"%PDF-")
        assert len(original_pdf_bytes) > 1000

        # A late entry changes newly calculated results.
        progress.record_progress(
            _completion_entry(goal, period.end_date, 1, 15)
        )

        # An exact request retry still returns its original snapshot.
        retried = reports.generate_report(
            period,
            request_id=request_id,
            advisory=advice,
            model_provenance={
                "provider": "test",
                "model": "simulated-advice",
            },
            expected_evidence_hash=evidence_hash,
        )["report"]

        assert retried["id"] == first["id"]

        # A new request cannot save advice against outdated facts.
        with pytest.raises(ConflictError, match="changed"):
            reports.generate_report(
                period,
                advisory=advice,
                expected_evidence_hash=evidence_hash,
            )

        second = reports.generate_report(period)["report"]

        assert second["revision"] == first["revision"] + 1
        assert second["facts"]["revised_completion"] == 100.0
        assert second["facts"]["recorded_minutes"] == 35

        original = reports.get_report(first["id"])

        assert original["facts"]["revised_completion"] == 50.0
        assert original["facts"]["recorded_minutes"] == 20

        repeated_pdf = reports.export_pdf(
            first["id"],
            paths.reports,
        )

        assert repeated_pdf == first_pdf
        assert repeated_pdf.read_bytes() == original_pdf_bytes

        second_pdf = reports.export_pdf(
            second["id"],
            paths.reports,
        )

        assert second_pdf != first_pdf
        assert second_pdf.read_bytes().startswith(b"%PDF-")