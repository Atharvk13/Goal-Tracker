import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request

from goal_tracker.assist.proposals import ProposalStore
from goal_tracker.bootstrap import bootstrap
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.domain.periods import current_period_start
from goal_tracker.progress.workflow import load_all_pending, load_goal_lookup
from goal_tracker.reporting.periods import find_unreported_periods
from goal_tracker.web.dependencies import (
    get_analysis_service,
    get_context,
    get_goal_service,
    get_progress_service,
    get_report_service,
    templates,
    today,
)
from goal_tracker.web.assist_routes import router as assist_router
from goal_tracker.web.agent_routes import router as agent_router
from goal_tracker.web.chat_routes import router as chat_router
from goal_tracker.web.scheduler import run_scheduler
from goal_tracker.web.goal_edit_routes import router as goal_edit_router
from goal_tracker.web.goal_lifecycle_routes import router as goal_lifecycle_router
from goal_tracker.web.goal_routes import router as goal_router
from goal_tracker.web.mail_routes import router as mail_router
from goal_tracker.web.plan_routes import router as plan_router
from goal_tracker.web.progress_routes import router as progress_router
from goal_tracker.web.report_routes import router as report_router
from goal_tracker.web.summary_routes import router as summary_router


def create_app(env_file: str | None = None) -> FastAPI:
    """Create the web application using the existing startup process."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = bootstrap(env_file)
        task = None
        if app.state.context.settings.agent_schedule:
            task = asyncio.create_task(run_scheduler(app.state.context))
        yield
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Goal Tracker",
        lifespan=lifespan,
    )

    app.include_router(goal_router)
    app.include_router(assist_router)
    app.include_router(chat_router)
    app.include_router(agent_router)
    app.include_router(goal_edit_router)
    app.include_router(goal_lifecycle_router)
    app.include_router(plan_router)
    app.include_router(mail_router)
    app.include_router(progress_router)
    app.include_router(summary_router)
    app.include_router(report_router)

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        context = get_context(request)
        current = today(request)
        overview = {"error": None}

        try:
            goals = load_goal_lookup(get_goal_service(request)).values()
            week = PeriodSpec(
                kind=PeriodKind.WEEKLY,
                start_date=current_period_start(PeriodKind.WEEKLY, current),
                timezone=context.settings.timezone,
            )
            overview.update(
                active_goals=sum(
                    goal["lifecycle_status"] == "active" for goal in goals
                ),
                pending_count=len(
                    load_all_pending(get_progress_service(request), current)
                ),
                week=get_analysis_service(request).analyze_period(week),
                unreported=find_unreported_periods(get_report_service(request)),
            )
        except AppError as error:
            context.logger.warning(
                "Dashboard overview failed (%s).",
                type(error).__name__,
            )
            overview["error"] = "Some workspace figures are temporarily unavailable."

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "model_name": context.settings.llm_model,
                "timezone": context.settings.timezone,
                "today": current.isoformat(),
                "overview": overview,
                "jarvis_count": ProposalStore(context.paths.database).count_pending(),
            },
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
