from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goal_tracker.domain.errors import AppError, ConflictError
from goal_tracker.domain.goal_changes import PlanFinalize
from goal_tracker.web.dependencies import (
    get_goal_service,
    load_goal,
    templates,
)


router = APIRouter()


def _review_response(
    request: Request,
    goal: dict,
    draft: PlanFinalize,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="plan_review.html",
        context={
            "goal": goal,
            "draft_json": draft.model_dump_json(),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/goals/{goal_id}/plan", include_in_schema=False)
def review_plan_finalization(request: Request, goal_id: UUID):
    goal = load_goal(get_goal_service(request), goal_id)
    draft = PlanFinalize(period_id=goal["period"]["id"])

    if goal["period"]["planning_closed_at"] is not None:
        return _review_response(
            request,
            goal,
            draft,
            error="This period's initial plan is already finalized.",
            status_code=409,
        )

    return _review_response(request, goal, draft)


@router.post("/goals/{goal_id}/plan/save", include_in_schema=False)
def save_plan_finalization(
    request: Request,
    goal_id: UUID,
    draft_json: Annotated[str, Form()],
):
    try:
        draft = PlanFinalize.model_validate_json(draft_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="The planning draft is invalid. Reopen the review page.",
        ) from error

    service = get_goal_service(request)
    goal = load_goal(service, goal_id)

    if str(draft.period_id) != goal["period"]["id"]:
        raise HTTPException(
            status_code=422,
            detail="The draft belongs to a different period.",
        )

    try:
        service.finalize_plan(draft)
    except ConflictError as error:
        return _review_response(
            request,
            goal,
            draft,
            error=str(error),
            status_code=409,
        )
    except AppError as error:
        return _review_response(
            request,
            goal,
            draft,
            error=str(error),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/goals/{goal_id}",
        status_code=303,
    )