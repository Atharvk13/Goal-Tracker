from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from pydantic import ValidationError

from goal_tracker.domain.errors import AppError
from goal_tracker.domain.models import GoalCreate
from goal_tracker.goals.service import GoalService


router = APIRouter()

templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


def _form_response(
    request: Request,
    *,
    values: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
):
    context = request.app.state.context
    today = datetime.now(ZoneInfo(context.settings.timezone)).date()

    return templates.TemplateResponse(
        request=request,
        name="goal_form.html",
        context={
            "values": values or {},
            "errors": errors or [],
            "today": today.isoformat(),
        },
        status_code=status_code,
    )


@router.get("/goals/new", include_in_schema=False)
def new_goal(request: Request):
    return _form_response(request)


@router.post("/goals/review", include_in_schema=False)
def review_goal(
    request: Request,
    kind: Annotated[str, Form()],
    start_date: Annotated[str, Form()],
    title: Annotated[str, Form()],
    milestones: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    expected_outcome: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
):
    context = request.app.state.context

    values = {
        "kind": kind,
        "start_date": start_date,
        "title": title,
        "description": description,
        "expected_outcome": expected_outcome,
        "target_date": target_date,
        "milestones": milestones,
    }

    try:
        draft = GoalCreate.model_validate(
            {
                "period": {
                    "kind": kind,
                    "start_date": start_date,
                    "timezone": context.settings.timezone,
                },
                "title": title,
                "description": description,
                "expected_outcome": expected_outcome,
                "target_date": target_date.strip() or None,
                "milestones": [
                    {"description": line.strip()}
                    for line in milestones.splitlines()
                    if line.strip()
                ],
            }
        )
    except ValidationError as error:
        return _form_response(
            request,
            values=values,
            errors=[item["msg"] for item in error.errors()],
            status_code=422,
        )

    today = datetime.now(ZoneInfo(context.settings.timezone)).date()

    if draft.period.end_date < today:
        return _form_response(
            request,
            values=values,
            errors=["Choose the current period or a future period."],
            status_code=422,
        )

    return templates.TemplateResponse(
        request=request,
        name="goal_review.html",
        context={
            "draft": draft,
            "draft_json": draft.model_dump_json(),
            "error": None,
        },
    )


@router.post("/goals/save", include_in_schema=False)
def save_goal(
    request: Request,
    draft_json: Annotated[str, Form()],
):
    context = request.app.state.context

    try:
        draft = GoalCreate.model_validate_json(draft_json)
    except ValidationError:
        return _form_response(
            request,
            errors=["The goal draft is invalid. Please enter the goal again."],
            status_code=422,
        )

    service = GoalService(
        context.paths.database,
        timezone=context.settings.timezone,
    )

    try:
        service.create_goal(draft)
    except AppError as error:
        return templates.TemplateResponse(
            request=request,
            name="goal_review.html",
            context={
                "draft": draft,
                "draft_json": draft.model_dump_json(),
                "error": str(error),
            },
            status_code=400,
        )

    return RedirectResponse(url="/goals", status_code=303)