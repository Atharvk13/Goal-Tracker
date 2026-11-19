"""Daily-progress workflow rules shared by the terminal and web interfaces.

These functions hold the decisions the recording workflow makes, without any
prompting or rendering, so each interface only collects input and shows output.
"""

import logging

from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.domain.periods import current_period_start
from goal_tracker.domain.progress import (
    ActivityKind,
    ActivityState,
    DailyProgressInput,
)
from goal_tracker.llm.schemas import ActivityProposal


WORK_STATUSES = (
    ActivityState.COMPLETED.value,
    ActivityState.PENDING.value,
    ActivityState.IN_PROGRESS.value,
)

JOURNAL_CATEGORIES = tuple(kind.value for kind in ActivityKind)

_PAGE_SIZE = 100


class ExtractionUnavailable(Exception):
    """The local model could not interpret the journal text."""


def extract_journal_items(
    gateway,
    raw_text: str,
    logger: logging.Logger,
) -> list[ActivityProposal]:
    """Ask the model for journal items; failures become ExtractionUnavailable.

    An empty list means the model ran but found nothing to propose.
    """
    try:
        proposal = gateway.extract_progress(raw_text)
    except Exception as error:
        logger.warning(
            "Progress extraction failed (%s).",
            type(error).__name__,
        )
        raise ExtractionUnavailable from error

    return list(proposal.activities)


def milestone_candidates(
    goals: list[dict],
    used_ids: set[str],
) -> list[tuple[dict, dict]]:
    """Return included milestones of the linked goals not yet used in the entry."""
    return [
        (goal, milestone)
        for goal in goals
        for milestone in goal["milestones"]
        if milestone["is_included"] and milestone["id"] not in used_ids
    ]


def milestone_state_choices(state: ActivityState) -> list[str]:
    """Only completed work may mark a milestone done."""
    if state == ActivityState.COMPLETED:
        return ["done", "not_done"]

    return ["not_done"]


def allow_duplicate(draft: DailyProgressInput) -> DailyProgressInput:
    """Return a copy of the draft that the user confirmed despite duplicates."""
    return DailyProgressInput.model_validate(
        {
            **draft.model_dump(),
            "allow_duplicate": True,
        }
    )


def link_independent_work(
    draft: DailyProgressInput,
    independent_ids: set[str],
    goal_service,
    timezone: str,
) -> DailyProgressInput:
    """Link independent work items to the week's shared independent goal.

    Called only when the user confirms saving. If the week has ended (or the
    goal is no longer active) the items stay unlinked. The draft keeps its
    request ID, and the goal is stable per week, so retries stay idempotent.
    """
    if not independent_ids:
        return draft

    period = PeriodSpec(
        kind=PeriodKind.WEEKLY,
        start_date=current_period_start(PeriodKind.WEEKLY, draft.activity_date),
        timezone=timezone,
    )
    goal = goal_service.ensure_independent_goal(period)

    if goal is None:
        return draft

    payload = draft.model_dump()
    for activity in payload["activities"]:
        if str(activity["id"]) in independent_ids:
            activity["goal_ids"] = [goal["id"]]

    return DailyProgressInput.model_validate(payload)


def without_independent_goals(goals: list[dict]) -> list[dict]:
    """Hide the automatic independent goal from goal pickers."""
    return [goal for goal in goals if not goal.get("is_independent")]


def load_all_pending(service, as_of) -> list[dict]:
    """Return every pending activity as of a date, across all pages."""
    activities = []
    offset = 0

    while True:
        page = service.list_pending_activities(
            as_of=as_of,
            limit=_PAGE_SIZE,
            offset=offset,
        )
        activities.extend(page)

        if len(page) < _PAGE_SIZE:
            return activities

        offset += _PAGE_SIZE


def load_goal_lookup(goal_service) -> dict[str, dict]:
    """Return all goals keyed by ID, across all pages."""
    goals = {}
    offset = 0

    while True:
        page = goal_service.list_goals(limit=_PAGE_SIZE, offset=offset)
        goals.update({goal["id"]: goal for goal in page})

        if len(page) < _PAGE_SIZE:
            return goals

        offset += _PAGE_SIZE


def updatable_pending(
    pending: list[dict],
    goal_lookup: dict[str, dict],
) -> list[dict]:
    """Keep pending work whose linked goals all accept progress on the date."""
    return [
        activity
        for activity in pending
        if all(goal_id in goal_lookup for goal_id in activity["goal_ids"])
    ]
