from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goal_tracker.domain.errors import AppError, ConflictError, NotFoundError
from goal_tracker.domain.goal_changes import GoalLifecycleChange
from goal_tracker.web.dependencies import (
    GOAL_NOT_FOUND,
    get_goal_service,
    load_goal,
    templates,
    validation_messages,
)


router = APIRouter()


def _form_response(
    request: Request,
    goal: dict,
    *,
    values: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            "expected_revision": goal["revision"],
            "action": "archive",
            "reason": "",
            "effective_date": "",
        }

    return templates.TemplateResponse(
        request=request,
        name="goal_lifecycle.html",
        context={
            "goal": goal,
            "values": values,
            "errors": errors or [],
        },
        status_code=status_code,
    )


def _review_response(
    request: Request,
    draft: GoalLifecycleChange,
    goal: dict,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="goal_lifecycle_review.html",
        context={
            "goal": goal,
            "draft": draft,
            "draft_json": draft.model_dump_json(),
            "target_status": (
                "cancelled" if draft.action == "cancel" else "archived"
            ),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/goals/{goal_id}/lifecycle", include_in_schema=False)
def lifecycle_form(request: Request, goal_id: UUID):
    goal = load_goal(get_goal_service(request), goal_id)
    return _form_response(request, goal)


@router.post("/goals/{goal_id}/lifecycle/review", include_in_schema=False)
def review_lifecycle(
    request: Request,
    goal_id: UUID,
    expected_revision: Annotated[int, Form()],
    action: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    effective_date: Annotated[str, Form()] = "",
):
    goal = load_goal(get_goal_service(request), goal_id)

    values = {
        "expected_revision": expected_revision,
        "action": action,
        "reason": reason,
        "effective_date": effective_date,
    }

    errors = []

    if expected_revision != goal["revision"]:
        errors.append(
            "This goal changed after you opened the form. "
            "Reload the latest goal before continuing."
        )
    elif goal["lifecycle_status"] == "archived":
        errors.append("The goal is already archived.")
    elif action == "cancel" and goal["lifecycle_status"] == "cancelled":
        errors.append("The goal is already cancelled.")

    if errors:
        return _form_response(
            request,
            goal,
            values=values,
            errors=errors,
            status_code=409,
        )

    try:
        draft = GoalLifecycleChange(
            goal_id=goal_id,
            expected_revision=expected_revision,
            action=action,
            reason=reason,
            effective_date=effective_date.strip() or None,
        )
    except ValidationError as error:
        return _form_response(
            request,
            goal,
            values=values,
            errors=validation_messages(error),
            status_code=422,
        )

    return _review_response(request, draft, goal)


@router.post("/goals/{goal_id}/lifecycle/save", include_in_schema=False)
def save_lifecycle(
    request: Request,
    goal_id: UUID,
    draft_json: Annotated[str, Form()],
):
    try:
        draft = GoalLifecycleChange.model_validate_json(draft_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="The status-change draft is invalid. Please reopen the form.",
        ) from error

    if draft.goal_id != goal_id:
        raise HTTPException(
            status_code=422,
            detail="The draft belongs to a different goal.",
        )

    service = get_goal_service(request)

    try:
        service.change_lifecycle(draft)
    except NotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=GOAL_NOT_FOUND,
        ) from error
    except ConflictError as error:
        goal = load_goal(service, goal_id)
        return _review_response(
            request,
            draft,
            goal,
            error=str(error),
            status_code=409,
        )
    except AppError as error:
        goal = load_goal(service, goal_id)
        return _review_response(
            request,
            draft,
            goal,
            error=str(error),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/goals/{goal_id}",
        status_code=303,
    )