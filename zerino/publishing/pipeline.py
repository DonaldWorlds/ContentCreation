"""
Publishing pipeline: render clip → fan out to post rows.

Used by both the manual CLI (immediate dispatch) and the batch scheduler
(deferred dispatch). Neither path is aware of the other; they share only
the posts table as the handoff point.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zerino.config import DB_PATH, get_logger
from zerino.db.repositories.accounts_repository import get_accounts_for_platform
from zerino.db.repositories.posts_repository import (
    create_post,
    mark_published,
    record_failure,
)
from zerino.publishing.zernio.poster import dispatch_post
from zerino.router import Router

log = get_logger("zerino.publishing.pipeline")


def process_and_queue(
    input_path: Path | str,
    platforms: list[str],
    caption: str = "",
    mode: str = "manual",
    scheduled_for: datetime | None = None,
    clip_id: int | None = None,
) -> list[int]:
    """
    1. Render the clip for every requested platform via Router.
    2. For each successful render, look up active accounts for that platform.
    3. Insert one post row per (platform, account).

    Returns list of post IDs created. Logs and skips platforms with no
    registered accounts or render failures (router already logs those).
    """
    input_path = Path(input_path)
    router = Router()
    renders = router.route(input_path, platforms)

    scheduled_str = scheduled_for.isoformat() if scheduled_for else None
    post_ids: list[int] = []

    for platform, result in renders.items():
        accounts = get_accounts_for_platform(platform)
        if not accounts:
            log.warning(
                "pipeline: no active accounts for platform=%s — skipping fan-out. "
                "Add one with: python -m zerino.cli.add_account add --platform %s ...",
                platform, platform,
            )
            continue

        for acct in accounts:
            pid = create_post(
                platform=platform,
                account_id=acct["id"],
                render_path=str(result.output_path),
                caption=caption,
                clip_id=clip_id,
                mode=mode,
                scheduled_for=scheduled_str,
            )
            post_ids.append(pid)
            log.info(
                "pipeline: queued post id=%d platform=%s handle=%s render=%s",
                pid, platform, acct["handle"], result.output_path.name,
            )

    return post_ids


def dispatch_post_ids(post_ids: list[int]) -> None:
    """Send each post row to Zernio right now.

    Each row's `scheduled_for` (which the row already has, set by the caller)
    is forwarded to Zernio. Zernio then decides whether to publish
    immediately (if scheduled_for is at-or-before now) or schedule it (if
    in the future). Either way the post appears in the Zernio dashboard
    as soon as this function returns — no waiting on the local scheduler.

    Concurrency: this function and the scheduler daemon (`scheduler_runner`)
    both publish from the `posts` table. To prevent double-posting we claim
    each row atomically with `UPDATE ... WHERE status='pending'`. If the
    scheduler already claimed the row in the gap between create_post() and
    here, our UPDATE matches zero rows and we skip — the scheduler is in
    flight with that one and will mark it published.

    On failure, the post is marked pending with a 60 s retry, so the
    scheduler daemon will pick it up as a fallback.
    """
    if not post_ids:
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        for pid in post_ids:
            # Atomic claim: only proceed if the row is still 'pending'.
            # If the scheduler beat us to it, rowcount=0 and we skip.
            now_iso = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "UPDATE posts SET status='processing', claimed_at=?, updated_at=? "
                "WHERE id=? AND status='pending'",
                (now_iso, now_iso, pid),
            )
            conn.commit()
            if cur.rowcount == 0:
                log.info(
                    "dispatch: post id=%d already claimed by scheduler or non-pending — skipping",
                    pid,
                )
                continue

            row = conn.execute(
                """SELECT p.*, a.zernio_account_id, a.profile_id
                   FROM posts p
                   JOIN accounts a ON a.id = p.account_id
                   WHERE p.id = ?""",
                (pid,),
            ).fetchone()

            if row is None:
                log.warning("dispatch: post id=%d not found in DB after claim — skipping", pid)
                continue

            row = dict(row)

            try:
                zernio_id = dispatch_post(row)
                mark_published(pid, zernio_id)
                log.info(
                    "dispatch: post id=%d platform=%s zernio_post_id=%s",
                    pid, row["platform"], zernio_id,
                )
            except Exception as e:  # noqa: BLE001
                log.exception("dispatch: post id=%d failed: %s", pid, e)
                # Schedule a 60 s retry — the scheduler daemon will pick it up
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
                record_failure(pid, str(e), retry_at=retry_at)
    finally:
        conn.close()
