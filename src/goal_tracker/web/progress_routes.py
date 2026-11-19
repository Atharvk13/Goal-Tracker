"""Daily progress pages: record, review, update pending work, browse entries.

The flow mirrors the terminal: choose a date and journal text, build items
(manually or from reviewed AI proposals), review the exact draft, then save.
All decisions come from progress.workflow and ProgressService.
"""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goal_tracker.domain.errors import (
    AppError,
    DuplicateReviewRequiredError,
    NotFoundError,
)
from goal_tracker.domain.progress import (
    ActivityInput,
    ActivityKind,
    ActivityState,
    DailyProgressInput,
    EntrySource,
    MilestoneProgress,
)
from goal_tracker.progress.workflow import (
    JOURNAL_CATEGORIES,
    WORK_STATUSES,
    ExtractionUnavailable,
    allow_duplicate,
    extract_journal_items,
    link_independent_work,
    load_all_pending,
    load_goal_lookup,
    milestone_state_choices,
    updatable_pending,
    without_independent_goals,
)
from goal_tracker.web.dependencies import (
    get_context,
    get_gateway,
    get_goal_service,
    get_progress_service,
    templates,
    today,
    validation_messages,
)


router = APIRouter()

ENTRIES_PAGE_SIZE = 20


def _normalize_text(value: str) -> str:
    """Browsers submit CRLF; keep LF so evidence quotes still match."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _blank_item() -> dict:
    return {
        "include": True,
        "kind": ActivityKind.ACTIVITY.value,
        "description": "",
        "state": ActivityState.IN_PROGRESS.value,
        "minutes": "",
        "goal_ids": [],
        "milestone_ids": [],
        "milestone_state": "done",
        "quote": "",
        "updates": "",
        "related": "goal",
    }


def _parse_ids(value: str) -> set[str]:
    return {part for part in value.split(",") if part}


def _milestone_names(goals) -> dict[str, str]:
    return {
        milestone["id"]: milestone["description"]
        for goal in goals
        for milestone in goal["milestones"]
    }


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=f"The {field} must use YYYY-MM-DD format.",
        ) from error


# ---------------------------------------------------------------- rendering


def _editor_response(
    request: Request,
    *,
    activity_date: date,
    raw_text: str,
    source: str,
    items: list[dict],
    errors: list[str] | None = None,
    warning: str | None = None,
    status_code: int = 200,
):
    goals = without_independent_goals(
        get_progress_service(request).list_recordable_goals(activity_date)
    )

    return templates.TemplateResponse(
        request=request,
        name="progress_items.html",
        context={
            "activity_date": activity_date.isoformat(),
            "raw_text": raw_text,
            "source": source,
            "items": items,
            "goals": goals,
            "goal_titles": {goal["id"]: goal["title"] for goal in goals},
            "kinds": JOURNAL_CATEGORIES,
            "work_statuses": WORK_STATUSES,
            "errors": errors or [],
            "warning": warning,
        },
        status_code=status_code,
    )


def _review_response(
    request: Request,
    draft: DailyProgressInput,
    *,
    independent_ids: set[str] | None = None,
    duplicates: list[dict] | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    goals = get_progress_service(request).list_recordable_goals(
        draft.activity_date
    )
    independent_ids = independent_ids or set()

    return templates.TemplateResponse(
        request=request,
        name="progress_review.html",
        context={
            "draft": draft.model_dump(mode="json"),
            "draft_json": draft.model_dump_json(),
            "independent_ids": independent_ids,
            "independent_field": ",".join(sorted(independent_ids)),
            "goal_titles": {goal["id"]: goal["title"] for goal in goals},
            "milestone_names": _milestone_names(goals),
            "duplicates": duplicates or [],
            "error": error,
        },
        status_code=status_code,
    )


# ------------------------------------------------------------ form parsing


def _items_from_form(form, count: int) -> list[dict]:
    items = []

    for index in range(count):
        prefix = f"item-{index}-"
        items.append(
            {
                "include": form.get(prefix + "include") == "on",
                "kind": form.get(prefix + "kind", ActivityKind.ACTIVITY.value),
                "description": form.get(prefix + "description", ""),
                "state": form.get(prefix + "state", ""),
                "minutes": form.get(prefix + "minutes", "").strip(),
                "goal_ids": form.getlist(prefix + "goal"),
                "milestone_ids": form.getlist(prefix + "milestone"),
                "milestone_state": form.get(prefix + "milestone_state", "done"),
                "quote": _normalize_text(form.get(prefix + "quote", "")),
                "updates": form.get(prefix + "updates", ""),
                "related": form.get(prefix + "related", "goal"),
            }
        )

    return items


def _build_activities(
    items: list[dict],
    goals: list[dict],
) -> tuple[list[ActivityInput], list[str], set[str]]:
    """Validate editor rows with the same rules as the terminal workflow.

    Also returns the IDs of independent work items: work that is not related
    to a listed goal (or has no goal ticked). They are linked to the week's
    shared independent goal only when the entry is saved.
    """
    goal_lookup = {goal["id"]: goal for goal in goals}
    activities: list[ActivityInput] = []
    errors: list[str] = []
    used_milestones: set[str] = set()
    independent_ids: set[str] = set()

    for number, item in enumerate(items, start=1):
        if not item["include"]:
            continue

        label = f"Item {number}"

        try:
            kind = ActivityKind(item["kind"])
        except ValueError:
            errors.append(f"{label}: choose a valid category.")
            continue

        if kind == ActivityKind.ACTIVITY:
            if item["state"] not in WORK_STATUSES:
                errors.append(f"{label}: choose a work status.")
                continue
            state = ActivityState(item["state"])
        else:
            state = ActivityState.NOT_APPLICABLE

        minutes = None
        if item["minutes"]:
            try:
                minutes = int(item["minutes"])
                if minutes < 0:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"{label}: minutes must be a nonnegative whole number."
                )
                continue

        independent = (
            kind == ActivityKind.ACTIVITY
            and not item["updates"]
            and (item["related"] == "independent" or not item["goal_ids"])
        )
        goal_ids = [] if independent else item["goal_ids"]

        if independent and item["milestone_ids"]:
            errors.append(
                f"{label}: independent tasks cannot change milestones. "
                "Choose “Related to a listed goal” to update a milestone."
            )
            continue

        unknown = [g for g in goal_ids if g not in goal_lookup]
        if unknown:
            errors.append(
                f"{label}: a selected goal does not accept progress on this date."
            )
            continue

        linked = [goal_lookup[g] for g in goal_ids]
        allowed = {
            milestone["id"]
            for goal in linked
            for milestone in goal["milestones"]
            if milestone["is_included"]
        }

        changes = []
        if item["milestone_ids"]:
            if kind != ActivityKind.ACTIVITY:
                errors.append(
                    f"{label}: only work activities can change milestones."
                )
                continue

            outside = [m for m in item["milestone_ids"] if m not in allowed]
            if outside:
                errors.append(
                    f"{label}: link the goal of every milestone you select."
                )
                continue

            repeated = [m for m in item["milestone_ids"] if m in used_milestones]
            if repeated:
                errors.append(
                    f"{label}: a milestone can change only once per entry."
                )
                continue

            choices = milestone_state_choices(state)
            milestone_state = (
                item["milestone_state"]
                if item["milestone_state"] in choices
                else choices[0]
            )

            for milestone_id in item["milestone_ids"]:
                used_milestones.add(milestone_id)
                changes.append(
                    MilestoneProgress(
                        milestone_id=milestone_id,
                        state=milestone_state,
                    )
                )

        try:
            activity = ActivityInput(
                kind=kind,
                description=item["description"],
                state=state,
                minutes=minutes,
                goal_ids=goal_ids,
                evidence_quote=item["quote"] or None,
                updates_activity_id=item["updates"] or None,
                milestone_changes=changes,
            )
        except ValidationError as error:
            errors.extend(
                f"{label}: {message}" for message in validation_messages(error)
            )
            continue

        activities.append(activity)
        if independent:
            independent_ids.add(str(activity.id))

    if not activities and not errors:
        errors.append("Include at least one item in this entry.")

    return activities, errors, independent_ids


def _items_from_draft(
    draft: DailyProgressInput,
    independent_ids: set[str],
) -> list[dict]:
    items = []

    for activity in draft.activities:
        changes = activity.milestone_changes
        items.append(
            {
                "include": True,
                "kind": activity.kind.value,
                "description": activity.description,
                "state": activity.state.value,
                "minutes": "" if activity.minutes is None else str(activity.minutes),
                "goal_ids": [str(goal_id) for goal_id in activity.goal_ids],
                "milestone_ids": [str(change.milestone_id) for change in changes],
                "milestone_state": changes[0].state.value if changes else "done",
                "quote": activity.evidence_quote or "",
                "updates": (
                    str(activity.updates_activity_id)
                    if activity.updates_activity_id
                    else ""
                ),
                "related": (
                    "independent"
                    if str(activity.id) in independent_ids
                    else "goal"
                ),
            }
        )

    return items


# ------------------------------------------------------------------ routes


@router.get("/progress", include_in_schema=False)
def progress_home(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    saved: str | None = None,
):
    current = today(request)
    end_date = _parse_date(end, "end date") if end else current
    start_date = (
        _parse_date(start, "start date") if start else end_date - timedelta(days=6)
    )

    service = get_progress_service(request)
    error = None
    entries: list[dict] = []

    if start_date > end_date:
        error = "The start date cannot follow the end date."
    else:
        entries = service.get_daily_progress(
            start_date,
            end_date,
            limit=ENTRIES_PAGE_SIZE + 1,
            offset=(page - 1) * ENTRIES_PAGE_SIZE,
        )

    pending = load_all_pending(service, current)

    return templates.TemplateResponse(
        request=request,
        name="progress.html",
        context={
            "entries": entries[:ENTRIES_PAGE_SIZE],
            "has_next": len(entries) > ENTRIES_PAGE_SIZE,
            "page": page,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "today": current.isoformat(),
            "pending_count": len(pending),
            "error": error,
        },
    )


@router.get("/progress/new", include_in_schema=False)
def new_entry(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="progress_new.html",
        context={
            "values": {"activity_date": today(request).isoformat(), "method": "manual"},
            "today": today(request).isoformat(),
            "errors": [],
        },
    )


@router.post("/progress/start", include_in_schema=False)
def start_entry(
    request: Request,
    activity_date: Annotated[str, Form()],
    raw_text: Annotated[str, Form()] = "",
    method: Annotated[str, Form()] = "manual",
):
    raw_text = _normalize_text(raw_text)
    current = today(request)
    errors = []

    try:
        selected_date = date.fromisoformat(activity_date)
        if selected_date > current:
            errors.append("Choose today or an earlier date.")
    except ValueError:
        selected_date = None
        errors.append("Enter the activity date in YYYY-MM-DD format.")

    if not raw_text.strip():
        errors.append("Please enter some journal text.")

    if errors:
        return templates.TemplateResponse(
            request=request,
            name="progress_new.html",
            context={
                "values": {
                    "activity_date": activity_date,
                    "raw_text": raw_text,
                    "method": method,
                },
                "today": current.isoformat(),
                "errors": errors,
            },
            status_code=422,
        )

    if method != "ai":
        return _editor_response(
            request,
            activity_date=selected_date,
            raw_text=raw_text,
            source=EntrySource.MANUAL.value,
            items=[_blank_item()],
        )

    context = get_context(request)

    try:
        proposals = extract_journal_items(
            get_gateway(request),
            raw_text,
            context.logger,
        )
    except ExtractionUnavailable:
        return _editor_response(
            request,
            activity_date=selected_date,
            raw_text=raw_text,
            source=EntrySource.MANUAL.value,
            items=[_blank_item()],
            warning="AI interpretation is unavailable. You can use manual entry.",
        )

    if not proposals:
        return _editor_response(
            request,
            activity_date=selected_date,
            raw_text=raw_text,
            source=EntrySource.MANUAL.value,
            items=[_blank_item()],
            warning=(
                "No supported journal items were found. "
                "You can use manual entry."
            ),
        )

    items = []
    for proposal in proposals:
        item = _blank_item()
        item.update(
            kind=proposal.kind,
            description=proposal.description,
            state=(
                proposal.status
                if proposal.status in WORK_STATUSES
                else ActivityState.IN_PROGRESS.value
            ),
            quote=proposal.evidence_quote,
        )
        items.append(item)

    return _editor_response(
        request,
        activity_date=selected_date,
        raw_text=raw_text,
        source=EntrySource.LLM_REVIEWED.value,
        items=items,
    )


@router.post("/progress/items", include_in_schema=False)
async def submit_items(request: Request):
    form = await request.form()

    activity_date = _parse_date(form.get("activity_date", ""), "activity date")
    raw_text = _normalize_text(form.get("raw_text", ""))
    source = form.get("source", EntrySource.MANUAL.value)

    try:
        count = max(0, min(int(form.get("item_count", "0")), 50))
    except ValueError:
        count = 0

    items = _items_from_form(form, count)

    if form.get("action") == "add_item":
        return _editor_response(
            request,
            activity_date=activity_date,
            raw_text=raw_text,
            source=source,
            items=[*items, _blank_item()],
        )

    service = get_progress_service(request)
    goals = service.list_recordable_goals(activity_date)
    activities, errors, independent_ids = _build_activities(items, goals)

    draft = None
    if not errors:
        try:
            draft = DailyProgressInput(
                activity_date=activity_date,
                raw_text=raw_text,
                activities=activities,
                source=source,
            )
        except ValidationError as error:
            errors = validation_messages(error)

    if errors:
        return _editor_response(
            request,
            activity_date=activity_date,
            raw_text=raw_text,
            source=source,
            items=items,
            errors=errors,
            status_code=422,
        )

    duplicates = service.find_duplicates(draft.activity_date, draft.raw_text)
    return _review_response(
        request,
        draft,
        independent_ids=independent_ids,
        duplicates=duplicates,
    )


@router.post("/progress/edit", include_in_schema=False)
def edit_draft(
    request: Request,
    draft_json: Annotated[str, Form()],
    independent_ids: Annotated[str, Form()] = "",
):
    try:
        draft = DailyProgressInput.model_validate_json(draft_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="The progress draft is invalid. Please start again.",
        ) from error

    return _editor_response(
        request,
        activity_date=draft.activity_date,
        raw_text=draft.raw_text,
        source=draft.source.value,
        items=_items_from_draft(draft, _parse_ids(independent_ids)),
    )


@router.post("/progress/save", include_in_schema=False)
def save_entry(
    request: Request,
    draft_json: Annotated[str, Form()],
    allow_duplicate_entry: Annotated[str, Form()] = "",
    independent_ids: Annotated[str, Form()] = "",
):
    try:
        draft = DailyProgressInput.model_validate_json(draft_json)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="The progress draft is invalid. Please start again.",
        ) from error

    if allow_duplicate_entry == "1":
        draft = allow_duplicate(draft)

    independent = _parse_ids(independent_ids)
    service = get_progress_service(request)

    try:
        linked = link_independent_work(
            draft,
            independent,
            get_goal_service(request),
            get_context(request).settings.timezone,
        )
        result = service.record_progress(linked)
    except DuplicateReviewRequiredError:
        return _review_response(
            request,
            draft,
            independent_ids=independent,
            duplicates=service.find_duplicates(draft.activity_date, draft.raw_text),
            status_code=409,
        )
    except AppError as error:
        return _review_response(
            request,
            draft,
            independent_ids=independent,
            error=str(error),
            status_code=400,
        )

    return RedirectResponse(
        url=f"/progress/entries/{result['entry']['id']}?saved=1",
        status_code=303,
    )


@router.get("/progress/entries/{entry_id}", include_in_schema=False)
def entry_details(request: Request, entry_id: UUID, saved: str | None = None):
    try:
        entry = get_progress_service(request).get_entry(entry_id)
    except NotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="The requested entry does not exist.",
        ) from error

    goals = load_goal_lookup(get_goal_service(request))

    return templates.TemplateResponse(
        request=request,
        name="progress_entry.html",
        context={
            "entry": entry,
            "goal_titles": {gid: goal["title"] for gid, goal in goals.items()},
            "milestone_names": _milestone_names(goals.values()),
            "saved": saved == "1",
        },
    )


@router.get("/progress/pending", include_in_schema=False)
def pending_work(request: Request, on: str | None = None):
    current = today(request)
    selected = _parse_date(on, "date") if on else current
    error = None

    if selected > current:
        error = "Choose today or an earlier date."
        selected = current

    service = get_progress_service(request)
    goals = service.list_recordable_goals(selected)
    goal_lookup = {goal["id"]: goal for goal in goals}
    all_goals = load_goal_lookup(get_goal_service(request))
    pending = load_all_pending(service, selected)
    updatable_ids = {
        activity["id"] for activity in updatable_pending(pending, goal_lookup)
    }

    return templates.TemplateResponse(
        request=request,
        name="progress_pending.html",
        context={
            "pending": pending,
            "updatable_ids": updatable_ids,
            "goal_titles": {gid: goal["title"] for gid, goal in all_goals.items()},
            "on": selected.isoformat(),
            "today": current.isoformat(),
            "error": error,
        },
    )


@router.get("/progress/pending/{activity_id}/update", include_in_schema=False)
def update_pending(request: Request, activity_id: UUID, on: str | None = None):
    current = today(request)
    selected = _parse_date(on, "date") if on else current

    if selected > current:
        raise HTTPException(status_code=422, detail="Choose today or an earlier date.")

    service = get_progress_service(request)
    goal_lookup = {
        goal["id"]: goal for goal in service.list_recordable_goals(selected)
    }
    previous = next(
        (
            activity
            for activity in updatable_pending(
                load_all_pending(service, selected), goal_lookup
            )
            if activity["id"] == str(activity_id)
        ),
        None,
    )

    if previous is None:
        raise HTTPException(
            status_code=404,
            detail="This pending work cannot be updated on the selected date.",
        )

    item = _blank_item()
    item.update(
        state=ActivityState.COMPLETED.value,
        description=previous["description"],
        goal_ids=list(previous["goal_ids"]),
        updates=previous["id"],
    )

    return _editor_response(
        request,
        activity_date=selected,
        raw_text="",
        source=EntrySource.MANUAL.value,
        items=[item],
    )
