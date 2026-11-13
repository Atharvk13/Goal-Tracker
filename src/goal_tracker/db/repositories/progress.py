import sqlite3
from datetime import date
from uuid import uuid4

from goal_tracker.domain.progress import DailyProgressInput


class ProgressRepository:
    """Store journal entries and retrieve recorded progress."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _require_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "Repository writes require an explicit transaction."
            )

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError(
                "Limit must be between 1 and 100; offset cannot be negative."
            )

    def find_duplicates(
        self,
        activity_date: date,
        normalized_hash: str,
    ) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT e.id, e.activity_date, e.raw_text, e.created_at
            FROM entries AS e
            WHERE e.activity_date = ?
              AND e.normalized_text_hash = ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM entries AS replacement
                  WHERE replacement.supersedes_entry_id = e.id
              )
            ORDER BY e.created_at DESC, e.id
            LIMIT 5
            """,
            (activity_date.isoformat(), normalized_hash),
        ).fetchall()

        return [dict(row) for row in rows]

    def save_entry(
        self,
        *,
        entry_id: str,
        entry: DailyProgressInput,
        normalized_hash: str,
    ) -> None:
        """Save an entry and its explicit state events."""
        self._require_transaction()

        self.connection.execute(
            """
            INSERT INTO entries (
                id,
                activity_date,
                raw_text,
                source,
                normalized_text_hash,
                request_id,
                supersedes_entry_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                entry.activity_date.isoformat(),
                entry.raw_text,
                entry.source.value,
                normalized_hash,
                str(entry.request_id),
                (
                    str(entry.supersedes_entry_id)
                    if entry.supersedes_entry_id is not None
                    else None
                ),
            ),
        )

        for activity in entry.activities:
            activity_id = str(activity.id)
            previous_id = (
                str(activity.updates_activity_id)
                if activity.updates_activity_id is not None
                else None
            )

            self.connection.execute(
                """
                INSERT INTO activities (
                    id,
                    entry_id,
                    kind,
                    description,
                    initial_state,
                    minutes,
                    evidence_quote,
                    updates_activity_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity_id,
                    entry_id,
                    activity.kind.value,
                    activity.description,
                    activity.state.value,
                    activity.minutes,
                    activity.evidence_quote,
                    previous_id,
                ),
            )

            for goal_id in activity.goal_ids:
                self.connection.execute(
                    """
                    INSERT INTO activity_goal_links (activity_id, goal_id)
                    VALUES (?, ?)
                    """,
                    (activity_id, str(goal_id)),
                )

            self.connection.execute(
                """
                INSERT INTO activity_events (
                    id,
                    activity_id,
                    state,
                    effective_date,
                    source_entry_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    previous_id or activity_id,
                    activity.state.value,
                    entry.activity_date.isoformat(),
                    entry_id,
                ),
            )

            for change in activity.milestone_changes:
                self.connection.execute(
                    """
                    INSERT INTO milestone_events (
                        id,
                        milestone_id,
                        state,
                        effective_date,
                        source_entry_id,
                        source_activity_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(change.milestone_id),
                        change.state.value,
                        entry.activity_date.isoformat(),
                        entry_id,
                        activity_id,
                    ),
                )

    def get_activity(
        self,
        activity_id: str,
        *,
        as_of: date | None = None,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT a.*, e.activity_date
            FROM activities AS a
            JOIN entries AS e ON e.id = a.entry_id
            WHERE a.id = ?
            """,
            (activity_id,),
        ).fetchone()

        if row is None:
            return None

        activity = dict(row)
        cutoff = (as_of or date.max).isoformat()

        latest = self.connection.execute(
            """
            SELECT state
            FROM activity_events
            WHERE activity_id = ? AND effective_date <= ?
            ORDER BY effective_date DESC, sequence DESC
            LIMIT 1
            """,
            (activity_id, cutoff),
        ).fetchone()

        activity["state"] = (
            latest["state"]
            if latest is not None
            else activity["initial_state"]
        )

        activity["goal_ids"] = [
            item["goal_id"]
            for item in self.connection.execute(
                """
                SELECT goal_id
                FROM activity_goal_links
                WHERE activity_id = ?
                ORDER BY goal_id
                """,
                (activity_id,),
            ).fetchall()
        ]

        activity["milestone_changes"] = [
            dict(item)
            for item in self.connection.execute(
                """
                SELECT milestone_id, state
                FROM milestone_events
                WHERE source_activity_id = ?
                ORDER BY sequence
                """,
                (activity_id,),
            ).fetchall()
        ]

        return activity

    def get_entry(self, entry_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()

        if row is None:
            return None

        entry = dict(row)
        entry["activities"] = []

        rows = self.connection.execute(
            """
            SELECT id
            FROM activities
            WHERE entry_id = ?
            ORDER BY rowid
            """,
            (entry_id,),
        ).fetchall()

        for item in rows:
            activity = self.get_activity(item["id"])

            if activity is not None:
                # Entry history shows the state recorded in this entry.
                activity["state"] = activity["initial_state"]
                entry["activities"].append(activity)

        return entry

    def list_entries(
        self,
        start_date: date,
        end_date: date,
        *,
        goal_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        self._validate_pagination(limit, offset)

        query = """
            SELECT e.id
            FROM entries AS e
            WHERE e.activity_date BETWEEN ? AND ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM entries AS replacement
                  WHERE replacement.supersedes_entry_id = e.id
              )
        """
        parameters = [
            start_date.isoformat(),
            end_date.isoformat(),
        ]

        if goal_id is not None:
            query += """
                AND EXISTS (
                    SELECT 1
                    FROM activities AS a
                    JOIN activity_goal_links AS link
                      ON link.activity_id = a.id
                    WHERE a.entry_id = e.id AND link.goal_id = ?
                )
            """
            parameters.append(goal_id)

        query += """
            ORDER BY e.activity_date DESC, e.created_at DESC, e.id
            LIMIT ? OFFSET ?
        """
        parameters.extend([limit, offset])

        rows = self.connection.execute(query, parameters).fetchall()
        entries = []

        for row in rows:
            entry = self.get_entry(row["id"])

            if entry is not None:
                entries.append(entry)

        return entries

    def list_pending_activities(
        self,
        *,
        as_of: date,
        goal_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        self._validate_pagination(limit, offset)

        query = """
            WITH current_states AS (
                SELECT
                    a.id,
                    a.rowid AS insertion_order,
                    e.activity_date,
                    COALESCE(
                        (
                            SELECT event.state
                            FROM activity_events AS event
                            WHERE event.activity_id = a.id
                              AND event.effective_date <= ?
                            ORDER BY
                                event.effective_date DESC,
                                event.sequence DESC
                            LIMIT 1
                        ),
                        a.initial_state
                    ) AS state
                FROM activities AS a
                JOIN entries AS e ON e.id = a.entry_id
                WHERE a.kind = 'activity'
                  AND a.updates_activity_id IS NULL
                  AND e.activity_date <= ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM entries AS replacement
                      WHERE replacement.supersedes_entry_id = e.id
                  )
            )
            SELECT current.id
            FROM current_states AS current
            WHERE current.state IN ('pending', 'in_progress')
        """
        cutoff = as_of.isoformat()
        parameters = [cutoff, cutoff]

        if goal_id is not None:
            query += """
                AND EXISTS (
                    SELECT 1
                    FROM activity_goal_links AS link
                    WHERE link.activity_id = current.id
                      AND link.goal_id = ?
                )
            """
            parameters.append(goal_id)

        query += """
            ORDER BY current.activity_date, current.insertion_order
            LIMIT ? OFFSET ?
        """
        parameters.extend([limit, offset])

        rows = self.connection.execute(query, parameters).fetchall()
        activities = []

        for row in rows:
            activity = self.get_activity(row["id"], as_of=as_of)

            if activity is not None:
                activities.append(activity)

        return activities