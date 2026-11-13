import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.repositories.goal_changes import GoalChangeRepository
from goal_tracker.db.repositories.goals import GoalRepository
from goal_tracker.db.repositories.operations import (
    OperationRepository,
    calculate_payload_hash,
)
from goal_tracker.domain.enums import GoalLifecycle
from goal_tracker.domain.errors import (
    AppError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    StorageError,
)
from goal_tracker.domain.goal_changes import (
    GoalLifecycleChange,
    GoalUpdate,
    PlanFinalize,
)
from goal_tracker.domain.models import GoalCreate
from goal_tracker.goals.changes import (
    apply_goal_update,
    apply_lifecycle_change,
    apply_plan_finalize,
)


MutationRequest = GoalCreate | GoalUpdate | GoalLifecycleChange | PlanFinalize
MutationHandler = Callable[
    [sqlite3.Connection, date],
    tuple[dict | None, dict],
]


class GoalService:
    """Coordinate goal operations, transactions and audit history."""

    def __init__(
        self,
        database_path: Path,
        *,
        timezone: str = "Asia/Kolkata",
        today: Callable[[], date] | None = None,
    ):
        database_path = database_path.expanduser()

        if not database_path.is_absolute():
            raise DomainValidationError(
                "The database path must be absolute."
            )

        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise DomainValidationError(
                "The application timezone is invalid."
            ) from error

        self.database_path = database_path.resolve()
        self.timezone = timezone
        self._today = (
            today if today is not None
            else lambda: datetime.now(zone).date()
        )
        self.logger = logging.getLogger("goal_tracker.goals")

    @contextmanager
    def _session(
        self,
        *,
        write: bool,
        request_id: str | None = None,
    ) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise StorageError(
                "The database is not initialized. Start the application first.",
                request_id=request_id,
            )

        try:
            with open_database(self.database_path) as connection:
                with transaction(connection, write=write):
                    yield connection

        except sqlite3.IntegrityError as error:
            raise ConflictError(
                "The operation conflicts with existing records.",
                request_id=request_id,
            ) from error

        except (sqlite3.Error, OSError, ValueError) as error:
            self.logger.error(
                "Database operation failed (%s).",
                type(error).__name__,
                extra={"request_id": request_id or "-"},
            )
            raise StorageError(
                "The database operation could not be completed safely.",
                request_id=request_id,
            ) from error

    @staticmethod
    def _normalize_id(value: str | UUID) -> str:
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as error:
            raise DomainValidationError(
                "Record IDs must be valid UUIDs."
            ) from error

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100 or offset < 0:
            raise DomainValidationError(
                "Limit must be between 1 and 100; offset cannot be negative."
            )

    def _execute_mutation(
        self,
        request: MutationRequest,
        *,
        operation: str,
        entity_type: str,
        result_key: str,
        action: str,
        reason: str,
        handler: MutationHandler,
    ) -> dict:
        """Apply a mutation once and save its response and audit atomically."""
        request_id = str(request.request_id)

        payload_hash = calculate_payload_hash(
            request.model_dump(
                mode="json",
                exclude={"request_id"},
                exclude_unset=isinstance(request, GoalUpdate),
            )
        )

        try:
            with self._session(
                write=True,
                request_id=request_id,
            ) as connection:
                operations = OperationRepository(connection)
                previous = operations.find_request(request_id)

                if previous is not None:
                    if (
                        previous["operation"] != operation
                        or previous["payload_hash"] != payload_hash
                    ):
                        raise ConflictError(
                            "This request ID was already used for different input.",
                            request_id=request_id,
                        )

                    return previous["response"]

                before, after = handler(connection, self._today())

                result = {
                    "request_id": request_id,
                    result_key: after,
                }

                operations.record_request(
                    request_id=request_id,
                    operation=operation,
                    payload_hash=payload_hash,
                    response=result,
                )

                operations.record_audit_event(
                    entity_type=entity_type,
                    entity_id=after["id"],
                    action=action,
                    before=before,
                    after=after,
                    reason=reason,
                    request_id=request_id,
                )

        except AppError as error:
            if error.request_id is None:
                error.request_id = request_id
            raise

        self.logger.info(
            "%s completed.",
            operation,
            extra={"request_id": request_id},
        )

        return result

    def _apply_creation(
        self,
        connection: sqlite3.Connection,
        goal: GoalCreate,
        today: date,
    ) -> tuple[None, dict]:
        goals = GoalRepository(connection)

        if goal.period.timezone != self.timezone:
            raise DomainValidationError(
                "The goal timezone must match the application timezone."
            )

        if goal.period.end_date < today:
            raise DomainValidationError(
                "New goals must belong to the current or a future period."
            )

        if goal.predecessor_goal_id is not None:
            predecessor = goals.get_goal(str(goal.predecessor_goal_id))

            if predecessor is None:
                raise NotFoundError(
                    "The predecessor goal does not exist."
                )

            predecessor_end = date.fromisoformat(
                predecessor["period"]["end_date"]
            )

            if goal.period.start_date <= predecessor_end:
                raise DomainValidationError(
                    "A successor goal must start after its predecessor's "
                    "period ends."
                )

        period = goals.find_period(goal.period)

        if period is None:
            period_id = str(uuid4())
            goals.insert_period(period_id, goal.period)
            period = {
                "id": period_id,
                "planning_closed_at": None,
            }

        goal_id = str(uuid4())

        goals.insert_goal(
            goal_id=goal_id,
            period_id=period["id"],
            goal=goal,
            effective_date=max(today, goal.period.start_date),
            is_original_plan=period["planning_closed_at"] is None,
        )

        saved_goal = goals.get_goal(goal_id)

        if saved_goal is None:
            raise StorageError(
                "The saved goal could not be retrieved."
            )

        return None, saved_goal

    def create_goal(self, goal: GoalCreate) -> dict:
        """Create a goal once, returning the original response on retries."""
        return self._execute_mutation(
            goal,
            operation="create_goal",
            entity_type="goal",
            result_key="goal",
            action="created",
            reason="Goal created.",
            handler=lambda connection, today: self._apply_creation(
                connection,
                goal,
                today,
            ),
        )

    def update_goal(self, request: GoalUpdate) -> dict:
        """Append reviewed goal and milestone revisions."""
        return self._execute_mutation(
            request,
            operation="update_goal",
            entity_type="goal",
            result_key="goal",
            action="updated",
            reason=request.reason,
            handler=lambda connection, today: apply_goal_update(
                connection,
                request,
                today=today,
            ),
        )

    def change_lifecycle(self, request: GoalLifecycleChange) -> dict:
        """Cancel or archive a goal while preserving its records."""
        cancelling = request.action == "cancel"

        return self._execute_mutation(
            request,
            operation="cancel_goal" if cancelling else "archive_goal",
            entity_type="goal",
            result_key="goal",
            action="cancelled" if cancelling else "archived",
            reason=request.reason,
            handler=lambda connection, today: apply_lifecycle_change(
                connection,
                request,
                today=today,
            ),
        )

    def finalize_plan(self, request: PlanFinalize) -> dict:
        """Close initial planning so later goals are identified as additions."""
        return self._execute_mutation(
            request,
            operation="finalize_plan",
            entity_type="period",
            result_key="period",
            action="planning_finalized",
            reason=request.reason,
            handler=lambda connection, today: apply_plan_finalize(
                connection,
                request,
                today=today,
            ),
        )

    def get_goal(self, goal_id: str | UUID) -> dict:
        normalized_id = self._normalize_id(goal_id)

        with self._session(write=False) as connection:
            goal = GoalRepository(connection).get_goal(normalized_id)

            if goal is None:
                raise NotFoundError(
                    "The requested goal does not exist."
                )

            return goal

    def list_goals(
        self,
        *,
        period_id: str | UUID | None = None,
        lifecycle_status: GoalLifecycle | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        normalized_period_id = (
            self._normalize_id(period_id)
            if period_id is not None
            else None
        )

        self._validate_pagination(limit, offset)

        if lifecycle_status is not None:
            try:
                lifecycle_status = GoalLifecycle(lifecycle_status)
            except ValueError as error:
                raise DomainValidationError(
                    "The goal lifecycle status is invalid."
                ) from error

        with self._session(write=False) as connection:
            return GoalRepository(connection).list_goals(
                period_id=normalized_period_id,
                lifecycle_status=lifecycle_status,
                limit=limit,
                offset=offset,
            )

    def get_goal_history(
        self,
        goal_id: str | UUID,
        *,
        audit_limit: int = 100,
        audit_offset: int = 0,
    ) -> dict:
        normalized_id = self._normalize_id(goal_id)
        self._validate_pagination(audit_limit, audit_offset)

        with self._session(write=False) as connection:
            goal = GoalRepository(connection).get_goal(normalized_id)

            if goal is None:
                raise NotFoundError(
                    "The requested goal does not exist."
                )

            history = GoalChangeRepository(connection).get_goal_history(
                normalized_id
            )

            events = OperationRepository(connection).list_audit_events(
                entity_type="goal",
                entity_id=normalized_id,
                limit=audit_limit,
                offset=audit_offset,
            )

            return {
                "goal": goal,
                **history,
                "audit_events": events,
                "audit_pagination": {
                    "limit": audit_limit,
                    "offset": audit_offset,
                },
            }