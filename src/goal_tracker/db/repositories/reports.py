import json
import sqlite3


class ReportRepository:
    """Store report snapshots and retrieve report history."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _require_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError(
                "Report writes require an explicit transaction."
            )

    @staticmethod
    def _encode(value: dict) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        report = dict(row)

        report["facts"] = json.loads(
            report.pop("factual_snapshot_json")
        )

        advisory_json = report.pop("advisory_json")
        report["advisory"] = (
            json.loads(advisory_json)
            if advisory_json is not None
            else None
        )

        provenance_json = report.pop("model_provenance_json")
        report["model_provenance"] = (
            json.loads(provenance_json)
            if provenance_json is not None
            else None
        )

        return report

    def next_revision(self, period_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(revision), 0) + 1
            FROM reports
            WHERE period_id = ?
            """,
            (period_id,),
        ).fetchone()

        return int(row[0])

    def save_report(
        self,
        *,
        report_id: str,
        period_id: str,
        revision: int,
        cutoff_date: str,
        facts: dict,
        advisory: dict | None,
        model_provenance: dict | None,
        evidence_hash: str,
        snapshot_hash: str,
        request_id: str,
    ) -> None:
        self._require_transaction()

        self.connection.execute(
            """
            INSERT INTO reports (
                id,
                period_id,
                revision,
                cutoff_date,
                factual_snapshot_json,
                advisory_json,
                model_provenance_json,
                evidence_hash,
                snapshot_hash,
                request_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                period_id,
                revision,
                cutoff_date,
                self._encode(facts),
                (
                    self._encode(advisory)
                    if advisory is not None
                    else None
                ),
                (
                    self._encode(model_provenance)
                    if model_provenance is not None
                    else None
                ),
                evidence_hash,
                snapshot_hash,
                request_id,
            ),
        )

    def get_report(self, report_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()

        if row is None:
            return None

        return self._decode(row)

    def list_reports(
        self,
        *,
        period_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError(
                "Limit must be between 1 and 100; "
                "offset cannot be negative."
            )

        conditions = []
        parameters: list[str | int] = []

        if period_id is not None:
            conditions.append("r.period_id = ?")
            parameters.append(period_id)

        where = (
            "WHERE " + " AND ".join(conditions)
            if conditions
            else ""
        )

        parameters.extend([limit, offset])

        rows = self.connection.execute(
            f"""
            SELECT
                r.id,
                r.period_id,
                r.revision,
                r.cutoff_date,
                r.generated_at,
                r.export_status,
                r.export_path,
                p.kind,
                p.start_date,
                p.end_date,
                p.timezone
            FROM reports AS r
            JOIN periods AS p ON p.id = r.period_id
            {where}
            ORDER BY
                p.end_date DESC,
                r.revision DESC,
                r.id
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()

        return [dict(row) for row in rows]

    def update_export(
        self,
        report_id: str,
        *,
        status: str,
        path: str | None,
    ) -> None:
        self._require_transaction()

        if status not in {"not_exported", "exported", "failed"}:
            raise ValueError("Invalid report export status.")

        if status == "exported" and not path:
            raise ValueError(
                "An exported report must have an export path."
            )

        cursor = self.connection.execute(
            """
            UPDATE reports
            SET export_status = ?, export_path = ?
            WHERE id = ?
            """,
            (status, path, report_id),
        )

        if cursor.rowcount != 1:
            raise ValueError("The requested report does not exist.")