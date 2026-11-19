import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goal_tracker.domain.errors import AppError, ConflictError, NotFoundError
from goal_tracker.domain.goal_changes import GoalUpdate
from goal_tracker.web.dependencies import (
    GOAL_NOT_FOUND,
    get_goal_service,
    load_goal,
    templates,
    validation_messages,
)


router = APIRouter()

EDITABLE_FIELDS = (
    "title",
    "description",
    "expected_outcome",
    "target_date",
)


def _edit_response(
    request: Request,
    goal: dict,
    *,
    values: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            field: goal[field] or ""
            for field in EDITABLE_FIELDS
        }
        values.update(
            expected_revision=goal["revision"],
            reason="",
            effective_date="",
        )

    return templates.TemplateResponse(
        request=request,
        name="goal_edit.html",
        context={
            "goal": goal,
            "values": values,
            "errors": errors or [],
        },
        status_code=status_code,
    )


def _review_response(
    request: Request,
    draft: GoalUpdate,
    *,
    error: str | None = None,
    status_code: int = 200,
):
    # Preserve omitted fields while retaining the generated retry ID.
    payload = draft.model_dump(mode="json", exclude_unset=True)
    payload["request_id"] = str(draft.request_id)

    changes = {
        field: payload[field]
        for field in EDITABLE_FIELDS
        if field in payload
    }

    return templates.TemplateResponse(
        request=request,
        name="goal_edit_review.html",
        context={
            "draft": draft,
            "changes": changes,
            "draft_json": json.dumps(payload),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/goals/{goal_id}/edit", include_in_schema=False)
def edit_goal(request: Request, goal_id: UUID):
    goal = load_goal(get_goal_service(request), goal_id)
    return _edit_response(request, goal)


@router.post("/goals/{goal_id}/edit/review", include_in_schema=False)
def review_goal_edit(
    request: Request,
    goal_id: UUID,
    expected_revision: Annotated[int, Form()],
    title: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    expected_outcome: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
    effective_date: Annotated[str, Form()] = "",
):
    goal = load_goal(get_goal_service(request), goal_id)

    values = {
        "expected_revision": expected_revision,
        "title": title,
        "description": description,
        "expected_outcome": expected_outcome,
        "target_date": target_date,
        "reason": reason,
        "effective_date": effective_date,
    }

    if expected_revision != goal["revision"]:
        return _edit_response(
            request,
            goal,
            values=values,
            errors=[
                "This goal changed after you opened the form. "
                "Reload the edit page and review the latest version."
            ],
            status_code=409,
        )

    proposed = {
        "title": title.strip(),
        "description": description.strip(),
        "expected_outcome": expected_outcome.strip(),
        "target_date": target_date.strip() or None,
    }

    changes = {
        field: value
        for field, value in proposed.items()
        if value != goal[field]
    }

    try:
        draft = GoalUpdate.model_validate(
            {
                "goal_id": goal_id,
                "expected_revision": expected_revision,
                "reason": reason,
                "effective_date": effective_date.strip() or None,
                **changes,
            }
        )
    except ValidationError as error:
        return _edit_response(
            request,
            goal,
            values=values,
            errors=validation_messages(error),
            status_code=422,
        )

    return _review_response(request, draft)


@router.post("/goals/{goal_id}/edit/save", include_in_schema=False)
def save_goal_edit(
    request: Request,
    goal_id: UUID,
    draft_json: Annotated[str, Form()],
):
    try:
        draft = GoalUpdate.model_validate_json(draft_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="The edit draft is invalid. Please reopen the edit form.",
        ) from error

    if draft.goal_id != goal_id:
        raise HTTPException(
            status_code=422,
            detail="The edit draft belongs to a different goal.",
        )

    try:
        get_goal_service(request).update_goal(draft)
    except NotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=GOAL_NOT_FOUND,
        ) from error
    except ConflictError:
        return _review_response(
            request,
            draft,
            error=(
                "This goal changed before your edit was saved. "
                "Reopen the edit form to review its latest version."
            ),
            status_code=409,
        )
    except AppError as error:
        return _review_response(
            request,
            draft,
            error=str(error),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/goals/{goal_id}",
        status_code=303,
    )
