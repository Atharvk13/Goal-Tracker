import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
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
from goal_tracker.db.repositories.progress import ProgressRepository
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import (
    AppError,
    ConflictError,
    DomainValidationError,
    DuplicateReviewRequiredError,
    NotFoundError,
    StorageError,
)
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.domain.progress import ActivityKind, DailyProgressInput
from goal_tracker.progress.duplicates import calculate_entry_hash


class ProgressService:
    """Coordinate daily progress recording and retrieval."""

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
        self.logger = logging.getLogger("goal_tracker.progress")

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
                "The progress operation conflicts with existing records.",
                request_id=request_id,
            ) from error

        except (sqlite3.Error, OSError, ValueError) as error:
            self.logger.error(
                "Progress storage failed (%s).",
                type(error).__name__,
                extra={"request_id": request_id or "-"},
            )
            raise StorageError(
                "The progress operation could not be completed safely.",
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

    def _goal_snapshot(
        self,
        connection: sqlite3.Connection,
        goal_id: str,
        activity_date: date,
    ) -> dict:
        """Build the goal view effective on the activity date."""
        goal = GoalRepository(connection).get_goal(goal_id)

        if goal is None:
            raise NotFoundError("A linked goal does not exist.")

        if goal["period"]["timezone"] != self.timezone:
            raise DomainValidationError(
                "Linked goals must use the application timezone."
            )

        history = GoalChangeRepository(connection).get_goal_history(goal_id)
        cutoff = activity_date.isoformat()

        eligible = [
            revision for revision in history["goal_revisions"]
            if revision["effective_date"] <= cutoff
        ]

        if not eligible:
            raise DomainValidationError(
                "A linked goal was not yet effective on this activity date."
            )

        revision = max(
            eligible,
            key=lambda item: (item["effective_date"], item["revision"]),
        )

        if revision["lifecycle_status"] != "active":
            raise DomainValidationError(
                "A linked goal was not active on this activity date."
            )

        latest_milestones = {}

        for item in history["milestone_revisions"]:
            if item["effective_date"] > cutoff:
                continue

            milestone_id = item["milestone_id"]
            previous = latest_milestones.get(milestone_id)

            if previous is None or (
                item["effective_date"], item["revision"]
            ) > (
                previous["effective_date"], previous["revision"]
            ):
                latest_milestones[milestone_id] = item

        snapshot = dict(goal)

        for field in (
            "revision",
            "title",
            "description",
            "expected_outcome",
            "target_date",
            "lifecycle_status",
            "effective_date",
        ):
            snapshot[field] = revision[field]

        snapshot["milestones"] = [
            {
                "id": milestone_id,
                "revision": milestone["revision"],
                "description": milestone["description"],
                "due_date": milestone["due_date"],
                "is_included": bool(milestone["is_included"]),
                "is_baseline": bool(milestone["is_baseline"]),
            }
            for milestone_id, milestone in latest_milestones.items()
            if milestone["is_included"]
        ]

        return snapshot

    def _validate_links(
        self,
        connection: sqlite3.Connection,
        entry: DailyProgressInput,
    ) -> None:
        progress = ProgressRepository(connection)
        snapshots = {}

        for activity in entry.activities:
            goal_ids = {str(goal_id) for goal_id in activity.goal_ids}

            if activity.updates_activity_id is not None:
                previous = progress.get_activity(
                    str(activity.updates_activity_id)
                )

                if previous is None:
                    raise NotFoundError(
                        "The activity being updated does not exist."
                    )

                if (
                    previous["kind"] != ActivityKind.ACTIVITY.value
                    or previous["updates_activity_id"] is not None
                ):
                    raise DomainValidationError(
                        "Select the original work activity when recording "
                        "a status update."
                    )

                if date.fromisoformat(
                    previous["activity_date"]
                ) > entry.activity_date:
                    raise DomainValidationError(
                        "An update cannot precede the original activity date."
                    )

                if goal_ids != set(previous["goal_ids"]):
                    raise DomainValidationError(
                        "An activity update must retain its original goal links."
                    )

            activity_goals = []

            for goal_id in goal_ids:
                if goal_id not in snapshots:
                    snapshots[goal_id] = self._goal_snapshot(
                        connection,
                        goal_id,
                        entry.activity_date,
                    )

                activity_goals.append(snapshots[goal_id])

            included_ids = {
                milestone["id"]
                for goal in activity_goals
                for milestone in goal["milestones"]
            }

            for change in activity.milestone_changes:
                if str(change.milestone_id) not in included_ids:
                    raise DomainValidationError(
                        "A milestone change must reference an included "
                        "milestone of a linked goal on this activity date."
                    )

    def _ensure_periods(
        self,
        connection: sqlite3.Connection,
        activity_date: date,
    ) -> None:
        """Include entry-only weeks and months in future reporting."""
        goals = GoalRepository(connection)

        starts = {
            PeriodKind.WEEKLY: (
                activity_date - timedelta(days=activity_date.weekday())
            ),
            PeriodKind.MONTHLY: activity_date.replace(day=1),
        }

        for kind, start_date in starts.items():
            period = PeriodSpec(
                kind=kind,
                start_date=start_date,
                timezone=self.timezone,
            )

            if goals.find_period(period) is None:
                goals.insert_period(str(uuid4()), period)

    def record_progress(self, entry: DailyProgressInput) -> dict:
        """Save a reviewed entry once, including its state events."""
        request_id = str(entry.request_id)
        operation = "add_daily_progress"

        payload_hash = calculate_payload_hash(
            entry.model_dump(mode="json", exclude={"request_id"})
        )

        try:
            with self._session(
                write=True,
                request_id=request_id,
            ) as connection:
                operations = OperationRepository(connection)
                progress = ProgressRepository(connection)
                previous = operations.find_request(request_id)

                if previous is not None:
                    if (
                        previous["operation"] != operation
                        or previous["payload_hash"] != payload_hash
                    ):
                        raise ConflictError(
                            "This request ID was already used for different input."
                        )

                    if "entry" not in previous["response"]:
                        raise StorageError(
                            "The saved request response is incomplete."
                        )

                    return previous["response"]

                if entry.supersedes_entry_id is not None:
                    raise DomainValidationError(
                        "Replacing an earlier entry is not available yet."
                    )

                if entry.activity_date > self._today():
                    raise DomainValidationError(
                        "Progress cannot be recorded for a future date."
                    )

                normalized_hash = calculate_entry_hash(entry.raw_text)

                duplicates = progress.find_duplicates(
                    entry.activity_date,
                    normalized_hash,
                )

                if duplicates and not entry.allow_duplicate:
                    raise DuplicateReviewRequiredError(
                        "A similar entry already exists on this date. "
                        "Review it before confirming another entry."
                    )

                self._validate_links(connection, entry)
                self._ensure_periods(connection, entry.activity_date)

                entry_id = str(uuid4())

                operations.record_request(
                    request_id=request_id,
                    operation=operation,
                    payload_hash=payload_hash,
                    response={},
                )

                progress.save_entry(
                    entry_id=entry_id,
                    entry=entry,
                    normalized_hash=normalized_hash,
                )

                saved = progress.get_entry(entry_id)

                if saved is None:
                    raise StorageError(
                        "The saved progress entry could not be retrieved."
                    )

                result = {
                    "request_id": request_id,
                    "entry": saved,
                }

                operations.complete_request_response(request_id, result)

                operations.record_audit_event(
                    entity_type="entry",
                    entity_id=entry_id,
                    action="created",
                    before=None,
                    after=saved,
                    reason=(
                        entry.correction_reason or "Daily progress recorded."
                    ),
                    request_id=request_id,
                )

        except AppError as error:
            if error.request_id is None:
                error.request_id = request_id
            raise

        self.logger.info(
            "Daily progress recorded.",
            extra={"request_id": request_id},
        )

        return result

    def get_entry(self, entry_id: str | UUID) -> dict:
        normalized_id = self._normalize_id(entry_id)

        with self._session(write=False) as connection:
            entry = ProgressRepository(connection).get_entry(normalized_id)

            if entry is None:
                raise NotFoundError("The requested entry does not exist.")

            return entry

    def get_daily_progress(
        self,
        start_date: date,
        end_date: date,
        *,
        goal_id: str | UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        if start_date > end_date:
            raise DomainValidationError(
                "The start date cannot follow the end date."
            )

        self._validate_pagination(limit, offset)
        normalized_goal_id = (
            self._normalize_id(goal_id)
            if goal_id is not None
            else None
        )

        with self._session(write=False) as connection:
            return ProgressRepository(connection).list_entries(
                start_date,
                end_date,
                goal_id=normalized_goal_id,
                limit=limit,
                offset=offset,
            )

    def list_pending_activities(
        self,
        *,
        as_of: date | None = None,
        goal_id: str | UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        self._validate_pagination(limit, offset)

        normalized_goal_id = (
            self._normalize_id(goal_id)
            if goal_id is not None
            else None
        )

        with self._session(write=False) as connection:
            return ProgressRepository(connection).list_pending_activities(
                as_of=as_of or self._today(),
                goal_id=normalized_goal_id,
                limit=limit,
                offset=offset,
            )

    def list_recordable_goals(self, activity_date: date) -> list[dict]:
        """Return goal choices valid on the entry date."""
        recordable = []

        with self._session(write=False) as connection:
            goals = GoalRepository(connection)
            offset = 0

            while True:
                page = goals.list_goals(limit=100, offset=offset)

                for goal in page:
                    try:
                        snapshot = self._goal_snapshot(
                            connection,
                            goal["id"],
                            activity_date,
                        )
                    except DomainValidationError:
                        continue

                    recordable.append(snapshot)

                if len(page) < 100:
                    break

                offset += 100

        return recordable

    def find_duplicates(
        self,
        activity_date: date,
        raw_text: str,
    ) -> list[dict]:
        """Retrieve matching text for explicit duplicate review."""
        try:
            normalized_hash = calculate_entry_hash(raw_text)
        except ValueError as error:
            raise DomainValidationError(
                "The daily entry cannot be blank."
            ) from error

        with self._session(write=False) as connection:
            return ProgressRepository(connection).find_duplicates(
                activity_date,
                normalized_hash,
            )