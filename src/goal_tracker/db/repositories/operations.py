import hashlib
import json
import sqlite3
from uuid import uuid4


def _canonical_json(value: dict) -> str:
    """Serialize JSON-compatible data consistently."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def calculate_payload_hash(payload: dict) -> str:
    """Identify a request's contents without storing another input copy."""
    serialized = _canonical_json(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class OperationRepository:
    """Queries for request deduplication and audit history."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _require_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "Repository writes require an explicit transaction."
            )

    def find_request(self, request_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT
                request_id,
                operation,
                payload_hash,
                response_json,
                recorded_at
            FROM mutation_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        if row is None:
            return None

        request = dict(row)
        response = json.loads(request.pop("response_json"))

        if not isinstance(response, dict):
            raise ValueError("Stored request response must be a JSON object.")

        request["response"] = response
        return request

    def record_request(
        self,
        *,
        request_id: str,
        operation: str,
        payload_hash: str,
        response: dict,
    ) -> None:
        self._require_transaction()

        self.connection.execute(
            """
            INSERT INTO mutation_requests (
                request_id,
                operation,
                payload_hash,
                response_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                request_id,
                operation,
                payload_hash,
                _canonical_json(response),
            ),
        )

    def complete_request_response(
        self,
        request_id: str,
        response: dict,
    ) -> None:
        """Complete a reserved request within the same transaction."""
        self._require_transaction()

        if not response:
            raise ValueError("A completed response cannot be empty.")

        cursor = self.connection.execute(
            """
            UPDATE mutation_requests
            SET response_json = ?
            WHERE request_id = ? AND response_json = '{}'
            """,
            (_canonical_json(response), request_id),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "The reserved request is missing or already completed."
            )

    def record_audit_event(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        before: dict | None,
        after: dict | None,
        reason: str,
        request_id: str,
    ) -> str:
        self._require_transaction()
        event_id = str(uuid4())

        self.connection.execute(
            """
            INSERT INTO audit_events (
                id,
                entity_type,
                entity_id,
                action,
                before_json,
                after_json,
                reason,
                request_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                entity_type,
                entity_id,
                action,
                _canonical_json(before) if before is not None else None,
                _canonical_json(after) if after is not None else None,
                reason,
                request_id,
            ),
        )

        return event_id

    def list_audit_events(
        self,
        *,
        entity_type: str,
        entity_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError(
                "Limit must be between 1 and 100; offset cannot be negative."
            )

        rows = self.connection.execute(
            """
            SELECT *
            FROM audit_events
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY sequence
            LIMIT ? OFFSET ?
            """,
            (entity_type, entity_id, limit, offset),
        ).fetchall()

        events = []

        for row in rows:
            event = dict(row)

            before_json = event.pop("before_json")
            after_json = event.pop("after_json")

            event["before"] = (
                json.loads(before_json)
                if before_json is not None
                else None
            )
            event["after"] = (
                json.loads(after_json)
                if after_json is not None
                else None
            )

            events.append(event)

        return events