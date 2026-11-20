"""Stored AI suggestions (migration 004)."""

import json
from pathlib import Path

from goal_tracker.db.connection import open_database, transaction


class SuggestionStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def get(self, kind: str, key: str) -> dict | None:
        with open_database(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM ai_suggestions WHERE kind = ? AND subject_key = ?",
                (kind, key),
            ).fetchone()

        if row is None:
            return None

        stored = dict(row)
        stored["content"] = json.loads(stored["content"])
        return stored

    def save(
        self,
        kind: str,
        key: str,
        *,
        evidence_hash: str,
        content: dict,
        model: str,
    ) -> dict:
        """Store suggestions, replacing earlier ones for the same subject."""
        with open_database(self.database_path) as connection:
            with transaction(connection, write=True):
                connection.execute(
                    """
                    INSERT INTO ai_suggestions (
                        kind, subject_key, evidence_hash, content, model
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (kind, subject_key) DO UPDATE SET
                        evidence_hash = excluded.evidence_hash,
                        content = excluded.content,
                        model = excluded.model,
                        created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (kind, key, evidence_hash, json.dumps(content), model),
                )

        return self.get(kind, key)
