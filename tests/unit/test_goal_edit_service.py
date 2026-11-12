import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from goal_tracker.db.connection import open_database
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.operations import OperationRepository
from goal_tracker.domain.errors import (
    ConflictError,
    DomainValidationError,
    StorageError,
)
from goal_tracker.domain.goal_changes import (
    GoalLifecycleChange,
    GoalUpdate,
    PlanFinalize,
)
from goal_tracker.domain.models import GoalCreate
from goal_tracker.goals.service import GoalService


@pytest.fixture
def goal_setup(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    migrate_database(database_path, tmp_path / "backups")

    service = GoalService(
        database_path,
        today=lambda: date(2026, 11, 11),
    )
    goal_input = GoalCreate(
        period={
            "kind": "weekly",
            "start_date": "2026-11-09",
        },
        title="Learn LangChain",
        milestones=[
            {"description": "Complete the tutorial"},
            {"description": "Build a client"},
        ],
    )
    goal = service.create_goal(goal_input)["goal"]

    return SimpleNamespace(
        service=service,
        goal=goal,
        goal_input=goal_input,
    )


def update_request(setup, **changes):
    data = {
        "goal_id": setup.goal["id"],
        "expected_revision": 1,
        "reason": "Refine the goal plan.",
    }
    data.update(changes)
    return GoalUpdate.model_validate(data)


def lifecycle_request(setup, action, revision=1):
    return GoalLifecycleChange(
        goal_id=setup.goal["id"],
        expected_revision=revision,
        action=action,
        reason="Update goal lifecycle.",
    )


def test_title_edit_preserves_history_and_records_audit(goal_setup):
    request = update_request(
        goal_setup,
        title="Build a LangChain project",
    )

    result = goal_setup.service.update_goal(request)
    history = goal_setup.service.get_goal_history(goal_setup.goal["id"])

    assert result["goal"]["revision"] == 2
    assert result["goal"]["title"] == "Build a LangChain project"

    assert [
        row["title"] for row in history["goal_revisions"]
    ] == ["Learn LangChain", "Build a LangChain project"]

    assert [
        event["action"] for event in history["audit_events"]
    ] == ["created", "updated"]


def test_retry_returns_original_response_even_after_later_edit(goal_setup):
    first_request = update_request(goal_setup, title="First revision")
    first_result = goal_setup.service.update_goal(first_request)

    second_request = update_request(
        goal_setup,
        expected_revision=2,
        title="Later revision",
    )
    goal_setup.service.update_goal(second_request)

    retried = goal_setup.service.update_goal(first_request)
    current = goal_setup.service.get_goal(goal_setup.goal["id"])
    history = goal_setup.service.get_goal_history(goal_setup.goal["id"])

    assert retried == first_result
    assert retried["goal"]["revision"] == 2
    assert current["revision"] == 3
    assert current["title"] == "Later revision"
    assert len(history["audit_events"]) == 3


def test_request_hash_distinguishes_omitted_and_cleared_deadline(goal_setup):
    request = update_request(goal_setup, title="Updated title")
    goal_setup.service.update_goal(request)

    payload = request.model_dump(mode="json", exclude_unset=True)
    payload["request_id"] = str(request.request_id)
    payload["target_date"] = None
    altered_request = GoalUpdate.model_validate(payload)

    with pytest.raises(ConflictError, match="different input"):
        goal_setup.service.update_goal(altered_request)


def test_stale_edit_is_rejected(goal_setup):
    goal_setup.service.update_goal(
        update_request(goal_setup, title="Updated title")
    )

    with pytest.raises(ConflictError, match="goal changed"):
        goal_setup.service.update_goal(
            update_request(goal_setup, title="Outdated edit")
        )

    assert goal_setup.service.get_goal(
        goal_setup.goal["id"]
    )["revision"] == 2


def test_added_milestone_is_outside_original_baseline(goal_setup):
    result = goal_setup.service.update_goal(
        update_request(
            goal_setup,
            add_milestones=[
                {"description": "Write integration tests"},
            ],
        )
    )

    saved = result["goal"]

    assert saved["revision"] == 2
    assert len(saved["milestones"]) == 3
    assert sum(
        milestone["is_baseline"]
        for milestone in saved["milestones"]
    ) == 2

    added = next(
        milestone for milestone in saved["milestones"]
        if milestone["description"] == "Write integration tests"
    )

    assert added["is_baseline"] is False
    assert added["due_date"] == "2026-11-15"


def test_edit_cannot_use_another_goals_milestone(goal_setup):
    other = goal_setup.service.create_goal(
        GoalCreate(
            period=goal_setup.goal_input.period,
            title="Another goal",
            milestones=[{"description": "Other milestone"}],
        )
    )["goal"]

    request = update_request(
        goal_setup,
        milestone_changes=[
            {
                "milestone_id": other["milestones"][0]["id"],
                "description": "Wrong owner",
            }
        ],
    )

    with pytest.raises(DomainValidationError, match="outside this goal"):
        goal_setup.service.update_goal(request)

    assert goal_setup.service.get_goal(other["id"]) == other
    assert goal_setup.service.get_goal(
        goal_setup.goal["id"]
    ) == goal_setup.goal


def test_included_milestone_descriptions_must_remain_unique(goal_setup):
    request = update_request(
        goal_setup,
        add_milestones=[
            {"description": "COMPLETE THE TUTORIAL"},
        ],
    )

    with pytest.raises(DomainValidationError, match="must be unique"):
        goal_setup.service.update_goal(request)


def test_removing_every_included_milestone_is_rejected(goal_setup):
    request = update_request(
        goal_setup,
        milestone_changes=[
            {
                "milestone_id": milestone["id"],
                "is_included": False,
            }
            for milestone in goal_setup.goal["milestones"]
        ],
    )

    with pytest.raises(DomainValidationError, match="at least one"):
        goal_setup.service.update_goal(request)


def test_shortened_deadline_requires_consistent_milestone_dates(goal_setup):
    with pytest.raises(DomainValidationError, match="cannot exceed"):
        goal_setup.service.update_goal(
            update_request(
                goal_setup,
                target_date="2026-11-12",
            )
        )

    result = goal_setup.service.update_goal(
        update_request(
            goal_setup,
            target_date="2026-11-12",
            milestone_changes=[
                {
                    "milestone_id": milestone["id"],
                    "due_date": None,
                }
                for milestone in goal_setup.goal["milestones"]
            ],
        )
    )

    assert result["goal"]["revision"] == 2
    assert all(
        milestone["due_date"] == "2026-11-12"
        for milestone in result["goal"]["milestones"]
    )

    history = goal_setup.service.get_goal_history(goal_setup.goal["id"])
    originals = [
        row for row in history["milestone_revisions"]
        if row["revision"] == 1
    ]

    assert all(row["due_date"] == "2026-11-15" for row in originals)


def test_unchanged_values_do_not_create_a_revision(goal_setup):
    request = update_request(
        goal_setup,
        title=goal_setup.goal["title"],
    )

    with pytest.raises(DomainValidationError, match="does not change"):
        goal_setup.service.update_goal(request)


@pytest.mark.parametrize(
    "effective_date",
    [
        "2026-11-10",
        "2026-11-12",
        "2026-11-16",
    ],
)
def test_rejects_invalid_effective_dates(goal_setup, effective_date):
    request = update_request(
        goal_setup,
        title="Updated title",
        effective_date=effective_date,
    )

    with pytest.raises(DomainValidationError, match="effective date|future date"):
        goal_setup.service.update_goal(request)


def test_ended_period_correction_requires_explicit_date(goal_setup):
    later_service = GoalService(
        goal_setup.service.database_path,
        today=lambda: date(2026, 11, 20),
    )

    with pytest.raises(DomainValidationError, match="explicit effective date"):
        later_service.update_goal(
            update_request(goal_setup, title="Corrected title")
        )

    result = later_service.update_goal(
        update_request(
            goal_setup,
            title="Corrected title",
            effective_date="2026-11-15",
        )
    )

    assert result["goal"]["title"] == "Corrected title"
    assert result["goal"]["effective_date"] == "2026-11-15"


def test_cancellation_and_archiving_preserve_records_and_retries(goal_setup):
    cancel = lifecycle_request(goal_setup, "cancel")
    cancelled = goal_setup.service.change_lifecycle(cancel)

    assert cancelled["goal"]["lifecycle_status"] == "cancelled"
    assert goal_setup.service.change_lifecycle(cancel) == cancelled

    archived = goal_setup.service.change_lifecycle(
        lifecycle_request(goal_setup, "archive", revision=2)
    )

    assert archived["goal"]["revision"] == 3
    assert archived["goal"]["lifecycle_status"] == "archived"

    assert {
        item["id"] for item in archived["goal"]["milestones"]
    } == {
        item["id"] for item in goal_setup.goal["milestones"]
    }

    assert goal_setup.service.change_lifecycle(cancel) == cancelled
    assert goal_setup.service.get_goal(
        goal_setup.goal["id"]
    )["revision"] == 3

    with pytest.raises(ConflictError, match="already archived"):
        goal_setup.service.change_lifecycle(
            lifecycle_request(goal_setup, "cancel", revision=3)
        )

    history = goal_setup.service.get_goal_history(goal_setup.goal["id"])

    assert [
        row["lifecycle_status"] for row in history["goal_revisions"]
    ] == ["active", "cancelled", "archived"]


@pytest.mark.parametrize("action", ["cancel", "archive"])
def test_inactive_goals_cannot_be_edited(goal_setup, action):
    goal_setup.service.change_lifecycle(
        lifecycle_request(goal_setup, action)
    )

    request = update_request(
        goal_setup,
        expected_revision=2,
        title="Forbidden edit",
    )

    with pytest.raises(DomainValidationError, match="Only active"):
        goal_setup.service.update_goal(request)


def test_finalization_marks_later_goals_as_additions(goal_setup):
    period_id = goal_setup.goal["period"]["id"]
    request = PlanFinalize(period_id=period_id)

    finalized = goal_setup.service.finalize_plan(request)

    assert finalized["period"]["planning_closed_at"] is not None
    assert goal_setup.service.finalize_plan(request) == finalized

    with pytest.raises(ConflictError, match="already finalized"):
        goal_setup.service.finalize_plan(
            PlanFinalize(period_id=period_id)
        )

    addition = goal_setup.service.create_goal(
        GoalCreate(
            period=goal_setup.goal_input.period,
            title="Additional goal",
            milestones=[{"description": "Practise SQLite"}],
        )
    )["goal"]

    assert addition["is_original_plan"] is False
    assert goal_setup.service.get_goal(
        goal_setup.goal["id"]
    )["is_original_plan"] is True

    with open_database(goal_setup.service.database_path) as connection:
        events = OperationRepository(connection).list_audit_events(
            entity_type="period",
            entity_id=period_id,
        )

    assert len(events) == 1
    assert events[0]["action"] == "planning_finalized"


def test_finalization_requires_an_active_goal(goal_setup):
    goal_setup.service.change_lifecycle(
        lifecycle_request(goal_setup, "cancel")
    )

    with pytest.raises(DomainValidationError, match="at least one active goal"):
        goal_setup.service.finalize_plan(
            PlanFinalize(period_id=goal_setup.goal["period"]["id"])
        )


def test_ended_period_cannot_be_finalized(goal_setup):
    later_service = GoalService(
        goal_setup.service.database_path,
        today=lambda: date(2026, 11, 20),
    )

    with pytest.raises(DomainValidationError, match="after the period ends"):
        later_service.finalize_plan(
            PlanFinalize(period_id=goal_setup.goal["period"]["id"])
        )


def test_audit_failure_rolls_back_edit_and_added_milestone(
    goal_setup,
    monkeypatch,
):
    request = update_request(
        goal_setup,
        title="Temporary title",
        add_milestones=[{"description": "Temporary milestone"}],
    )

    def fail_audit(self, **kwargs):
        raise sqlite3.OperationalError("Simulated audit failure")

    monkeypatch.setattr(
        OperationRepository,
        "record_audit_event",
        fail_audit,
    )

    with pytest.raises(StorageError):
        goal_setup.service.update_goal(request)

    assert goal_setup.service.get_goal(
        goal_setup.goal["id"]
    ) == goal_setup.goal

    with open_database(goal_setup.service.database_path) as connection:
        assert OperationRepository(connection).find_request(
            str(request.request_id)
        ) is None


def test_history_supports_audit_pagination(goal_setup):
    goal_setup.service.update_goal(
        update_request(goal_setup, title="Updated title")
    )

    history = goal_setup.service.get_goal_history(
        goal_setup.goal["id"],
        audit_limit=1,
        audit_offset=1,
    )

    assert len(history["goal_revisions"]) == 2
    assert len(history["audit_events"]) == 1
    assert history["audit_events"][0]["action"] == "updated"
    assert history["audit_pagination"] == {
        "limit": 1,
        "offset": 1,
    }