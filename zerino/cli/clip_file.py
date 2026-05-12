"""Hand a video file to zerino directly — bypass capture, markers, and
hotkey. Useful for ad-hoc clips, testing, or clipping content recorded
elsewhere (a downloaded VOD, an edited file, etc.).

The file is run through the same one-pass render+post pipeline that the
capture flow uses:

    file path → ClipJob → queue_clip_jobs_for_posting
              → render per (platform, layout) of active accounts
              → post via Zernio

No `clips` table row is created — these are ephemeral; the resulting
`posts` rows have NULL clip_id and are the only persisted trace.

Usage:
    # Whole file, all active platforms, random caption
    python -m zerino.cli.clip_file --file path/to/video.mp4

    # A 30-second window inside the file
    python -m zerino.cli.clip_file --file path/to/video.mp4 --start 30 --end 60

    # Specific platforms + caption
    python -m zerino.cli.clip_file --file path/to/video.mp4 \\
        --platforms tiktok,youtube_shorts \\
        --caption "Wait for the end"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from zerino.config import get_logger
from zerino.ffmpeg.ffmpeg_utils import get_video_duration_seconds
from zerino.healthcheck import HealthcheckError, run_capture_healthcheck
from zerino.models import ClipJob
from zerino.publishing.clip_to_posts import queue_clip_jobs_for_posting

log = get_logger("zerino.cli.clip_file")


def _parse_platforms(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts or None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hand a video file to zerino for one-pass render and post.",
    )
    parser.add_argument(
        "--file", required=True,
        help="Path to the source video. Any mp4/mov ffmpeg can read.",
    )
    parser.add_argument(
        "--start", type=float, default=None,
        help="Window start in seconds. Default: 0 (start of file).",
    )
    parser.add_argument(
        "--end", type=float, default=None,
        help="Window end in seconds. Default: file duration.",
    )
    parser.add_argument(
        "--platforms", default=None,
        help="Comma-separated platform list (e.g. tiktok,youtube_shorts). "
             "Default: every platform with an active registered account.",
    )
    parser.add_argument(
        "--caption", default=None,
        help="Explicit post caption. Default: random pick from the captions pool.",
    )
    parser.add_argument(
        "--interval-minutes", type=int, default=120,
        help="Gap between scheduled posts when this expands to many. "
             "Only matters if you batch (single file = single post; ignored).",
    )
    args = parser.parse_args()

    try:
        run_capture_healthcheck()
    except HealthcheckError as e:
        log.error("startup healthcheck failed: %s", e)
        raise SystemExit(1)

    source = Path(args.file).expanduser().resolve()
    if not source.exists():
        log.error("file not found: %s", source)
        raise SystemExit(1)
    if not source.is_file():
        log.error("not a file: %s", source)
        raise SystemExit(1)

    duration = get_video_duration_seconds(source)
    if duration is None:
        log.error("could not probe duration: %s (ffprobe failed)", source)
        raise SystemExit(1)

    start = 0.0 if args.start is None else max(0.0, float(args.start))
    end = float(duration) if args.end is None else min(float(args.end), float(duration))
    if end <= start:
        log.error(
            "invalid window: start=%.2fs >= end=%.2fs (file duration=%.2fs)",
            start, end, duration,
        )
        raise SystemExit(1)

    platforms = _parse_platforms(args.platforms)

    job = ClipJob(
        clip_id=None,
        source_path=source,
        start=start,
        end=end,
    )

    log.info(
        "clip-file: src=%s [%.2fs-%.2fs] (duration=%.2fs) platforms=%s",
        source.name, start, end, duration, platforms or "(all active)",
    )

    queue_clip_jobs_for_posting(
        [job],
        caption=args.caption,
        interval_minutes=args.interval_minutes,
        platforms=platforms,
    )


if __name__ == "__main__":
    main()
