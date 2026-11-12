import sqlite3
from datetime import date
from uuid import uuid4

from goal_tracker.domain.enums import GoalLifecycle
from goal_tracker.domain.models import GoalCreate, PeriodSpec


class GoalRepository:
    """SQLite queries for periods, goals and milestones."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _require_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "Repository writes require an explicit transaction."
            )

    def find_period(self, period: PeriodSpec) -> dict | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM periods
            WHERE kind = ?
              AND start_date = ?
              AND end_date = ?
              AND timezone = ?
            """,
            (
                period.kind.value,
                period.start_date.isoformat(),
                period.end_date.isoformat(),
                period.timezone,
            ),
        ).fetchone()

        return dict(row) if row is not None else None

    def insert_period(self, period_id: str, period: PeriodSpec) -> None:
        self._require_transaction()

        self.connection.execute(
            """
            INSERT INTO periods (
                id, kind, start_date, end_date, timezone
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                period_id,
                period.kind.value,
                period.start_date.isoformat(),
                period.end_date.isoformat(),
                period.timezone,
            ),
        )

    def insert_goal(
        self,
        goal_id: str,
        period_id: str,
        goal: GoalCreate,
        effective_date: date,
        *,
        is_original_plan: bool,
    ) -> None:
        self._require_transaction()

        predecessor_id = (
            str(goal.predecessor_goal_id)
            if goal.predecessor_goal_id is not None
            else None
        )

        self.connection.execute(
            """
            INSERT INTO goals (
                id,
                period_id,
                predecessor_goal_id,
                current_revision,
                is_original_plan
            )
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                goal_id,
                period_id,
                predecessor_id,
                int(is_original_plan),
            ),
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
            VALUES (?, 1, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                goal_id,
                goal.title,
                goal.description,
                goal.expected_outcome,
                (
                    goal.target_date.isoformat()
                    if goal.target_date is not None
                    else None
                ),
                effective_date.isoformat(),
                "Goal created.",
            ),
        )

        for milestone in goal.milestones:
            milestone_id = str(uuid4())
            due_date = (
                milestone.due_date
                or goal.target_date
                or goal.period.end_date
            )

            self.connection.execute(
                """
                INSERT INTO milestones (
                    id, goal_id, is_baseline
                )
                VALUES (?, ?, 1)
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
                    milestone.description,
                    due_date.isoformat(),
                    effective_date.isoformat(),
                    "Milestone created.",
                ),
            )

    def get_goal(self, goal_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT
                g.id,
                g.period_id,
                g.predecessor_goal_id,
                g.is_original_plan,
                g.created_at,
                r.revision,
                r.title,
                r.description,
                r.expected_outcome,
                r.target_date,
                r.lifecycle_status,
                r.effective_date,
                p.kind AS period_kind,
                p.start_date AS period_start,
                p.end_date AS period_end,
                p.timezone,
                p.planning_closed_at
            FROM goals AS g
            JOIN goal_revisions AS r
              ON r.goal_id = g.id
             AND r.revision = g.current_revision
            JOIN periods AS p
              ON p.id = g.period_id
            WHERE g.id = ?
            """,
            (goal_id,),
        ).fetchone()

        if row is None:
            return None

        goal = dict(row)
        goal["is_original_plan"] = bool(goal["is_original_plan"])

        goal["period"] = {
            "id": goal.pop("period_id"),
            "kind": goal.pop("period_kind"),
            "start_date": goal.pop("period_start"),
            "end_date": goal.pop("period_end"),
            "timezone": goal.pop("timezone"),
            "planning_closed_at": goal.pop("planning_closed_at"),
        }

        milestone_rows = self.connection.execute(
            """
            SELECT
                m.id,
                m.is_baseline,
                r.revision,
                r.description,
                r.due_date,
                r.is_included
            FROM milestones AS m
            JOIN milestone_revisions AS r
              ON r.milestone_id = m.id
            WHERE m.goal_id = ?
              AND r.revision = (
                  SELECT MAX(history.revision)
                  FROM milestone_revisions AS history
                  WHERE history.milestone_id = m.id
              )
            ORDER BY m.created_at, m.id
            """,
            (goal_id,),
        ).fetchall()

        goal["milestones"] = []

        for milestone_row in milestone_rows:
            milestone = dict(milestone_row)
            milestone["is_baseline"] = bool(milestone["is_baseline"])
            milestone["is_included"] = bool(milestone["is_included"])
            goal["milestones"].append(milestone)

        return goal

    def list_goals(
        self,
        *,
        period_id: str | None = None,
        lifecycle_status: GoalLifecycle | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError(
                "Limit must be between 1 and 100; offset cannot be negative."
            )

        query = """
            SELECT g.id
            FROM goals AS g
            JOIN goal_revisions AS r
              ON r.goal_id = g.id
             AND r.revision = g.current_revision
            WHERE 1 = 1
        """
        parameters = []

        if period_id is not None:
            query += " AND g.period_id = ?"
            parameters.append(period_id)

        if lifecycle_status is not None:
            query += " AND r.lifecycle_status = ?"
            parameters.append(lifecycle_status.value)

        query += " ORDER BY g.created_at, g.id LIMIT ? OFFSET ?"
        parameters.extend([limit, offset])

        rows = self.connection.execute(
            query,
            parameters,
        ).fetchall()

        goals = []

        for row in rows:
            goal = self.get_goal(row["id"])

            if goal is not None:
                goals.append(goal)

        return goals