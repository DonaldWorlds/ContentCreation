from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from zerino.config import DB_PATH

# A post that's been in 'processing' longer than this is treated as stranded
# (scheduler crashed mid-dispatch). Recovery marks it 'failed' rather than
# auto-retrying, because we can't tell if Zernio already received it.
STALE_CLAIM_TIMEOUT_SECONDS = 600


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
    Returns rows already marked as 'processing' with claimed_at=now.
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
        if not ids:
            return result
        conn.execute(
            f"UPDATE posts SET status='processing', claimed_at=?, updated_at=? "
            f"WHERE id IN ({','.join('?'*len(ids))})",
            [now, now, *ids],
        )
        # Reflect the just-applied claim in the returned dicts so callers see
        # claimed_at on the rows they're about to process.
        for r in result:
            r["claimed_at"] = now
            r["status"] = "processing"
        return result


def recover_stale_claims(timeout_seconds: int = STALE_CLAIM_TIMEOUT_SECONDS) -> list[int]:
    """Mark posts stuck in 'processing' beyond `timeout_seconds` as 'failed'.

    Called at scheduler startup and periodically after that. We can't safely
    auto-retry, because Zernio may already have received the post — re-sending
    would duplicate it. Marking 'failed' surfaces it to the operator (status
    CLI / Zernio dashboard) for a manual decision.

    Returns list of post ids that were recovered.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    ).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id FROM posts
               WHERE status='processing'
                 AND claimed_at IS NOT NULL
                 AND claimed_at < ?""",
            (cutoff,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        conn.execute(
            f"UPDATE posts "
            f"SET status='failed', "
            f"    last_error='dispatch interrupted; check Zernio dashboard before retrying', "
            f"    updated_at=? "
            f"WHERE id IN ({','.join('?'*len(ids))})",
            [_now(), *ids],
        )
        return ids


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
