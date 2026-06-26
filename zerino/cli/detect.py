"""Windows batch entry for highlight detection (DETECTION_DECISIONS.md §4).

    recording/source -> adapter -> core -> emit -> (gated) existing render+post

Two modes:
  --recording-id N   detect on a DB recording: emit gameplay markers + detections rows,
                     and (only with --render) feed the EXISTING ClipService.create_clips
                     render+post path. Render is OFF by default (trust gate).
  --file PATH        dry-run on an arbitrary VOD: print detected events, no DB writes.
                     Ideal for the golden-VOD harness / ad-hoc testing.

reprocess --detect reuses detect_recording() here (no logic duplicated). All torch/OCR/
ffmpeg work is lazy-imported inside the detection package; this CLI is Windows-batch only.
"""
from __future__ import annotations

import argparse
import sqlite3

from zerino.config import DB_PATH, RECORDINGS_DIR, get_logger
from zerino.detection import cache
from zerino.detection.adapters.fortnite import FortniteAdapter
from zerino.detection.media import MediaHandle
from zerino.detection.profile import load_profile
from zerino.detection.service import detect_and_emit

log = get_logger("zerino.cli.detect")

# game_id -> adapter class. New games (2K, Phase 3) register here; nothing else changes.
ADAPTERS = {"fortnite": FortniteAdapter}


def _adapter(game: str):
    try:
        return ADAPTERS[game]()
    except KeyError:
        raise SystemExit(f"unknown game '{game}'. known: {', '.join(sorted(ADAPTERS))}")


# Master kill-switch for detection AUTO-POSTING. Default OFF: detection emits markers +
# detections but posts NOTHING. Set ZERINO_DETECTION_AUTOPOST=1 to route detected windows
# through the SAME create_clips -> queue_clip_jobs_for_posting -> Zernio path F8/F9 uses
# (auto-post like a manual marker). Unset / "0" disables all detection posting instantly.
AUTOPOST_ENV = "ZERINO_DETECTION_AUTOPOST"


def autopost_enabled() -> bool:
    import os
    return os.getenv(AUTOPOST_ENV, "0").strip() == "1"


def detect_recording(recording_id: int, *, game: str = "fortnite",
                     conn: sqlite3.Connection, clip_service=None,
                     recordings_dir=RECORDINGS_DIR) -> list[dict]:
    """Shared orchestration (this CLI AND reprocess --detect): resolve the recording's
    source file, run detect -> core -> emit. AUTO-POST is gated by the AUTOPOST_ENV master
    flag (default OFF): flag OFF -> emit only, no post; flag ON -> the detected windows ride
    the SAME create_clips -> queue_clip_jobs_for_posting path F8/F9 uses. Returns the windows."""
    from pathlib import Path

    row = conn.execute(
        "SELECT filename, streamer_id FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"recording id={recording_id} not found")
    filename = row[0]
    streamer_id = row[1] if len(row) > 1 else None
    source = Path(recordings_dir) / filename
    if not source.exists():
        raise SystemExit(f"source file missing on disk: {source}")

    profile = load_profile(game)
    adapter = _adapter(game)
    media = MediaHandle.open(source)
    source_hash = cache.source_hash(source)

    autopost = autopost_enabled()
    if autopost and clip_service is None:
        from zerino.capture.services.clip_service import ClipService
        clip_service = ClipService()

    windows = detect_and_emit(
        adapter, profile, recording_id, conn,
        media=media, duration=media.timebase.duration if media.timebase else 0.0,
        streamer_id=streamer_id, source_hash=source_hash,
        render=autopost, clip_service=clip_service if autopost else None,
    )
    log.info("detect: recording id=%s game=%s -> %d window(s) (autopost=%s)",
             recording_id, game, len(windows), autopost)
    return windows


def _dry_run_file(path: str, game: str) -> int:
    """Detect on an arbitrary VOD and print events (no DB writes)."""
    profile = load_profile(game)
    adapter = _adapter(game)
    media = MediaHandle.open(path)
    events = adapter.detect(media, profile)
    print(f"\n{path}\n  game={game}  events={len(events)}")
    for e in sorted(events, key=lambda e: e.t):
        print(f"  t={e.t:7.1f}s  {e.type:11s} conf={e.confidence:.2f} "
              f"w={e.weight:.1f} src={e.source} meta={e.meta}")
    return 0


def render_review(path, game: str, review_dir) -> list:
    """Step C render-for-review (NO POST, render-only). detect -> core -> render each
    detected window through the EXISTING SplitProcessor (dual-source split + face pair,
    Decision 5) into review_dir. NEVER touches create_clips / queue_clip_jobs_for_posting /
    Zernio — SplitProcessor.process_clip_job only renders + returns the output path."""
    from pathlib import Path

    from zerino.detection.core.pipeline import run as core_run
    from zerino.models import ClipJob
    from zerino.processors.split import SplitProcessor

    profile = load_profile(game)
    adapter = _adapter(game)
    media = MediaHandle.open(path)
    duration = media.timebase.duration if media.timebase else 0.0
    candidates = core_run(adapter.detect(media, profile), profile.core_params(), duration)

    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    face = media.face_source_path
    proc = SplitProcessor()
    outputs = []
    print(f"\n{path}\n  game={game}  detected_windows={len(candidates)}  "
          f"face={'paired' if face else 'NONE (single-source split fallback)'}")
    for i, c in enumerate(sorted(candidates, key=lambda x: x.win_start), 1):
        job = ClipJob(
            clip_id=None, source_path=Path(media.source_path),
            start=float(c.win_start), end=float(c.win_end),
            layout="split", face_source_path=Path(face) if face else None,
        )
        res = proc.process_clip_job(job, platform="tiktok", output_dir=review_dir)
        outputs.append(res.output_path)
        print(f"  clip {i}: win=[{c.win_start:.1f}-{c.win_end:.1f}]s anchor={c.anchor_t:.1f} "
              f"score={c.score:.2f} -> {res.output_path}")
    print(f"\nrendered {len(outputs)} review clip(s) to {review_dir} — NO post, no DB rows, render-only.")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run highlight detection on a recording or file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--recording-id", type=int, help="Detect on this DB recording (emits markers+detections).")
    group.add_argument("--file", type=str, help="Dry-run on an arbitrary VOD (prints events, no DB writes).")
    parser.add_argument("--game", default="fortnite", help="GameProfile id (default: fortnite).")
    parser.add_argument("--render-review", default=None, metavar="DIR",
                        help="Step C: render detected windows through the EXISTING split renderer "
                             "(dual-source + face pair, Decision 5) into DIR for manual review. "
                             "NO post, never via create_clips. Requires --file.")
    args = parser.parse_args()

    if args.render_review:
        if not args.file:
            raise SystemExit("--render-review requires --file")
        render_review(args.file, args.game, args.render_review)
        raise SystemExit(0)

    if args.file:
        raise SystemExit(_dry_run_file(args.file, args.game))

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        windows = detect_recording(args.recording_id, game=args.game, conn=conn)
    finally:
        conn.close()
    print(f"emitted {len(windows)} detected window(s) for recording {args.recording_id} "
          f"(autopost={'ON' if autopost_enabled() else 'OFF'}).")


if __name__ == "__main__":
    main()
