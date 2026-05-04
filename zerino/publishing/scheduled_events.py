
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from zerino.config import DB_PATH



STATUS_PENDING = "pending"
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_SUBMITTED = "submitted"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"


@dataclass
class ScheduledJobRow:
    id: str
    run_at_utc: str
    timezone: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SqliteScheduledStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path if db_path is not None else str(DB_PATH)
       

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
   

    def insert_scheduled_job(
        self,
        *,
        job_id: str,
        run_at_utc: str,          # ISO string UTC, recommended "...Z"
        timezone_name: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> None:
        payload_json = json.dumps(payload)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scheduled_jobs(id, run_at_utc, timezone, payload_json, status, attempts, max_attempts)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (job_id, run_at_utc, timezone_name, payload_json, STATUS_PENDING, max_attempts),
            )
            self._event(conn, job_id, "inserted", f"run_at_utc={run_at_utc} tz={timezone_name}")
            conn.commit()


    def claim_due_jobs(self, *, now_utc: str | None = None, limit: int = 50) -> list[ScheduledJobRow]:
        """
        Atomically claims due jobs by marking them from pending/failed -> queued,
        incrementing attempts. Returns claimed jobs.

        Retry logic:
        - Jobs in FAILED state are eligible again if attempts < max_attempts.
        - After max_attempts, they remain FAILED.
        """
        now_utc = now_utc or _utc_now_iso()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")  # lock for safe claiming across processes

            rows = conn.execute(
                """
                SELECT *
                FROM scheduled_jobs
                WHERE
                  run_at_utc <= ?
                  AND status IN (?, ?)
                  AND attempts < max_attempts
                ORDER BY run_at_utc ASC
                LIMIT ?
                """,
                (now_utc, STATUS_PENDING, STATUS_FAILED, limit),
            ).fetchall()

            claimed: list[ScheduledJobRow] = []
            for r in rows:
                new_attempts = int(r["attempts"]) + 1
                conn.execute(
                    """
                    UPDATE scheduled_jobs
                    SET status = ?, attempts = ?, updated_at = datetime('now'), last_error = NULL
                    WHERE id = ?
                    """,
                    (STATUS_QUEUED, new_attempts, r["id"]),
                )
                self._event(conn, r["id"], "claimed", f"attempt={new_attempts}")

                claimed.append(
                    ScheduledJobRow(
                        id=r["id"],
                        run_at_utc=r["run_at_utc"],
                        timezone=r["timezone"],
                        payload=json.loads(r["payload_json"]),
                        status=STATUS_QUEUED,
                        attempts=new_attempts,
                        max_attempts=int(r["max_attempts"]),
                    )
                )

            conn.commit()
            return claimed
        
    def _event(self, conn: sqlite3.Connection, job_id: str, event: str, message: str | None = None) -> None:
        conn.execute(
            "INSERT INTO job_events(job_id, event, message) VALUES(?, ?, ?)",
            (job_id, event, message),
        )

    def mark_processing(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                (STATUS_PROCESSING, job_id),
            )
            self._event(conn, job_id, "processing")
            conn.commit()

    def mark_submitted(self, job_id: str, zernio_post_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET status=?, zernio_post_id=?, last_error=NULL, updated_at=datetime('now')
                WHERE id=?
                """,
                (STATUS_SUBMITTED, zernio_post_id, job_id),
            )
            self._event(conn, job_id, "submitted", f"zernio_post_id={zernio_post_id}")
            conn.commit()

    def mark_failed(self, job_id: str, error: str) -> None:
        """
        Marks job as FAILED. It will be retried by claim_due_jobs() if attempts < max_attempts.
        """
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET status=?, last_error=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (STATUS_FAILED, error[:2000], job_id),
            )
            self._event(conn, job_id, "failed", error[:500])
            conn.commit()

    def cancel(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_jobs SET status=?, updated_at=datetime('now') WHERE id=?",
                (STATUS_CANCELED, job_id),
            )
            self._event(conn, job_id, "canceled")
            conn.commit()