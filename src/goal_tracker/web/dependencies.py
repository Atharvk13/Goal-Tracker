"""Shared helpers for the web routes.

Routes obtain services through the same factories the terminal uses, so both
interfaces run identical business logic. The web context never starts an MCP
runtime, so the factories return the in-process services.
"""

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from goal_tracker.bootstrap import AppContext
from goal_tracker.domain.errors import NotFoundError
from goal_tracker.domain.periods import today_in
from goal_tracker.llm.gateway import ModelGateway
from goal_tracker.mcp import services as service_factories


TEMPLATE_DIRECTORY = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))

GOAL_NOT_FOUND = "The requested goal does not exist."


def _label(value) -> str:
    """Turn stored values such as in_progress into 'In Progress'."""
    if value is None:
        return ""
    return str(value).replace("_", " ").title()


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f}%"


def _when(value) -> str:
    """Show stored ISO timestamps as 'YYYY-MM-DD HH:MM UTC'."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
        return moment.strftime("%Y-%m-%d %H:%M UTC")
    return moment.strftime("%Y-%m-%d %H:%M")


templates.env.filters["label"] = _label
templates.env.filters["percent"] = _percent
templates.env.filters["when"] = _when


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def get_goal_service(request: Request):
    return service_factories.get_goal_service(get_context(request))


def get_progress_service(request: Request):
    return service_factories.get_progress_service(get_context(request))


def get_analysis_service(request: Request):
    return service_factories.get_analysis_service(get_context(request))


def get_report_service(request: Request):
    return service_factories.get_report_service(get_context(request))


def get_gateway(request: Request) -> ModelGateway:
    """Return the local model gateway; tests replace ModelGateway here."""
    return ModelGateway(get_context(request).settings)


def today(request: Request) -> date:
    """Return today's date in the configured timezone."""
    return today_in(get_context(request).settings.timezone)


def load_goal(service, goal_id: UUID | str) -> dict:
    """Return a goal or raise a 404 response."""
    try:
        return service.get_goal(goal_id)
    except NotFoundError as error:
        raise HTTPException(status_code=404, detail=GOAL_NOT_FOUND) from error


def validation_messages(error: ValidationError) -> list[str]:
    return [item["msg"] for item in error.errors()]
