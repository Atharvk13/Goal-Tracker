import sqlite3
from uuid import uuid4

import pytest

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.migrate import migrate_database
from goal_tracker.db.repositories.operations import (
    OperationRepository,
    calculate_payload_hash,
)


@pytest.fixture
def repository(tmp_path):
    database_path = tmp_path / "tracker.sqlite3"
    migrate_database(database_path, tmp_path / "backups")

    with open_database(database_path) as connection:
        yield OperationRepository(connection)


def test_hash_ignores_dictionary_key_order():
    first = {
        "title": "Learn",
        "period": {"kind": "weekly", "timezone": "Asia/Kolkata"},
    }
    second = {
        "period": {"timezone": "Asia/Kolkata", "kind": "weekly"},
        "title": "Learn",
    }

    assert calculate_payload_hash(first) == calculate_payload_hash(second)


def test_changed_input_has_a_different_hash():
    first = {"title": "Learn LangChain"}
    second = {"title": "Learn SQLite"}

    assert calculate_payload_hash(first) != calculate_payload_hash(second)


def test_missing_request_returns_none(repository):
    assert repository.find_request(str(uuid4())) is None


def test_request_response_round_trip(repository):
    request_id = str(uuid4())
    payload_hash = calculate_payload_hash({"title": "Learn LangChain"})
    response = {
        "id": "goal-123",
        "title": "Learn LangChain",
        "revision": 1,
        "milestones": [{"description": "Read 📚", "included": True}],
    }

    with transaction(repository.connection):
        repository.record_request(
            request_id=request_id,
            operation="create_goal",
            payload_hash=payload_hash,
            response=response,
        )

    saved = repository.find_request(request_id)

    assert saved["request_id"] == request_id
    assert saved["operation"] == "create_goal"
    assert saved["payload_hash"] == payload_hash
    assert saved["response"] == response


def test_duplicate_request_id_does_not_replace_original(repository):
    request_id = str(uuid4())

    with transaction(repository.connection):
        repository.record_request(
            request_id=request_id,
            operation="create_goal",
            payload_hash="first-hash",
            response={"id": "original-goal"},
        )

    with pytest.raises(sqlite3.IntegrityError):
        with transaction(repository.connection):
            repository.record_request(
                request_id=request_id,
                operation="create_goal",
                payload_hash="different-hash",
                response={"id": "different-goal"},
            )

    saved = repository.find_request(request_id)

    assert saved["payload_hash"] == "first-hash"
    assert saved["response"] == {"id": "original-goal"}


def test_audit_history_preserves_snapshots_and_filters_entities(repository):
    request_id = str(uuid4())
    entity_id = str(uuid4())

    original = {"title": "Original title"}
    revised = {"title": "Revised title"}

    with transaction(repository.connection):
        repository.record_request(
            request_id=request_id,
            operation="test_operation",
            payload_hash="test-hash",
            response={"id": entity_id},
        )

        first_id = repository.record_audit_event(
            entity_type="goal",
            entity_id=entity_id,
            action="created",
            before=None,
            after=original,
            reason="Initial goal",
            request_id=request_id,
        )

        second_id = repository.record_audit_event(
            entity_type="goal",
            entity_id=entity_id,
            action="updated",
            before=original,
            after=revised,
            reason="Clarify the outcome",
            request_id=request_id,
        )

        repository.record_audit_event(
            entity_type="period",
            entity_id=entity_id,
            action="created",
            before=None,
            after={"kind": "weekly"},
            reason="Different entity type",
            request_id=request_id,
        )

    events = repository.list_audit_events(
        entity_type="goal",
        entity_id=entity_id,
    )

    assert [event["id"] for event in events] == [first_id, second_id]
    assert events[0]["before"] is None
    assert events[0]["after"] == original
    assert events[1]["before"] == original
    assert events[1]["after"] == revised
    assert events[1]["reason"] == "Clarify the outcome"

    second_page = repository.list_audit_events(
        entity_type="goal",
        entity_id=entity_id,
        limit=1,
        offset=1,
    )

    assert [event["id"] for event in second_page] == [second_id]


def test_failed_transaction_rolls_back_request_and_audit(repository):
    request_id = str(uuid4())
    entity_id = str(uuid4())

    with pytest.raises(RuntimeError, match="Abort operation"):
        with transaction(repository.connection):
            repository.record_request(
                request_id=request_id,
                operation="create_goal",
                payload_hash="test-hash",
                response={"id": entity_id},
            )

            repository.record_audit_event(
                entity_type="goal",
                entity_id=entity_id,
                action="created",
                before=None,
                after={"title": "Temporary goal"},
                reason="Initial goal",
                request_id=request_id,
            )

            raise RuntimeError("Abort operation")

    assert repository.find_request(request_id) is None
    assert repository.list_audit_events(
        entity_type="goal",
        entity_id=entity_id,
    ) == []


def test_writes_require_an_explicit_transaction(repository):
    request_id = str(uuid4())

    with pytest.raises(RuntimeError, match="explicit transaction"):
        repository.record_request(
            request_id=request_id,
            operation="create_goal",
            payload_hash="test-hash",
            response={},
        )

    with pytest.raises(RuntimeError, match="explicit transaction"):
        repository.record_audit_event(
            entity_type="goal",
            entity_id=str(uuid4()),
            action="created",
            before=None,
            after={},
            reason="Initial goal",
            request_id=request_id,
        )


def test_audit_requires_an_existing_request(repository):
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(repository.connection):
            repository.record_audit_event(
                entity_type="goal",
                entity_id=str(uuid4()),
                action="created",
                before=None,
                after={"title": "Unconfirmed goal"},
                reason="Missing request",
                request_id=str(uuid4()),
            )

    assert repository.connection.execute(
        "SELECT COUNT(*) FROM audit_events"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "pagination",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
def test_rejects_invalid_audit_pagination(repository, pagination):
    with pytest.raises(ValueError, match="Limit must be"):
        repository.list_audit_events(
            entity_type="goal",
            entity_id=str(uuid4()),
            **pagination,
        )