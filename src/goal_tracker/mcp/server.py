import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID, uuid4

from fastmcp import FastMCP

from goal_tracker.domain.enums import GoalLifecycle
from goal_tracker.domain.errors import AppError
from goal_tracker.goals.service import GoalService

from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import ValidationError

from goal_tracker.domain.goal_changes import (
    GoalLifecycleChange,
    GoalUpdate,
    PlanFinalize,
)
from goal_tracker.domain.models import GoalCreate

from goal_tracker.analysis.service import AnalysisService
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.progress.service import ProgressService

from goal_tracker.domain.enums import PeriodKind
from goal_tracker.llm.recommendations import ReportRecommendations
from goal_tracker.reporting.service import ReportService


def _execute(
    action: Callable[[], Any],
    request_id: str | None = None,
) -> dict:
    """Return a consistent response for service operations."""
    request_id = request_id or str(uuid4())

    try:
        data = action()

    except AppError as error:
        return {
            "ok": False,
            "request_id": error.request_id or request_id,
            "error": {
                "code": error.code,
                "message": error.message,
            },
        }

    except ValidationError:
        return {
            "ok": False,
            "request_id": request_id,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Tool input failed application validation.",
            },
        }

    return {
        "ok": True,
        "request_id": request_id,
        "data": data,
    }


def create_server(
    database_path: Path,
    timezone: str = "Asia/Kolkata",
) -> FastMCP:
    """Build an MCP server around the application's services."""
    database_path = database_path.expanduser()

    if not database_path.is_absolute():
        raise ValueError("The database path must be absolute.")

    if not database_path.is_file():
        raise ValueError(
            "The database is not initialized. "
            "Start Goal Tracker before connecting to MCP."
        )

    goals = GoalService(
        database_path,
        timezone=timezone,
    )
    progress = ProgressService(
        database_path,
        timezone=timezone,
    )
    analysis = AnalysisService(
        database_path,
        timezone=timezone,
    )
    reports = ReportService(
        database_path,
        timezone=timezone,
    )
    reports_directory = database_path.resolve().parent / "reports"

    server = FastMCP("Goal Tracker")

    @server.tool
    def list_goals(
        period_id: UUID | None = None,
        lifecycle_status: GoalLifecycle | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List saved goals with optional period and lifecycle filters."""
        request_id = str(uuid4())

        try:
            records = goals.list_goals(
                period_id=period_id,
                lifecycle_status=lifecycle_status,
                limit=limit,
                offset=offset,
            )

        except AppError as error:
            return {
                "ok": False,
                "request_id": error.request_id or request_id,
                "error": {
                    "code": error.code,
                    "message": error.message,
                },
            }

        return {
            "ok": True,
            "request_id": request_id,
            "data": records,
        }
    
    @server.tool
    def get_goal(goal_id: UUID) -> dict:
        """Retrieve a saved goal and its current milestone scope."""
        return _execute(
            lambda: goals.get_goal(goal_id)
        )

    @server.tool
    def create_goal(goal: GoalCreate) -> dict:
        """Create a goal from input already reviewed by the user."""
        return _execute(
            lambda: goals.create_goal(goal),
            request_id=str(goal.request_id),
        )

    @server.tool
    def update_goal(change: GoalUpdate) -> dict:
        """Revise a reviewed goal while preserving its original plan."""
        return _execute(
            lambda: goals.update_goal(change),
            request_id=str(change.request_id),
        )

    @server.tool
    def change_goal_lifecycle(
        change: GoalLifecycleChange,
    ) -> dict:
        """Cancel or archive a goal without deleting its history."""
        return _execute(
            lambda: goals.change_lifecycle(change),
            request_id=str(change.request_id),
        )

    @server.tool
    def delete_goal(
        goal_id: UUID,
        expected_revision: int,
        reason: str,
        request_id: UUID,
        effective_date: date | None = None,
    ) -> dict:
        """Cancel a goal; historical records are retained."""
        return _execute(
            lambda: goals.change_lifecycle(
                GoalLifecycleChange(
                    goal_id=goal_id,
                    expected_revision=expected_revision,
                    action="cancel",
                    reason=reason,
                    effective_date=effective_date,
                    request_id=request_id,
                )
            ),
            request_id=str(request_id),
        )

    @server.tool
    def finalize_plan(change: PlanFinalize) -> dict:
        """Close initial planning after the user confirms the plan."""
        return _execute(
            lambda: goals.finalize_plan(change),
            request_id=str(change.request_id),
        )

    @server.tool
    def get_goal_history(
        goal_id: UUID,
        audit_limit: int = 100,
        audit_offset: int = 0,
    ) -> dict:
        """Retrieve goal revisions and a page of audit history."""
        return _execute(
            lambda: goals.get_goal_history(
                goal_id,
                audit_limit=audit_limit,
                audit_offset=audit_offset,
            )
        )
    @server.tool
    def add_daily_progress(entry: DailyProgressInput) -> dict:
        """Save daily progress already reviewed and confirmed by the user."""
        return _execute(
            lambda: progress.record_progress(entry),
            request_id=str(entry.request_id),
        )

    @server.tool
    def get_daily_progress(
        start_date: date,
        end_date: date,
        goal_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Retrieve progress entries within an inclusive date range."""
        return _execute(
            lambda: progress.get_daily_progress(
                start_date,
                end_date,
                goal_id=goal_id,
                limit=limit,
                offset=offset,
            )
        )

    @server.tool
    def get_entry(entry_id: UUID) -> dict:
        """Retrieve a saved daily entry and its recorded activities."""
        return _execute(
            lambda: progress.get_entry(entry_id)
        )

    @server.tool
    def list_pending_activities(
        as_of: date | None = None,
        goal_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List work still pending or in progress at the selected cutoff."""
        return _execute(
            lambda: progress.list_pending_activities(
                as_of=as_of,
                goal_id=goal_id,
                limit=limit,
                offset=offset,
            )
        )

    @server.tool
    def list_recordable_goals(activity_date: date) -> dict:
        """List goals and milestones valid for an activity date."""
        return _execute(
            lambda: progress.list_recordable_goals(
                activity_date
            )
        )

    @server.tool
    def find_progress_duplicates(
        activity_date: date,
        raw_text: str,
    ) -> dict:
        """Find matching entries for explicit duplicate review."""
        return _execute(
            lambda: progress.find_duplicates(
                activity_date,
                raw_text,
            )
        )

    @server.tool
    def analyze_progress(
        period: PeriodSpec,
        as_of: date | None = None,
    ) -> dict:
        """Calculate factual period metrics from confirmed records."""
        return _execute(
            lambda: analysis.analyze_period(
                period,
                as_of=as_of,
            )
        )
    @server.tool
    def generate_progress_report(
        period: PeriodSpec,
        request_id: UUID,
        advisory: ReportRecommendations | None = None,
        model_provenance: dict | None = None,
        expected_evidence_hash: str | None = None,
    ) -> dict:
        """Save a completed-period report with optional reviewed advice."""
        return _execute(
            lambda: reports.generate_report(
                period,
                request_id=request_id,
                advisory=advisory,
                model_provenance=model_provenance,
                expected_evidence_hash=expected_evidence_hash,
            ),
            request_id=str(request_id),
        )

    @server.tool
    def get_report(report_id: UUID) -> dict:
        """Retrieve a saved report revision without recalculating it."""
        return _execute(
            lambda: reports.get_report(report_id)
        )

    @server.tool
    def list_reports(
        period_id: UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """List saved report revisions and their export status."""
        return _execute(
            lambda: reports.list_reports(
                period_id=period_id,
                limit=limit,
                offset=offset,
            )
        )

    @server.tool
    def get_completed_periods(
        kind: PeriodKind | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """List stored reporting periods that have ended."""
        return _execute(
            lambda: reports.list_completed_periods(
                kind=kind,
                limit=limit,
                offset=offset,
            )
        )

    @server.tool
    def export_report_pdf(report_id: UUID) -> dict:
        """Export a saved report to the application reports directory."""
        return _execute(
            lambda: {
                "path": str(
                    reports.export_pdf(
                        report_id,
                        reports_directory,
                    )
                )
            }
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Goal Tracker MCP server",
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="Absolute path to the initialized SQLite database.",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Kolkata",
    )
    arguments = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )

    server = create_server(
        arguments.database,
        timezone=arguments.timezone,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()