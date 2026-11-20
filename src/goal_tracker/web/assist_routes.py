"""AI suggestions for a saved goal and the Monday-to-Friday week plan.

Both are advice: they are stored so they are generated once, and they change
goals only when the user adds a suggested milestone through the normal
reviewed edit flow.
"""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goal_tracker.assist.planning import (
    GOAL_REVIEW,
    WEEK_PLAN,
    evidence_hash,
    goal_review_input,
    week_plan_input,
    week_to_plan,
)
from goal_tracker.assist.store import SuggestionStore
from goal_tracker.domain.goal_changes import GoalUpdate
from goal_tracker.web.dependencies import (
    get_analysis_service,
    get_context,
    get_gateway,
    get_goal_service,
    get_progress_service,
    load_goal,
    templates,
    today,
    validation_messages,
)
from goal_tracker.web.goal_edit_routes import _review_response as edit_review_response


router = APIRouter()

ADDED_MILESTONE_REASON = "Added an AI-suggested milestone."


def _store(request: Request) -> SuggestionStore:
    return SuggestionStore(get_context(request).paths.database)


def _unavailable(request: Request, what: str, error: Exception) -> str:
    get_context(request).logger.warning("%s failed (%s).", what, type(error).__name__)
    return (
        f"The local model is unavailable ({type(error).__name__}). "
        "Check that Ollama is running and try again."
    )


# ------------------------------------------------------- goal suggestions


def goal_suggestions(request: Request, goal: dict) -> dict:
    """What the goal page shows in its "AI suggestions" section."""
    if goal.get("is_independent"):
        return {"available": False}

    stored = _store(request).get(GOAL_REVIEW, goal["id"])
    return {
        "available": goal["lifecycle_status"] == "active",
        "stored": stored,
        "stale": bool(stored)
        and stored["evidence_hash"] != evidence_hash(goal_review_input(goal)),
        "error": None,
    }


def _suggestions_fragment(request: Request, goal: dict, ai: dict, status_code=200):
    return templates.TemplateResponse(
        request=request,
        name="_goal_suggestions.html",
        context={"goal": goal, "ai": ai},
        status_code=status_code,
    )


@router.post("/goals/{goal_id}/suggestions", include_in_schema=False)
def generate_goal_suggestions(request: Request, goal_id: UUID):
    """Ask the model to review the goal and store the suggestions."""
    goal = load_goal(get_goal_service(request), goal_id)
    ai = goal_suggestions(request, goal)
    fragment = request.headers.get("x-fragment") == "1"

    if not ai["available"]:
        raise HTTPException(
            status_code=400,
            detail="AI suggestions are only available for active planned goals.",
        )

    data = goal_review_input(goal)
    try:
        review = get_gateway(request).review_goal(data)
    except Exception as error:
        ai["error"] = _unavailable(request, "Goal review", error)
        if fragment:
            return _suggestions_fragment(request, goal, ai, 503)
        return templates.TemplateResponse(
            request=request,
            name="goal_details.html",
            context={"goal": goal, "ai": ai},
            status_code=503,
        )

    ai["stored"] = _store(request).save(
        GOAL_REVIEW,
        goal["id"],
        evidence_hash=evidence_hash(data),
        content=review.model_dump(),
        model=get_context(request).settings.llm_model,
    )
    ai["stale"] = False

    if fragment:
        return _suggestions_fragment(request, goal, ai)

    return RedirectResponse(url=f"/goals/{goal_id}#ai-suggestions", status_code=303)


@router.post("/goals/{goal_id}/suggestions/milestone", include_in_schema=False)
def add_suggested_milestone(
    request: Request,
    goal_id: UUID,
    description: Annotated[str, Form()],
):
    """Open the normal edit review with the suggested milestone added."""
    goal = load_goal(get_goal_service(request), goal_id)

    try:
        draft = GoalUpdate.model_validate(
            {
                "goal_id": goal_id,
                "expected_revision": goal["revision"],
                "reason": ADDED_MILESTONE_REASON,
                "add_milestones": [{"description": description.strip()}],
            }
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422, detail="; ".join(validation_messages(error))
        ) from error

    return edit_review_response(request, draft)


# -------------------------------------------------------------- week plan


def _week_page(request: Request, requested: date | None, *, error=None, status_code=200):
    current = today(request)
    monday, days = week_to_plan(current, requested)
    data = week_plan_input(
        get_goal_service(request),
        get_analysis_service(request),
        get_progress_service(request),
        monday=monday,
        today=current,
    )
    stored = _store(request).get(WEEK_PLAN, monday.isoformat())

    dates = {
        name: (monday + timedelta(days=index)).isoformat()
        for index, name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        )
    }

    return templates.TemplateResponse(
        request=request,
        name="week_plan.html",
        context={
            "monday": monday.isoformat(),
            "friday": (monday + timedelta(days=4)).isoformat(),
            "next_monday": (monday + timedelta(days=7)).isoformat(),
            "is_current_week": monday <= current,
            "days": days,
            "dates": dates,
            "today_name": current.strftime("%A"),
            "data": data,
            "stored": stored,
            "stale": bool(stored)
            and stored["evidence_hash"] != evidence_hash(data),
            "error": error,
        },
        status_code=status_code,
    )


def _parse_week(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("/week-plan", include_in_schema=False)
def week_plan(request: Request, week: str | None = None):
    return _week_page(request, _parse_week(week))


@router.post("/week-plan", include_in_schema=False)
def generate_week_plan(request: Request, week: Annotated[str, Form()] = ""):
    requested = _parse_week(week)
    current = today(request)
    monday, days = week_to_plan(current, requested)
    data = week_plan_input(
        get_goal_service(request),
        get_analysis_service(request),
        get_progress_service(request),
        monday=monday,
        today=current,
    )

    if not data["goals"] and not data["pending_work"]:
        return _week_page(
            request,
            requested,
            error=(
                "There is nothing to plan yet: no active goal has open "
                "milestones this week and no work is pending. Add a goal first."
            ),
            status_code=422,
        )

    try:
        plan = get_gateway(request).plan_week(data, days)
    except Exception as error:
        return _week_page(
            request,
            requested,
            error=_unavailable(request, "Week planning", error),
            status_code=503,
        )

    _store(request).save(
        WEEK_PLAN,
        monday.isoformat(),
        evidence_hash=evidence_hash(data),
        content=plan.model_dump(),
        model=get_context(request).settings.llm_model,
    )

    return RedirectResponse(url=f"/week-plan?week={monday.isoformat()}", status_code=303)
