"""Record of daily mails sent through Outlook."""

import sqlite3
from datetime import date
from pathlib import Path
from uuid import uuid4

from goal_tracker.db.connection import open_database, transaction


class MailLog:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def reserve(
        self,
        *,
        request_id: str,
        mail_date: date,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        method: str,
    ) -> tuple[dict, bool]:
        """Reserve a send for this request ID.

        Returns (row, is_new). When is_new is False the request was already
        made (double click, refresh or retry) and must not be sent again.
        """
        with open_database(self.database_path) as connection:
            with transaction(connection, write=True):
                try:
                    connection.execute(
                        """
                        INSERT INTO sent_mails (
                            id, request_id, mail_date, recipients_to,
                            recipients_cc, subject, body, method, status
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sending')
                        """,
                        (
                            str(uuid4()),
                            request_id,
                            mail_date.isoformat(),
                            "; ".join(to),
                            "; ".join(cc),
                            subject,
                            body,
                            method,
                        ),
                    )
                    is_new = True
                except sqlite3.IntegrityError:
                    is_new = False

                row = connection.execute(
                    "SELECT * FROM sent_mails WHERE request_id = ?",
                    (request_id,),
                ).fetchone()

        return dict(row), is_new

    def complete(self, request_id: str, *, error: str | None = None) -> dict:
        with open_database(self.database_path) as connection:
            with transaction(connection, write=True):
                connection.execute(
                    """
                    UPDATE sent_mails
                    SET status = ?,
                        error = ?,
                        completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE request_id = ?
                    """,
                    ("failed" if error else "sent", error, request_id),
                )
                row = connection.execute(
                    "SELECT * FROM sent_mails WHERE request_id = ?",
                    (request_id,),
                ).fetchone()

        return dict(row)

    def sent_on(self, mail_date: date) -> list[dict]:
        """Successful sends for a mail date, newest first."""
        with open_database(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM sent_mails
                WHERE mail_date = ? AND status = 'sent'
                ORDER BY created_at DESC
                """,
                (mail_date.isoformat(),),
            ).fetchall()

        return [dict(row) for row in rows]

    def recent(self, *, limit: int = 30, offset: int = 0) -> list[dict]:
        with open_database(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM sent_mails
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

        return [dict(row) for row in rows]
