"""
Manual posting CLI — renders a clip and dispatches it immediately to Zernio.
No scheduler required; posts go straight to the API.

Usage:
    python -m zerino.cli.post_manual \\
        --clip path/to/clip.mp4 \\
        --platforms tiktok youtube_shorts instagram_reels twitter \\
        --caption "My caption here"

    # post to all platforms with registered accounts
    python -m zerino.cli.post_manual --clip path/to/clip.mp4 --caption "Hello"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from zerino.config import DB_PATH, get_logger
from zerino.db.repositories.posts_repository import mark_published, record_failure
from zerino.publishing.pipeline import process_and_queue
from zerino.publishing.zernio.poster import dispatch_post

log = get_logger("zerino.cli.post_manual")

ALL_PLATFORMS = ["tiktok", "youtube_shorts", "instagram_reels", "twitter", "pinterest"]


def _dispatch_post_ids(post_ids: list[int]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    for pid in post_ids:
        row = conn.execute(
            """SELECT p.*, a.zernio_account_id, a.profile_id
               FROM posts p
               JOIN accounts a ON a.id = p.account_id
               WHERE p.id = ?""",
            (pid,),
        ).fetchone()

        if row is None:
            log.warning("post id=%d not found in DB — skipping", pid)
            continue

        row = dict(row)
        conn.execute(
            "UPDATE posts SET status='processing' WHERE id=?", (pid,)
        )
        conn.commit()

        try:
            zernio_id = dispatch_post(row)
            mark_published(pid, zernio_id)
            log.info(
                "published post id=%d platform=%s zernio_post_id=%s",
                pid, row["platform"], zernio_id,
            )
        except Exception as e:
            log.exception("post id=%d failed: %s", pid, e)
            record_failure(pid, str(e), retry_at=None)

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a clip and post it immediately to Zernio."
    )
    parser.add_argument("--clip", required=True, help="Path to the source clip")
    parser.add_argument(
        "--platforms", nargs="+", default=ALL_PLATFORMS,
        help="Platforms to post to (default: all). "
             "Choices: tiktok youtube_shorts instagram_reels twitter pinterest",
    )
    parser.add_argument("--caption", default="", help="Caption / post text")
    parser.add_argument(
        "--clip-id", type=int, default=None,
        help="DB clips.id if this clip is already tracked in the DB",
    )
    args = parser.parse_args()

    clip_path = Path(args.clip)
    if not clip_path.exists():
        log.error("Clip not found: %s", clip_path)
        sys.exit(1)

    log.info(
        "manual post: clip=%s platforms=%s",
        clip_path.name, args.platforms,
    )

    post_ids = process_and_queue(
        input_path=clip_path,
        platforms=args.platforms,
        caption=args.caption,
        mode="manual",
        clip_id=args.clip_id,
    )

    if not post_ids:
        log.error(
            "No posts created. Make sure accounts are registered for the requested "
            "platforms: python -m zerino.cli.add_account add --platform <name> ..."
        )
        sys.exit(1)

    log.info("Dispatching %d post(s) now...", len(post_ids))
    _dispatch_post_ids(post_ids)


if __name__ == "__main__":
    main()
