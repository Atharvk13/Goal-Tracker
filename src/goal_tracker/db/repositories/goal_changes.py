import sqlite3
from datetime import date
from uuid import uuid4

from goal_tracker.domain.errors import ConflictError


class GoalChangeRepository:
    """Database operations that preserve goal and milestone revisions."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _require_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "Repository writes require an explicit transaction."
            )

    def get_period(self, period_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM periods WHERE id = ?",
            (period_id,),
        ).fetchone()

        return dict(row) if row is not None else None

    def finalize_period_planning(
        self,
        period_id: str,
        closed_at: str,
    ) -> None:
        self._require_transaction()

        cursor = self.connection.execute(
            """
            UPDATE periods
            SET planning_closed_at = ?
            WHERE id = ? AND planning_closed_at IS NULL
            """,
            (closed_at, period_id),
        )

        if cursor.rowcount != 1:
            raise ConflictError(
                "The period is missing or its initial plan is already finalized."
            )

    def append_goal_revision(
        self,
        *,
        goal_id: str,
        expected_revision: int,
        details: dict,
        effective_date: date,
        reason: str,
    ) -> int:
        self._require_transaction()
        next_revision = expected_revision + 1

        cursor = self.connection.execute(
            """
            UPDATE goals
            SET current_revision = ?
            WHERE id = ? AND current_revision = ?
            """,
            (next_revision, goal_id, expected_revision),
        )

        if cursor.rowcount != 1:
            raise ConflictError(
                "The goal changed. Reload it before editing."
            )

        self.connection.execute(
            """
            INSERT INTO goal_revisions (
                goal_id,
                revision,
                title,
                description,
                expected_outcome,
                target_date,
                lifecycle_status,
                effective_date,
                change_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal_id,
                next_revision,
                details["title"],
                details["description"],
                details["expected_outcome"],
                details["target_date"],
                details["lifecycle_status"],
                effective_date.isoformat(),
                reason,
            ),
        )

        return next_revision

    def insert_milestone(
        self,
        *,
        goal_id: str,
        description: str,
        due_date: date,
        effective_date: date,
        reason: str,
    ) -> str:
        self._require_transaction()
        milestone_id = str(uuid4())

        self.connection.execute(
            """
            INSERT INTO milestones (id, goal_id, is_baseline)
            VALUES (?, ?, 0)
            """,
            (milestone_id, goal_id),
        )

        self.connection.execute(
            """
            INSERT INTO milestone_revisions (
                milestone_id,
                revision,
                description,
                due_date,
                is_included,
                effective_date,
                change_reason
            )
            VALUES (?, 1, ?, ?, 1, ?, ?)
            """,
            (
                milestone_id,
                description,
                due_date.isoformat(),
                effective_date.isoformat(),
                reason,
            ),
        )

        return milestone_id

    def append_milestone_revision(
        self,
        *,
        milestone_id: str,
        expected_revision: int,
        details: dict,
        effective_date: date,
        reason: str,
    ) -> int:
        self._require_transaction()

        row = self.connection.execute(
            """
            SELECT MAX(revision) AS revision
            FROM milestone_revisions
            WHERE milestone_id = ?
            """,
            (milestone_id,),
        ).fetchone()

        if row["revision"] != expected_revision:
            raise ConflictError(
                "The milestone changed. Reload the goal before editing."
            )

        next_revision = expected_revision + 1

        self.connection.execute(
            """
            INSERT INTO milestone_revisions (
                milestone_id,
                revision,
                description,
                due_date,
                is_included,
                effective_date,
                change_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                milestone_id,
                next_revision,
                details["description"],
                details["due_date"],
                int(details["is_included"]),
                effective_date.isoformat(),
                reason,
            ),
        )

        return next_revision

    def get_goal_history(self, goal_id: str) -> dict:
        goal_rows = self.connection.execute(
            """
            SELECT *
            FROM goal_revisions
            WHERE goal_id = ?
            ORDER BY revision
            """,
            (goal_id,),
        ).fetchall()

        milestone_rows = self.connection.execute(
            """
            SELECT
                r.*,
                m.is_baseline
            FROM milestone_revisions AS r
            JOIN milestones AS m
              ON m.id = r.milestone_id
            WHERE m.goal_id = ?
            ORDER BY m.created_at, m.id, r.revision
            """,
            (goal_id,),
        ).fetchall()

        milestones = []

        for row in milestone_rows:
            milestone = dict(row)
            milestone["is_baseline"] = bool(milestone["is_baseline"])
            milestone["is_included"] = bool(milestone["is_included"])
            milestones.append(milestone)

        return {
            "goal_revisions": [dict(row) for row in goal_rows],
            "milestone_revisions": milestones,
        }