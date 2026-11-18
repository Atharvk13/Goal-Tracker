from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from goal_tracker.bootstrap import bootstrap

from fastapi import Query

from goal_tracker.goals.service import GoalService

from goal_tracker.web.goal_routes import router as goal_router

TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))


def create_app(env_file: str | None = None) -> FastAPI:
    """Create the web application using the existing startup process."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = bootstrap(env_file)
        yield

    app = FastAPI(
        title="Goal Tracker",
        lifespan=lifespan,
    )
    app.include_router(goal_router)

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        context = request.app.state.context

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "model_name": context.settings.llm_model,
                "timezone": context.settings.timezone,
            },
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/goals", include_in_schema=False)
    def goals_page(
        request: Request,
        page: int = Query(default=1, ge=1),
    ):
        context = request.app.state.context
        page_size = 20

        service = GoalService(
            context.paths.database,
            timezone=context.settings.timezone,
        )

        goals = service.list_goals(
            limit=page_size + 1,
            offset=(page - 1) * page_size,
        )

        return templates.TemplateResponse(
            request=request,
            name="goals.html",
            context={
                "goals": goals[:page_size],
                "page": page,
                "has_next": len(goals) > page_size,
            },
        )    

    return app