"""Bridge between the capture pipeline and the publishing pipeline.

When `clip_service.create_clips()` finishes cutting a batch of clips for a
finished recording, it calls `queue_clips_for_posting()` here.

Posting cadence (Flow B + B/A hybrid the user requested):
    - clip[0]  → scheduled_for=None, mode='manual'    → posts immediately
    - clip[N>0] → scheduled_for=now + N*INTERVAL_MIN, mode='scheduled'

The first clip goes live as soon as the scheduler claims it (on the next
poll, ≤ 5 s). Subsequent clips are paced 2 hours apart by default so the
feed isn't flooded.

Platforms are derived from the registered active accounts table — no
hard-coded list. Register an account with `python -m zerino.cli.add_account`
to make a platform eligible for auto-posting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zerino.config import get_logger
from zerino.db.repositories.accounts_repository import list_all_accounts
from zerino.db.repositories.captions_repository import pick_random_caption
from zerino.publishing.pipeline import process_and_queue

DEFAULT_INTERVAL_MINUTES = 120  # 2 hours between scheduled clips

log = get_logger("zerino.publishing.clip_to_posts")


def _platforms_with_accounts() -> list[str]:
    """All platforms that have at least one active registered account."""
    return sorted({a["platform"] for a in list_all_accounts() if a.get("active")})


def queue_clips_for_posting(
    clip_specs: list[tuple[int, Path]],
    *,
    caption: str | None = None,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    platforms: list[str] | None = None,
) -> list[int]:
    """Render each clip and queue post rows.

    Args:
        clip_specs: list of (clip_id, output_path) in the order they should
            be posted. The first one goes immediately, the rest are spaced
            `interval_minutes` apart.
        caption: explicit post text. If None or empty, each clip pulls a
            different random caption from the captions pool.
        interval_minutes: gap between scheduled clips. Default 120 (2 hours).
        platforms: explicit platform list, or None to use every platform
            with a registered active account.

    Returns:
        Flat list of post IDs created across all clips.
    """
    if not clip_specs:
        log.info("clip_to_posts: nothing to queue")
        return []

    platforms = platforms or _platforms_with_accounts()
    if not platforms:
        log.warning(
            "clip_to_posts: no active accounts registered — no posts will be queued. "
            "Register one with: python -m zerino.cli.add_account add ..."
        )
        return []

    log.info(
        "clip_to_posts: queuing %d clip(s) -> platforms=%s, interval=%dmin",
        len(clip_specs), platforms, interval_minutes,
    )

    now = datetime.now(timezone.utc)
    all_post_ids: list[int] = []

    print()
    print(f"=== Posting schedule for {len(clip_specs)} clip(s) ===")

    for index, (clip_id, clip_path) in enumerate(clip_specs):
        if index == 0:
            scheduled_for = None
            mode = "manual"
            when_short = "POSTING NOW"
            when_full = "immediate"
        else:
            scheduled_for = now + timedelta(minutes=index * interval_minutes)
            hours = int(index * interval_minutes // 60)
            mins = int(index * interval_minutes % 60)
            offset = (
                f"in {hours}h {mins:02d}m" if hours else f"in {mins} min"
            )
            local_time = scheduled_for.astimezone()
            when_short = f"scheduled for {local_time:%a %b %d, %I:%M %p %Z} ({offset})"
            when_full = scheduled_for.isoformat()
            mode = "scheduled"

        # Each clip pulls its OWN random caption from the pool — different
        # clips in the same batch get different captions. If `caption` was
        # passed explicitly, use that for every clip instead.
        chosen_caption = caption if caption else pick_random_caption()
        if not chosen_caption:
            log.warning(
                "clip_to_posts: caption pool empty — clip_id=%d will post with no body. "
                "Add captions: python -m zerino.cli.captions add ...",
                clip_id,
            )

        # Console line — prominent, easy to read at a glance during a stream
        print(f"  Clip {index + 1}: {when_short}")
        print(f"          file: {clip_path.name}")
        print(f"          text: {chosen_caption.splitlines()[0] if chosen_caption else '(no caption — pool is empty)'}")

        # Structured log line — same info but for logs/zerino.log
        log.info(
            "clip_to_posts: clip_id=%d (idx=%d) -> %s (mode=%s)",
            clip_id, index, when_full, mode,
        )

        post_ids = process_and_queue(
            input_path=clip_path,
            platforms=platforms,
            caption=chosen_caption,
            mode=mode,
            scheduled_for=scheduled_for,
            clip_id=clip_id,
        )
        all_post_ids.extend(post_ids)

    print(f"=== Queued {len(all_post_ids)} post row(s); scheduler will dispatch when due ===")
    print()
    log.info(
        "clip_to_posts: queued %d post(s) across %d clip(s)",
        len(all_post_ids), len(clip_specs),
    )
    return all_post_ids
