import sqlite3
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.operations import (
    OperationRepository,
    calculate_payload_hash,
)
from goal_tracker.domain.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    StorageError,
)
from goal_tracker.domain.models import GoalCreate
from goal_tracker.goals.service import GoalService


@pytest.fixture
def service(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    migrate_database(database_path, tmp_path / "backups")

    return GoalService(
        database_path,
        today=lambda: date(2026, 11, 11),
    )


@pytest.fixture
def goal_input():
    return GoalCreate(
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


def changed_goal(goal, **changes):
    """Revalidate changed test inputs rather than mutating model fields."""
    data = goal.model_dump()
    data.update(changes)
    return GoalCreate.model_validate(data)


def test_creation_persists_goal_request_and_audit(service, goal_input):
    result = service.create_goal(goal_input)
    saved = result["goal"]

    assert result["request_id"] == str(goal_input.request_id)
    assert saved["title"] == "Learn LangChain"
    assert saved["revision"] == 1
    assert saved["effective_date"] == "2026-11-11"
    assert saved["is_original_plan"] is True
    assert len(saved["milestones"]) == 2
    assert service.get_goal(saved["id"]) == saved
    assert [goal["id"] for goal in service.list_goals()] == [saved["id"]]

    with open_database(service.database_path) as connection:
        operations = OperationRepository(connection)
        request = operations.find_request(str(goal_input.request_id))
        events = operations.list_audit_events(
            entity_type="goal",
            entity_id=saved["id"],
        )

    assert request["response"] == result
    assert len(events) == 1
    assert events[0]["action"] == "created"
    assert events[0]["after"] == saved


def test_retry_after_restart_and_period_end_returns_original(
    service,
    goal_input,
):
    original = service.create_goal(goal_input)

    restarted = GoalService(
        service.database_path,
        today=lambda: date(2026, 11, 20),
    )
    retried = restarted.create_goal(goal_input)

    assert retried == original

    with open_database(service.database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM goals) AS goals,
                (SELECT COUNT(*) FROM mutation_requests) AS requests,
                (SELECT COUNT(*) FROM audit_events) AS audits
            """
        ).fetchone()

    assert dict(counts) == {"goals": 1, "requests": 1, "audits": 1}


def test_reusing_request_id_with_changed_input_is_a_conflict(
    service,
    goal_input,
):
    original = service.create_goal(goal_input)
    changed = changed_goal(goal_input, title="Different goal")

    with pytest.raises(ConflictError) as error:
        service.create_goal(changed)

    assert error.value.code == "CONFLICT"
    assert error.value.request_id == str(goal_input.request_id)
    assert service.get_goal(original["goal"]["id"])["title"] == "Learn LangChain"
    assert len(service.list_goals()) == 1


def test_request_id_cannot_be_reused_for_another_operation(
    service,
    goal_input,
):
    payload_hash = calculate_payload_hash(
        goal_input.model_dump(mode="json", exclude={"request_id"})
    )

    with open_database(service.database_path) as connection:
        with transaction(connection):
            OperationRepository(connection).record_request(
                request_id=str(goal_input.request_id),
                operation="different_operation",
                payload_hash=payload_hash,
                response={},
            )

    with pytest.raises(ConflictError, match="different input"):
        service.create_goal(goal_input)

    assert service.list_goals() == []


def test_rejects_new_goal_for_an_ended_period(service, goal_input):
    past_goal = changed_goal(
        goal_input,
        period={
            "kind": "weekly",
            "start_date": "2026-11-02",
        },
    )

    with pytest.raises(DomainValidationError, match="current or a future"):
        service.create_goal(past_goal)

    assert service.list_goals() == []


def test_future_goal_becomes_effective_at_period_start(service, goal_input):
    future_goal = changed_goal(
        goal_input,
        period={
            "kind": "weekly",
            "start_date": "2026-11-16",
        },
    )

    result = service.create_goal(future_goal)

    assert result["goal"]["effective_date"] == "2026-11-16"


def test_rejects_timezone_mismatch(service, goal_input):
    different_timezone = changed_goal(
        goal_input,
        period={
            "kind": "weekly",
            "start_date": "2026-11-09",
            "timezone": "UTC",
        },
    )

    with pytest.raises(DomainValidationError, match="timezone must match"):
        service.create_goal(different_timezone)

    assert service.list_goals() == []


def test_missing_predecessor_is_reported(service, goal_input):
    successor = changed_goal(
        goal_input,
        predecessor_goal_id=uuid4(),
    )

    with pytest.raises(NotFoundError, match="predecessor"):
        service.create_goal(successor)

    assert service.list_goals() == []


def test_successor_cannot_overlap_predecessor_period(service, goal_input):
    first = service.create_goal(goal_input)

    successor = changed_goal(
        goal_input,
        request_id=uuid4(),
        predecessor_goal_id=first["goal"]["id"],
    )

    with pytest.raises(DomainValidationError, match="must start after"):
        service.create_goal(successor)

    assert len(service.list_goals()) == 1


def test_successor_can_reference_an_earlier_period(service, goal_input):
    earlier_service = GoalService(
        service.database_path,
        today=lambda: date(2026, 11, 3),
    )
    earlier_goal = changed_goal(
        goal_input,
        period={
            "kind": "weekly",
            "start_date": "2026-11-02",
        },
    )
    predecessor = earlier_service.create_goal(earlier_goal)

    successor = changed_goal(
        goal_input,
        request_id=uuid4(),
        predecessor_goal_id=predecessor["goal"]["id"],
    )
    result = service.create_goal(successor)

    assert (
        result["goal"]["predecessor_goal_id"]
        == predecessor["goal"]["id"]
    )


def test_goal_added_after_planning_closes_is_not_original(
    service,
    goal_input,
):
    first = service.create_goal(goal_input)
    period_id = first["goal"]["period"]["id"]

    with open_database(service.database_path) as connection:
        with transaction(connection):
            connection.execute(
                "UPDATE periods SET planning_closed_at = ? WHERE id = ?",
                ("2026-11-11T09:00:00Z", period_id),
            )

    addition = changed_goal(
        goal_input,
        request_id=uuid4(),
        title="Additional goal",
    )
    result = service.create_goal(addition)

    assert result["goal"]["is_original_plan"] is False
    assert all(
        milestone["is_baseline"]
        for milestone in result["goal"]["milestones"]
    )


def test_audit_failure_rolls_back_entire_creation(
    service,
    goal_input,
    monkeypatch,
):
    def fail_audit(self, **kwargs):
        raise sqlite3.OperationalError("Simulated audit failure")

    monkeypatch.setattr(
        OperationRepository,
        "record_audit_event",
        fail_audit,
    )

    with pytest.raises(StorageError) as error:
        service.create_goal(goal_input)

    assert error.value.request_id == str(goal_input.request_id)

    with open_database(service.database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM periods) AS periods,
                (SELECT COUNT(*) FROM goals) AS goals,
                (SELECT COUNT(*) FROM mutation_requests) AS requests,
                (SELECT COUNT(*) FROM audit_events) AS audits
            """
        ).fetchone()

    assert dict(counts) == {
        "periods": 0,
        "goals": 0,
        "requests": 0,
        "audits": 0,
    }


def test_unknown_goal_is_reported(service):
    with pytest.raises(NotFoundError):
        service.get_goal(uuid4())


def test_invalid_goal_id_is_rejected(service):
    with pytest.raises(DomainValidationError, match="valid UUIDs"):
        service.get_goal("not-a-uuid")


def test_missing_database_is_not_created(tmp_path, goal_input):
    database_path = tmp_path / "missing.sqlite3"
    uninitialized = GoalService(
        database_path,
        today=lambda: date(2026, 11, 11),
    )

    with pytest.raises(StorageError, match="not initialized"):
        uninitialized.create_goal(goal_input)

    assert not database_path.exists()


def test_invalid_lifecycle_filter_is_rejected(service):
    with pytest.raises(DomainValidationError, match="lifecycle status"):
        service.list_goals(lifecycle_status="unknown")


@pytest.mark.parametrize(
    "pagination",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_invalid_pagination_is_rejected(service, pagination):
    with pytest.raises(DomainValidationError, match="Limit must be"):
        service.list_goals(**pagination)