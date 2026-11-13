import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from goal_tracker.analysis.service import AnalysisService
from goal_tracker.db.connection import open_database, transaction
from goal_tracker.db.repositories.operations import (
    OperationRepository,
    calculate_payload_hash,
)
from goal_tracker.db.repositories.reports import ReportRepository
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    StorageError,
)
from goal_tracker.domain.models import PeriodSpec


class ReportService:
    """Generate and retrieve saved reports for completed periods."""

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
            today
            if today is not None
            else lambda: datetime.now(zone).date()
        )
        self.logger = logging.getLogger("goal_tracker.reporting")

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
                "The report operation conflicts with existing records.",
                request_id=request_id,
            ) from error

        except (sqlite3.Error, OSError) as error:
            self.logger.error(
                "Report storage failed (%s).",
                type(error).__name__,
                extra={"request_id": request_id or "-"},
            )
            raise StorageError(
                "The report operation could not be completed.",
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
                "Limit must be between 1 and 100; "
                "offset cannot be negative."
            )

    def generate_report(
        self,
        period: PeriodSpec,
        *,
        request_id: str | UUID | None = None,
    ) -> dict:
        """Save a factual snapshot as a new report revision."""
        normalized_request_id = self._normalize_id(
            request_id if request_id is not None else uuid4()
        )

        if period.timezone != self.timezone:
            raise DomainValidationError(
                "The reporting period must use the application timezone."
            )

        today = self._today()

        if today <= period.end_date:
            raise DomainValidationError(
                "Reports are available only after the period has ended. "
                "Use the progress summary for an active period."
            )

        operation = "generate_progress_report"
        payload_hash = calculate_payload_hash(
            period.model_dump(mode="json")
        )

        with self._session(
            write=True,
            request_id=normalized_request_id,
        ) as connection:
            operations = OperationRepository(connection)
            reports = ReportRepository(connection)

            previous = operations.find_request(
                normalized_request_id
            )

            if previous is not None:
                if (
                    previous["operation"] != operation
                    or previous["payload_hash"] != payload_hash
                ):
                    raise ConflictError(
                        "This request ID was already used for different input.",
                        request_id=normalized_request_id,
                    )

                if "report" not in previous["response"]:
                    raise StorageError(
                        "The saved report response is incomplete.",
                        request_id=normalized_request_id,
                    )

                return previous["response"]

            # The write transaction prevents other writers from changing
            # records while AnalysisService reads the factual snapshot.
            # No database writes occur before that read completes.
            analysis = AnalysisService(
                self.database_path,
                timezone=self.timezone,
                today=lambda: today,
            )
            facts = analysis.analyze_period(
                period,
                as_of=period.end_date,
            )

            period_id = facts["period"]["id"]

            if period_id is None:
                raise DomainValidationError(
                    "No stored period exists for these dates. "
                    "Create goals or record progress for the period first.",
                    request_id=normalized_request_id,
                )

            evidence_hash = calculate_payload_hash(facts)
            snapshot_hash = calculate_payload_hash(
                {
                    "facts": facts,
                    "advisory": None,
                    "model_provenance": None,
                }
            )

            report_id = str(uuid4())
            revision = reports.next_revision(period_id)

            operations.record_request(
                request_id=normalized_request_id,
                operation=operation,
                payload_hash=payload_hash,
                response={},
            )

            reports.save_report(
                report_id=report_id,
                period_id=period_id,
                revision=revision,
                cutoff_date=period.end_date.isoformat(),
                facts=facts,
                advisory=None,
                model_provenance=None,
                evidence_hash=evidence_hash,
                snapshot_hash=snapshot_hash,
                request_id=normalized_request_id,
            )

            saved = reports.get_report(report_id)

            if saved is None:
                raise StorageError(
                    "The saved report could not be retrieved.",
                    request_id=normalized_request_id,
                )

            result = {
                "request_id": normalized_request_id,
                "report": saved,
            }

            operations.complete_request_response(
                normalized_request_id,
                result,
            )

            operations.record_audit_event(
                entity_type="report",
                entity_id=report_id,
                action="created",
                before=None,
                after={
                    "period_id": period_id,
                    "revision": revision,
                    "evidence_hash": evidence_hash,
                    "snapshot_hash": snapshot_hash,
                },
                reason="Completed-period report generated.",
                request_id=normalized_request_id,
            )

        self.logger.info(
            "Completed-period report generated.",
            extra={"request_id": normalized_request_id},
        )

        return result

    def get_report(self, report_id: str | UUID) -> dict:
        normalized_id = self._normalize_id(report_id)

        with self._session(write=False) as connection:
            report = ReportRepository(connection).get_report(
                normalized_id
            )

            if report is None:
                raise NotFoundError(
                    "The requested report does not exist."
                )

            return report

    def list_reports(
        self,
        *,
        period_id: str | UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        self._validate_pagination(limit, offset)

        normalized_period_id = (
            self._normalize_id(period_id)
            if period_id is not None
            else None
        )

        with self._session(write=False) as connection:
            return ReportRepository(connection).list_reports(
                period_id=normalized_period_id,
                limit=limit,
                offset=offset,
            )

    def list_completed_periods(
        self,
        *,
        kind: PeriodKind | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """List stored periods that have ended in this timezone."""
        self._validate_pagination(limit, offset)

        parameters: list[str | int] = [
            self.timezone,
            self._today().isoformat(),
        ]
        kind_filter = ""

        if kind is not None:
            kind_filter = "AND kind = ?"
            parameters.append(PeriodKind(kind).value)

        parameters.extend([limit, offset])

        with self._session(write=False) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    kind,
                    start_date,
                    end_date,
                    timezone
                FROM periods
                WHERE timezone = ?
                  AND end_date < ?
                  {kind_filter}
                ORDER BY end_date DESC, kind, id
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()

            return [dict(row) for row in rows]