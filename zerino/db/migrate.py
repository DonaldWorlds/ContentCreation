"""Initialize the unified zerino.db schema.

Runs both the capture-side (recordings, markers, clips, exports, streamers)
and publishing-side (scheduled_jobs, job_events) schemas against the single
DB at zerino.config.DB_PATH.

Also runs idempotent column-level migrations for tables whose schema has
changed since the original CREATE — this is how existing DBs pick up new
columns like posts.claimed_at without losing data.
"""
from __future__ import annotations

import sqlite3

from zerino.config import DB_PATH, get_logger
from zerino.db.init_db import create_database
from zerino.publishing.init_db import init_db as init_publishing_db


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, ddl: str, log
) -> None:
    if _column_exists(conn, table, column):
        return
    log.info("migrate: adding %s.%s", table, column)
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _apply_column_migrations(log) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        # posts.claimed_at — set when scheduler claims a row for dispatch.
        # Used to detect stale 'processing' rows after a crash.
        _add_column_if_missing(conn, "posts", "claimed_at", "TEXT", log)
        conn.commit()
    finally:
        conn.close()


def migrate() -> None:
    log = get_logger("zerino.db.migrate")
    log.info("Initializing unified DB at %s", DB_PATH)
    create_database()
    init_publishing_db()
    _apply_column_migrations(log)
    log.info("Migration complete.")


if __name__ == "__main__":
    migrate()
