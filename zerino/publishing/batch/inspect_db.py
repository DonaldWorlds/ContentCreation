# app/database/db_inspector.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass
class TableInfo:
    name: str
    sql: str


@dataclass
class ColumnInfo:
    cid: int
    name: str
    type: str
    notnull: int
    dflt_value: Optional[str]
    pk: int


class SqliteDbInspector:
    def __init__(self, db_path: str = "jobs.sqlite3"):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- schema inspection ---

    def list_tables(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    def get_table_sql(self, table: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return row["sql"] if row else None

    def table_exists(self, table: str) -> bool:
        return self.get_table_sql(table) is not None

    def list_columns(self, table: str) -> list[ColumnInfo]:
        with self._connect() as conn:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [
            ColumnInfo(
                cid=r["cid"],
                name=r["name"],
                type=r["type"],
                notnull=r["notnull"],
                dflt_value=r["dflt_value"],
                pk=r["pk"],
            )
            for r in rows
        ]

    def assert_required_columns(self, table: str, required: Iterable[str]) -> None:
        cols = {c.name for c in self.list_columns(table)}
        missing = [c for c in required if c not in cols]
        if missing:
            raise RuntimeError(f"Table '{table}' missing columns: {missing}")

    # --- data inspection ---

    def count_rows(self, table: str, where: str = "", params: tuple = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {table} " + (f"WHERE {where}" if where else "")
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["n"])

    def preview_rows(
        self,
        table: str,
        limit: int = 10,
        order_by: str | None = "rowid DESC",
    ) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {table}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        sql += " LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_scheduled_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        # Helpful: decode payload_json if present
        if "payload_json" in d and d["payload_json"]:
            try:
                d["payload"] = json.loads(d["payload_json"])
            except Exception:
                d["payload"] = None
        return d

    def list_scheduled_jobs(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduled_jobs
                    WHERE status=?
                    ORDER BY run_at_utc ASC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM scheduled_jobs
                    ORDER BY run_at_utc ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if "payload_json" in d and d["payload_json"]:
                try:
                    d["payload"] = json.loads(d["payload_json"])
                except Exception:
                    d["payload"] = None
            out.append(d)
        return out

    def list_job_events_for_job(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
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
        return [dict(r) for r in rows]

    def list_recent_job_events(self, limit: int = 100, event: str | None = None) -> list[dict[str, Any]]:
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
        return [dict(r) for r in rows]


def print_db_summary(db_path: str = "jobs.sqlite3") -> None:
    insp = SqliteDbInspector(db_path=db_path)

    print(f"DB: {db_path}")
    print("Tables:", insp.list_tables())

    for table in ("scheduled_jobs", "job_events"):
        if not insp.table_exists(table):
            print(f"- {table}: MISSING")
            continue

        print(f"\n== {table} ==")
        print("columns:", [c.name for c in insp.list_columns(table)])
        print("count:", insp.count_rows(table))
        print("preview:", insp.preview_rows(table, limit=5))


if __name__ == "__main__":
    print_db_summary("jobs.sqlite3")