from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from zerino.config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_post(
    platform: str,
    account_id: int,
    render_path: str,
    caption: str = "",
    clip_id: int | None = None,
    mode: str = "manual",
    scheduled_for: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO posts
               (clip_id, platform, account_id, render_path, caption, mode, scheduled_for)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (clip_id, platform.lower(), account_id, render_path, caption, mode, scheduled_for),
        )
        return cur.lastrowid


def get_post_by_id(post_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """SELECT p.*, a.zernio_account_id, a.profile_id
               FROM posts p
               JOIN accounts a ON a.id = p.account_id
               WHERE p.id = ?""",
            (post_id,),
        ).fetchone()
        return dict(row) if row else None


def claim_due_posts(limit: int = 10) -> list[dict[str, Any]]:
    """
    Atomically claim posts that are due for dispatch.
    Returns rows already marked as 'processing'.
    """
    now = _now()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT p.*, a.zernio_account_id, a.profile_id
               FROM posts p
               JOIN accounts a ON a.id = p.account_id
               WHERE p.status = 'pending'
                 AND p.attempts < p.max_attempts
                 AND (p.scheduled_for IS NULL OR p.scheduled_for <= ?)
                 AND (p.next_retry_at IS NULL OR p.next_retry_at <= ?)
               ORDER BY p.scheduled_for ASC, p.id ASC
               LIMIT ?""",
            (now, now, limit),
        ).fetchall()

        result = [dict(r) for r in rows]
        ids = [r["id"] for r in result]
        if ids:
            conn.execute(
                f"UPDATE posts SET status='processing', updated_at=? WHERE id IN ({','.join('?'*len(ids))})",
                [now, *ids],
            )
        return result


def mark_published(post_id: int, zernio_post_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE posts
               SET status='published', zernio_post_id=?, attempts=attempts+1, updated_at=?
               WHERE id=?""",
            (zernio_post_id, _now(), post_id),
        )


def record_failure(post_id: int, error: str, retry_at: str | None) -> None:
    """
    Increment attempts and record error.
    retry_at set → status='pending' (will be retried).
    retry_at None → status='failed' (permanently failed).
    """
    status = "pending" if retry_at else "failed"
    with _connect() as conn:
        conn.execute(
            """UPDATE posts
               SET attempts=attempts+1, last_error=?, next_retry_at=?, status=?, updated_at=?
               WHERE id=?""",
            (error, retry_at, status, _now(), post_id),
        )
