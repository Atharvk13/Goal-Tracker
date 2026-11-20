from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goal_tracker.domain.errors import AppError
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.web.assist_routes import goal_suggestions
from goal_tracker.web.dependencies import (
    get_context,
    get_gateway,
    get_goal_service,
    load_goal,
    templates,
    today,
    validation_messages,
)


router = APIRouter()


def _form_response(
    request: Request,
    *,
    values: dict | None = None,
    errors: list[str] | None = None,
    notice: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="goal_form.html",
        context={
            "values": values or {},
            "errors": errors or [],
            "notice": notice,
            "today": today(request).isoformat(),
        },
        status_code=status_code,
    )


def _review_response(
    request: Request,
    draft: GoalCreate,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="goal_review.html",
        context={
            "draft": draft,
            "draft_json": draft.model_dump_json(),
            "error": error,
        },
        status_code=status_code,
    )


GOALS_PAGE_SIZE = 20


@router.get("/goals", include_in_schema=False)
def goals_page(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
):
    goals = get_goal_service(request).list_goals(
        limit=GOALS_PAGE_SIZE + 1,
        offset=(page - 1) * GOALS_PAGE_SIZE,
    )

    return templates.TemplateResponse(
        request=request,
        name="goals.html",
        context={
            "goals": goals[:GOALS_PAGE_SIZE],
            "page": page,
            "has_next": len(goals) > GOALS_PAGE_SIZE,
        },
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
    context = get_context(request)

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
            errors=validation_messages(error),
            status_code=422,
        )

    if draft.period.end_date < today(request):
        return _form_response(
            request,
            values=values,
            errors=["Choose the current period or a future period."],
            status_code=422,
        )

    return _review_response(request, draft)


@router.post("/goals/suggest", include_in_schema=False)
def suggest_milestones(
    request: Request,
    kind: Annotated[str, Form()],
    start_date: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
    milestones: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    expected_outcome: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
):
    """Append reviewed-before-saving AI milestone ideas to the form."""
    values = {
        "kind": kind,
        "start_date": start_date,
        "title": title,
        "description": description,
        "expected_outcome": expected_outcome,
        "target_date": target_date,
        "milestones": milestones,
    }

    if not title.strip():
        return _form_response(
            request,
            values=values,
            errors=["Enter a goal title before asking for suggestions."],
            status_code=422,
        )

    try:
        period = PeriodSpec(
            kind=kind,
            start_date=start_date,
            timezone=get_context(request).settings.timezone,
        )
    except ValidationError as error:
        return _form_response(
            request,
            values=values,
            errors=validation_messages(error),
            status_code=422,
        )

    try:
        proposal = get_gateway(request).suggest_milestones(
            period=period,
            title=title.strip(),
            description=description.strip(),
            expected_outcome=expected_outcome.strip(),
        )
    except Exception as error:
        get_context(request).logger.warning(
            "Milestone suggestions failed (%s).",
            type(error).__name__,
        )
        return _form_response(
            request,
            values=values,
            errors=[
                f"AI planning is unavailable ({type(error).__name__}). "
                "Continue with manual milestones."
            ],
            status_code=503,
        )

    existing = [line.strip() for line in milestones.splitlines() if line.strip()]
    seen = {line.casefold() for line in existing}
    added = []

    for suggestion in proposal.milestones:
        cleaned = suggestion.strip()

        if cleaned and cleaned.casefold() not in seen:
            added.append(cleaned)
            seen.add(cleaned.casefold())

    values["milestones"] = "\n".join(existing + added)

    notice = (
        f"Added {len(added)} AI suggestion(s). Edit or delete any line "
        "before reviewing; nothing is saved yet."
        if added
        else "The model did not suggest any new milestones."
    )

    return _form_response(request, values=values, notice=notice)


@router.post("/goals/save", include_in_schema=False)
def save_goal(
    request: Request,
    draft_json: Annotated[str, Form()],
):
    try:
        draft = GoalCreate.model_validate_json(draft_json)
    except ValidationError:
        return _form_response(
            request,
            errors=["The goal draft is invalid. Please enter the goal again."],
            status_code=422,
        )

    try:
        created = get_goal_service(request).create_goal(draft)
    except AppError as error:
        return _review_response(
            request,
            draft,
            error=str(error),
            status_code=400,
        )

    # Open the new goal; its page asks the model for suggestions.
    return RedirectResponse(
        url=f"/goals/{created['goal']['id']}?suggest=1",
        status_code=303,
    )


@router.get("/goals/{goal_id}", include_in_schema=False)
def goal_details(request: Request, goal_id: UUID):
    goal = load_goal(get_goal_service(request), goal_id)

    return templates.TemplateResponse(
        request=request,
        name="goal_details.html",
        context={
            "goal": goal,
            "ai": goal_suggestions(request, goal),
            "auto_suggest": request.query_params.get("suggest") == "1",
        },
    )
