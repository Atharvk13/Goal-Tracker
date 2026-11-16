from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from goal_tracker.domain.errors import StorageError
from goal_tracker.domain.goal_changes import (
    GoalLifecycleChange,
    GoalUpdate,
    PlanFinalize,
)
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.llm.recommendations import ReportRecommendations
from goal_tracker.mcp.runtime import MCPRuntime
from goal_tracker.bootstrap import AppContext


class MCPGoalService:
    """Goal-service interface backed by MCP tools."""

    def __init__(self, runtime: MCPRuntime):
        self.runtime = runtime

    def create_goal(self, goal: GoalCreate) -> dict:
        return self.runtime.call(
            "create_goal",
            {"goal": goal},
        )

    def get_goal(self, goal_id: str | UUID) -> dict:
        return self.runtime.call(
            "get_goal",
            {"goal_id": goal_id},
        )

    def list_goals(self, **filters) -> list[dict]:
        return self.runtime.call(
            "list_goals",
            filters,
        )

    def update_goal(self, change: GoalUpdate) -> dict:
        payload = change.model_dump(
            mode="json",
            exclude_unset=True,
        )
        payload["request_id"] = str(change.request_id)

        return self.runtime.call(
            "update_goal",
            {"change": payload},
        )

    def change_lifecycle(
        self,
        change: GoalLifecycleChange,
    ) -> dict:
        return self.runtime.call(
            "change_goal_lifecycle",
            {"change": change},
        )

    def finalize_plan(self, change: PlanFinalize) -> dict:
        return self.runtime.call(
            "finalize_plan",
            {"change": change},
        )

    def get_goal_history(
        self,
        goal_id: str | UUID,
        **pagination,
    ) -> dict:
        return self.runtime.call(
            "get_goal_history",
            {
                "goal_id": goal_id,
                **pagination,
            },
        )


class MCPProgressService:
    """Progress-service interface backed by MCP tools."""

    def __init__(self, runtime: MCPRuntime):
        self.runtime = runtime

    def record_progress(self, entry: DailyProgressInput) -> dict:
        return self.runtime.call(
            "add_daily_progress",
            {"entry": entry},
        )

    def get_entry(self, entry_id: str | UUID) -> dict:
        return self.runtime.call(
            "get_entry",
            {"entry_id": entry_id},
        )

    def get_daily_progress(
        self,
        start_date: date,
        end_date: date,
        **filters,
    ) -> list[dict]:
        return self.runtime.call(
            "get_daily_progress",
            {
                "start_date": start_date,
                "end_date": end_date,
                **filters,
            },
        )

    def list_pending_activities(self, **filters) -> list[dict]:
        return self.runtime.call(
            "list_pending_activities",
            filters,
        )

    def list_recordable_goals(
        self,
        activity_date: date,
    ) -> list[dict]:
        return self.runtime.call(
            "list_recordable_goals",
            {"activity_date": activity_date},
        )

    def find_duplicates(
        self,
        activity_date: date,
        raw_text: str,
    ) -> list[dict]:
        return self.runtime.call(
            "find_progress_duplicates",
            {
                "activity_date": activity_date,
                "raw_text": raw_text,
            },
        )


class MCPAnalysisService:
    """Analysis-service interface backed by MCP tools."""

    def __init__(self, runtime: MCPRuntime):
        self.runtime = runtime

    def analyze_period(
        self,
        period: PeriodSpec,
        *,
        as_of: date | None = None,
    ) -> dict:
        return self.runtime.call(
            "analyze_progress",
            {
                "period": period,
                "as_of": as_of,
            },
        )


class MCPReportService:
    """Report-service interface backed by MCP tools."""

    def __init__(self, runtime: MCPRuntime):
        self.runtime = runtime

    def generate_report(
        self,
        period: PeriodSpec,
        *,
        request_id: str | UUID | None = None,
        advisory: ReportRecommendations | None = None,
        model_provenance: dict | None = None,
        expected_evidence_hash: str | None = None,
    ) -> dict:
        return self.runtime.call(
            "generate_progress_report",
            {
                "period": period,
                "request_id": (
                    request_id
                    if request_id is not None
                    else uuid4()
                ),
                "advisory": advisory,
                "model_provenance": model_provenance,
                "expected_evidence_hash": expected_evidence_hash,
            },
        )

    def get_report(self, report_id: str | UUID) -> dict:
        return self.runtime.call(
            "get_report",
            {"report_id": report_id},
        )

    def list_reports(self, **filters) -> list[dict]:
        return self.runtime.call(
            "list_reports",
            filters,
        )

    def list_completed_periods(self, **filters) -> list[dict]:
        return self.runtime.call(
            "get_completed_periods",
            filters,
        )

    def export_pdf(
        self,
        report_id: str | UUID,
        output_directory: Path,
    ) -> Path:
        result = self.runtime.call(
            "export_report_pdf",
            {"report_id": report_id},
        )
        path = Path(result["path"])

        if (
            not path.is_absolute()
            or path.resolve().parent
            != output_directory.expanduser().resolve()
        ):
            raise StorageError(
                "The server returned a PDF location outside "
                "the configured reports directory."
            )

        return path

def get_goal_service(context: AppContext):
    runtime = getattr(context, "mcp", None)

    if runtime is not None:
        return MCPGoalService(runtime)

    from goal_tracker.goals.service import GoalService

    return GoalService(
        context.paths.database,
        timezone=context.settings.timezone,
    )


def get_progress_service(context: AppContext):
    runtime = getattr(context, "mcp", None)

    if runtime is not None:
        return MCPProgressService(runtime)

    from goal_tracker.progress.service import ProgressService

    return ProgressService(
        context.paths.database,
        timezone=context.settings.timezone,
    )


def get_analysis_service(context: AppContext):
    runtime = getattr(context, "mcp", None)

    if runtime is not None:
        return MCPAnalysisService(runtime)

    from goal_tracker.analysis.service import AnalysisService

    return AnalysisService(
        context.paths.database,
        timezone=context.settings.timezone,
    )


def get_report_service(context: AppContext):
    runtime = getattr(context, "mcp", None)

    if runtime is not None:
        return MCPReportService(runtime)

    from goal_tracker.reporting.service import ReportService

    return ReportService(
        context.paths.database,
        timezone=context.settings.timezone,
    )