"""Changes Jarvis proposes and the user confirms.

A proposal stores the exact request (with its request ID) that will run on
confirmation, so confirming twice never saves twice. Nothing here runs a
change by itself: `execute` is called only from the confirm action.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.goal_changes import GoalUpdate
from goal_tracker.domain.models import GoalCreate
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.llm.recommendations import ReportRecommendations
from goal_tracker.mcp import services as service_factories
from goal_tracker.progress.workflow import (
    link_independent_work,
    load_all_pending,
    updatable_pending,
)
from goal_tracker.reporting.advice import completed_period_spec


class ProposalError(Exception):
    """A proposal can no longer be applied (records changed meanwhile)."""


@dataclass
class AgentServices:
    goals: object
    progress: object
    analysis: object
    reports: object
    timezone: str


def services_for(context) -> AgentServices:
    """The same in-process services the web pages and terminal use."""
    return AgentServices(
        goals=service_factories.get_goal_service(context),
        progress=service_factories.get_progress_service(context),
        analysis=service_factories.get_analysis_service(context),
        reports=service_factories.get_report_service(context),
        timezone=context.settings.timezone,
    )


# ------------------------------------------------------------------- store


class ProposalStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    @staticmethod
    def _row(row) -> dict:
        proposal = dict(row)
        proposal["details"] = json.loads(proposal["details"])
        proposal["payload"] = json.loads(proposal["payload"])
        return proposal

    def add(
        self,
        *,
        kind: str,
        source: str,
        summary: str,
        details: list[tuple[str, str]] | None = None,
        payload: dict | None = None,
        dedupe_key: str | None = None,
    ) -> dict | None:
        """Store a pending proposal; None if the same one was made before."""
        proposal_id = str(uuid4())
        with open_database(self.database_path) as connection:
            with transaction(connection, write=True):
                try:
                    connection.execute(
                        """
                        INSERT INTO agent_proposals (
                            id, kind, source, summary, details, payload, dedupe_key
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            proposal_id,
                            kind,
                            source,
                            summary,
                            json.dumps(details or []),
                            json.dumps(payload or {}, default=str),
                            dedupe_key,
                        ),
                    )
                except sqlite3.IntegrityError:
                    return None
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict | None:
        with open_database(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM agent_proposals WHERE id = ?", (str(proposal_id),)
            ).fetchone()
        return self._row(row) if row else None

    def by_status(self, status: str = "pending", limit: int = 50) -> list[dict]:
        with open_database(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_proposals
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def recent_resolved(self, limit: int = 10) -> list[dict]:
        with open_database(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_proposals
                WHERE status != 'pending'
                ORDER BY resolved_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def count_pending(self) -> int:
        with open_database(self.database_path) as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM agent_proposals WHERE status = 'pending'"
            ).fetchone()[0]

    def resolve(self, proposal_id: str, status: str, result: str | None = None) -> dict:
        """Close a pending proposal. Already-closed proposals stay as they are."""
        with open_database(self.database_path) as connection:
            with transaction(connection, write=True):
                connection.execute(
                    """
                    UPDATE agent_proposals
                    SET status = ?, result = ?,
                        resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status = 'pending'
                    """,
                    (status, result, str(proposal_id)),
                )
        return self.get(proposal_id)

    # Scheduled runs ----------------------------------------------------

    def has_run(self, job: str, run_key: str) -> bool:
        with open_database(self.database_path) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM agent_runs WHERE job = ? AND run_key = ?",
                    (job, run_key),
                ).fetchone()
                is not None
            )

    def mark_run(self, job: str, run_key: str, status: str = "done") -> None:
        with open_database(self.database_path) as connection:
            with transaction(connection, write=True):
                connection.execute(
                    "INSERT OR IGNORE INTO agent_runs (job, run_key, status) VALUES (?, ?, ?)",
                    (job, run_key, status),
                )


# ----------------------------------------------------------------- execute


def _progress_entry(payload: dict, raw_text: str, activity: dict) -> DailyProgressInput:
    # The activity ID is fixed when the proposal is made, so a retried
    # confirmation rebuilds exactly the same request (idempotent).
    return DailyProgressInput.model_validate(
        {
            "activity_date": payload["activity_date"],
            "raw_text": raw_text,
            "activities": [{**activity, "id": payload["activity_uuid"]}],
            "request_id": payload["request_id"],
        }
    )


def ids() -> dict:
    """Fresh request and activity IDs for a new proposal payload."""
    return {"request_id": str(uuid4()), "activity_uuid": str(uuid4())}


def execute(proposal: dict, services: AgentServices, today: date) -> str:
    """Apply a confirmed proposal through the normal services.

    Returns a short result message. Raises ProposalError when the records
    changed so the proposal no longer applies, or AppError from a service.
    """
    kind = proposal["kind"]
    payload = proposal["payload"]

    try:
        if kind == "note":
            return "Noted."

        if kind == "update_work_status":
            recordable = {g["id"]: g for g in services.progress.list_recordable_goals(today)}
            still_open = {
                activity["id"]
                for activity in updatable_pending(
                    load_all_pending(services.progress, today), recordable
                )
            }
            if payload["activity_id"] not in still_open:
                raise ProposalError("This work is no longer pending, or its goal no longer accepts progress.")

            entry = _progress_entry(
                {**payload, "activity_date": today.isoformat()},
                f"Status update via Jarvis: {payload['description']} is now "
                f"{payload['state'].replace('_', ' ')}.",
                {
                    "description": payload["description"],
                    "state": payload["state"],
                    "goal_ids": payload["goal_ids"],
                    "updates_activity_id": payload["activity_id"],
                },
            )
            services.progress.record_progress(entry)
            return "Status updated."

        if kind == "log_work":
            activity = {
                "description": payload["description"],
                "state": payload["state"],
                "minutes": payload["minutes"],
                "goal_ids": payload["goal_ids"],
            }
            entry = _progress_entry(payload, f"Logged via Jarvis: {payload['description']}", activity)
            if not payload["goal_ids"]:
                entry = link_independent_work(
                    entry,
                    {str(item.id) for item in entry.activities},
                    services.goals,
                    services.timezone,
                )
            services.progress.record_progress(entry)
            return "Work logged."

        if kind == "complete_milestone":
            entry = _progress_entry(
                payload,
                f"Completed milestone via Jarvis: {payload['milestone']}",
                {
                    "description": f"Completed milestone: {payload['milestone']}",
                    "state": "completed",
                    "goal_ids": [payload["goal_id"]],
                    "milestone_changes": [
                        {"milestone_id": payload["milestone_id"], "state": "done"}
                    ],
                },
            )
            services.progress.record_progress(entry)
            return "Milestone marked done."

        if kind == "add_milestone":
            services.goals.update_goal(
                GoalUpdate.model_validate(
                    {
                        "goal_id": payload["goal_id"],
                        "expected_revision": payload["expected_revision"],
                        "reason": "Milestone added via Jarvis.",
                        "add_milestones": [{"description": payload["description"]}],
                        "request_id": payload["request_id"],
                    }
                )
            )
            return "Milestone added."

        if kind == "move_milestone":
            services.goals.update_goal(
                GoalUpdate.model_validate(
                    {
                        "goal_id": payload["goal_id"],
                        "expected_revision": payload["expected_revision"],
                        "reason": "Milestone due date moved via Jarvis.",
                        "milestone_changes": [
                            {"milestone_id": payload["milestone_id"], "due_date": payload["due_date"]}
                        ],
                        "request_id": payload["request_id"],
                    }
                )
            )
            return "Due date moved."

        if kind == "create_goal":
            result = services.goals.create_goal(GoalCreate.model_validate(payload["goal"]))
            return f"Goal created: {result['goal']['title']}."

        if kind == "save_report":
            result = services.reports.generate_report(
                completed_period_spec(payload["period"]),
                request_id=payload["request_id"],
                advisory=(
                    ReportRecommendations.model_validate(payload["advisory"])
                    if payload.get("advisory")
                    else None
                ),
                model_provenance=payload.get("provenance"),
                expected_evidence_hash=payload.get("evidence_hash"),
            )
            return f"Report saved (/reports/{result['report']['id']})."

    except ValidationError as error:
        raise ProposalError("; ".join(item["msg"] for item in error.errors())) from error

    raise ProposalError(f"Unknown proposal kind: {kind}")


def confirm(store: ProposalStore, proposal_id: str, services: AgentServices, today: date) -> dict:
    """Run a pending proposal once and record the outcome."""
    proposal = store.get(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal["status"] != "pending":
        return proposal  # already handled (double click, second tab)

    try:
        message = execute(proposal, services, today)
    except (ProposalError, AppError) as error:
        return store.resolve(proposal_id, "failed", str(error))

    return store.resolve(proposal_id, "confirmed", message)
