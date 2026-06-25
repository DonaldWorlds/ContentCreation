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


def detect_recording(recording_id: int, *, game: str = "fortnite", render: bool = False,
                     conn: sqlite3.Connection, clip_service=None, recordings_dir=RECORDINGS_DIR) -> list[dict]:
    """Shared orchestration (used by this CLI AND reprocess --detect): resolve the
    recording's source file, run detect -> core -> emit, idempotency-skip handled inside
    detect_and_emit. Returns the emitted window dicts."""
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

    windows = detect_and_emit(
        adapter, profile, recording_id, conn,
        media=media, duration=media.timebase.duration if media.timebase else 0.0,
        streamer_id=streamer_id, source_hash=source_hash,
        render=render, clip_service=clip_service,
    )
    log.info("detect: recording id=%s game=%s -> %d window(s) (render=%s)",
             recording_id, game, len(windows), render)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run highlight detection on a recording or file.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--recording-id", type=int, help="Detect on this DB recording (emits markers+detections).")
    group.add_argument("--file", type=str, help="Dry-run on an arbitrary VOD (prints events, no DB writes).")
    parser.add_argument("--game", default="fortnite", help="GameProfile id (default: fortnite).")
    parser.add_argument("--render", action="store_true",
                        help="Feed detected windows to the existing render+post path (default OFF — trust gate).")
    args = parser.parse_args()

    if args.file:
        raise SystemExit(_dry_run_file(args.file, args.game))

    from zerino.capture.services.clip_service import ClipService
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        windows = detect_recording(args.recording_id, game=args.game, render=args.render,
                                   conn=conn, clip_service=ClipService() if args.render else None)
    finally:
        conn.close()
    print(f"emitted {len(windows)} detected window(s) for recording {args.recording_id} "
          f"(render={'on' if args.render else 'off'}).")


if __name__ == "__main__":
    main()
