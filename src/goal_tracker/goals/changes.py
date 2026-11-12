import sqlite3
from datetime import UTC, date, datetime

from goal_tracker.db.repositories.goal_changes import GoalChangeRepository
from goal_tracker.db.repositories.goals import GoalRepository
from goal_tracker.domain.enums import GoalLifecycle
from goal_tracker.domain.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    StorageError,
)
from goal_tracker.domain.goal_changes import (
    GoalLifecycleChange,
    GoalUpdate,
    PlanFinalize,
)


def _load_goal(
    connection: sqlite3.Connection,
    goal_id: str,
    expected_revision: int,
) -> dict:
    goal = GoalRepository(connection).get_goal(goal_id)

    if goal is None:
        raise NotFoundError("The requested goal does not exist.")

    if goal["revision"] != expected_revision:
        raise ConflictError("The goal changed. Reload it before editing.")

    return goal


def _effective_date(
    goal: dict,
    requested: date | None,
    today: date,
) -> date:
    period_start = date.fromisoformat(goal["period"]["start_date"])
    period_end = date.fromisoformat(goal["period"]["end_date"])
    current_effective = date.fromisoformat(goal["effective_date"])

    if requested is None:
        if today > period_end:
            raise DomainValidationError(
                "Changes to an ended period require an explicit effective date."
            )

        selected = max(today, period_start)
    else:
        selected = requested

    if not period_start <= selected <= period_end:
        raise DomainValidationError(
            "The effective date must fall within the goal's period."
        )

    if selected < current_effective:
        raise DomainValidationError(
            "The effective date cannot precede the latest goal revision."
        )

    if selected > max(today, period_start):
        raise DomainValidationError(
            "An edit cannot be scheduled for a future date "
            "after the period starts."
        )

    return selected


def _validate_plan(goal: dict, milestones: list[dict]) -> None:
    period_start = date.fromisoformat(goal["period"]["start_date"])
    period_end = date.fromisoformat(goal["period"]["end_date"])

    deadline = (
        date.fromisoformat(goal["target_date"])
        if goal["target_date"] is not None
        else period_end
    )

    if not period_start <= deadline <= period_end:
        raise DomainValidationError(
            "The goal deadline must fall within its period."
        )

    included = [
        milestone for milestone in milestones
        if milestone["is_included"]
    ]

    if not included:
        raise DomainValidationError(
            "An active goal must retain at least one included milestone. "
            "Cancel the goal instead."
        )

    descriptions = set()

    for milestone in included:
        description = milestone["description"].casefold()

        if description in descriptions:
            raise DomainValidationError(
                "Included milestone descriptions must be unique."
            )

        descriptions.add(description)

        due_date = (
            date.fromisoformat(milestone["due_date"])
            if milestone["due_date"] is not None
            else deadline
        )

        if not period_start <= due_date <= deadline:
            raise DomainValidationError(
                "Included milestone deadlines must fall within the period "
                "and cannot exceed the goal deadline."
            )


def apply_goal_update(
    connection: sqlite3.Connection,
    request: GoalUpdate,
    *,
    today: date,
) -> tuple[dict, dict]:
    """Validate and apply an edit inside the caller's transaction."""
    goal_id = str(request.goal_id)
    before = _load_goal(connection, goal_id, request.expected_revision)

    if before["lifecycle_status"] != GoalLifecycle.ACTIVE.value:
        raise DomainValidationError(
            "Only active goals can be edited."
        )

    effective_date = _effective_date(
        before,
        request.effective_date,
        today,
    )

    revised = dict(before)

    for name in ("title", "description", "expected_outcome"):
        if name in request.model_fields_set:
            revised[name] = getattr(request, name)

    if "target_date" in request.model_fields_set:
        revised["target_date"] = (
            request.target_date.isoformat()
            if request.target_date is not None
            else None
        )

    default_due_date = (
        revised["target_date"] or before["period"]["end_date"]
    )

    milestones = {
        milestone["id"]: dict(milestone)
        for milestone in before["milestones"]
    }

    modified = []

    for change in request.milestone_changes:
        milestone_id = str(change.milestone_id)

        if milestone_id not in milestones:
            raise DomainValidationError(
                "A milestone change refers to a milestone outside this goal."
            )

        original = milestones[milestone_id]
        updated = dict(original)

        if "description" in change.model_fields_set:
            updated["description"] = change.description

        if "is_included" in change.model_fields_set:
            updated["is_included"] = change.is_included

        if "due_date" in change.model_fields_set:
            updated["due_date"] = (
                change.due_date.isoformat()
                if change.due_date is not None
                else default_due_date
            )

        milestones[milestone_id] = updated

        if updated != original:
            modified.append((original, updated))

    additions = [
        {
            "description": milestone.description,
            "due_date": (
                milestone.due_date.isoformat()
                if milestone.due_date is not None
                else default_due_date
            ),
            "is_included": True,
        }
        for milestone in request.add_milestones
    ]

    _validate_plan(
        revised,
        [*milestones.values(), *additions],
    )

    changed_text = any(
        revised[name] != before[name]
        for name in (
            "title",
            "description",
            "expected_outcome",
            "target_date",
        )
    )

    if not changed_text and not modified and not additions:
        raise DomainValidationError(
            "The request does not change the saved goal."
        )

    changes = GoalChangeRepository(connection)

    changes.append_goal_revision(
        goal_id=goal_id,
        expected_revision=request.expected_revision,
        details=revised,
        effective_date=effective_date,
        reason=request.reason,
    )

    for original, updated in modified:
        changes.append_milestone_revision(
            milestone_id=original["id"],
            expected_revision=original["revision"],
            details=updated,
            effective_date=effective_date,
            reason=request.reason,
        )

    for milestone in additions:
        changes.insert_milestone(
            goal_id=goal_id,
            description=milestone["description"],
            due_date=date.fromisoformat(milestone["due_date"]),
            effective_date=effective_date,
            reason=request.reason,
        )

    after = GoalRepository(connection).get_goal(goal_id)

    if after is None:
        raise StorageError("The updated goal could not be retrieved.")

    return before, after


def apply_lifecycle_change(
    connection: sqlite3.Connection,
    request: GoalLifecycleChange,
    *,
    today: date,
) -> tuple[dict, dict]:
    """Cancel or archive a goal without deleting its history."""
    goal_id = str(request.goal_id)
    before = _load_goal(connection, goal_id, request.expected_revision)

    if before["lifecycle_status"] == GoalLifecycle.ARCHIVED.value:
        raise ConflictError("The goal is already archived.")

    target_status = (
        GoalLifecycle.CANCELLED.value
        if request.action == "cancel"
        else GoalLifecycle.ARCHIVED.value
    )

    if before["lifecycle_status"] == target_status:
        raise ConflictError("The goal already has the requested status.")

    effective_date = _effective_date(
        before,
        request.effective_date,
        today,
    )

    revised = {
        **before,
        "lifecycle_status": target_status,
    }

    GoalChangeRepository(connection).append_goal_revision(
        goal_id=goal_id,
        expected_revision=request.expected_revision,
        details=revised,
        effective_date=effective_date,
        reason=request.reason,
    )

    after = GoalRepository(connection).get_goal(goal_id)

    if after is None:
        raise StorageError("The updated goal could not be retrieved.")

    return before, after


def apply_plan_finalize(
    connection: sqlite3.Connection,
    request: PlanFinalize,
    *,
    today: date,
) -> tuple[dict, dict]:
    """Finalize initial planning without changing creation baselines."""
    period_id = str(request.period_id)
    changes = GoalChangeRepository(connection)
    before = changes.get_period(period_id)

    if before is None:
        raise NotFoundError("The requested period does not exist.")

    if before["planning_closed_at"] is not None:
        raise ConflictError("The initial plan is already finalized.")

    if today > date.fromisoformat(before["end_date"]):
        raise DomainValidationError(
            "Initial planning cannot be finalized after the period ends."
        )

    active_goals = GoalRepository(connection).list_goals(
        period_id=period_id,
        lifecycle_status=GoalLifecycle.ACTIVE,
        limit=1,
    )

    if not active_goals:
        raise DomainValidationError(
            "Create at least one active goal before finalizing the plan."
        )

    closed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    changes.finalize_period_planning(period_id, closed_at)
    after = changes.get_period(period_id)

    if after is None:
        raise StorageError("The finalized period could not be retrieved.")

    return before, after