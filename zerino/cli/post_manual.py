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
import sys
from pathlib import Path

from zerino.config import get_logger
from zerino.publishing.pipeline import dispatch_post_ids, process_and_queue

log = get_logger("zerino.cli.post_manual")

ALL_PLATFORMS = ["tiktok", "youtube_shorts", "instagram_reels", "twitter", "pinterest"]


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
    dispatch_post_ids(post_ids)


if __name__ == "__main__":
    main()
