
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from typing import Optional

from zerino.config import DB_PATH


@dataclass
class JobEvent:
    id: int
    job_id: str
    event: str
    message: Optional[str]
    created_at: str


class JobEventStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path if db_path is not None else str(DB_PATH)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    

    def log_job_event(self, job_id: str, event: str, message: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO job_events(job_id, event, message) VALUES(?, ?, ?)",
                (job_id, event, message),
            )
            conn.commit()

    def list_job_events(self, job_id: str, limit: int = 100) -> list[JobEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, event, message, created_at
                FROM job_events
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()

        return [
            JobEvent(
                id=row["id"],
                job_id=row["job_id"],
                event=row["event"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_recent_events(self, limit: int = 200, event: str | None = None) -> list[JobEvent]:
        with self._connect() as conn:
            if event:
                rows = conn.execute(
                    """
                    SELECT id, job_id, event, message, created_at
                    FROM job_events
                    WHERE event = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (event, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, job_id, event, message, created_at
                    FROM job_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        return [
            JobEvent(
                id=row["id"],
                job_id=row["job_id"],
                event=row["event"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]