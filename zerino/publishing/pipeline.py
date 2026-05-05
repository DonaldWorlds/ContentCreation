"""
Publishing pipeline: render clip → fan out to post rows.

Used by both the manual CLI (immediate dispatch) and the batch scheduler
(deferred dispatch). Neither path is aware of the other; they share only
the posts table as the handoff point.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from zerino.config import get_logger
from zerino.db.repositories.accounts_repository import get_accounts_for_platform
from zerino.db.repositories.posts_repository import create_post
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
