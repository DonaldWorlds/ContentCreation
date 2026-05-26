"""Recover a recording whose clips never got cut + posted.

The capture daemon only turns a recording into clips when the recording
FINISHES (OBS stops recording AND the file stops growing) *and* the daemon is
still alive to see it. If the daemon is stopped before the OBS recording is
stopped, the recording row stays `pending`, its F8/F9 markers sit saved in the
DB, and no clips/posts are ever made — the session looks "lost" even though
the footage + markers are intact.

This command reprocesses such a recording on demand. It runs the SAME
`ClipService.process_recording()` the daemon runs at stream-end, so the saved
markers are cut into clips, rendered per platform, and sent to Zernio (clip 1
immediately, the rest spaced apart — identical to the live flow).

It is SAFE to re-run: clips that already exist for the recording are skipped
(`clip_exists`), and each post is claimed atomically before dispatch, so a
second run cannot double-post.

Usage:
    # See which recordings exist, their marker counts, and whether the
    # source file is still on disk (do this first):
    python -m zerino.cli.reprocess --list

    # Recover ONE recording (cut + render + post its markers):
    python -m zerino.cli.reprocess --recording-id 22

    # Recover every recording still 'pending' that has markers:
    python -m zerino.cli.reprocess --all-pending
"""
from __future__ import annotations

import argparse
import sqlite3

from zerino.capture.services.clip_service import ClipService
from zerino.config import DB_PATH, RECORDINGS_DIR, get_logger
from zerino.healthcheck import HealthcheckError, run_capture_healthcheck

log = get_logger("zerino.cli.reprocess")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _recordings_overview() -> list[dict]:
    """Per-recording: marker count, clip count, status, file-on-disk."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.filename, r.status, r.created_at,
                   (SELECT COUNT(*) FROM markers m WHERE m.recording_id = r.id) AS markers,
                   (SELECT COUNT(*) FROM clips c WHERE c.recording_id = r.id)   AS clips
            FROM recordings r
            ORDER BY r.id DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["on_disk"] = (RECORDINGS_DIR / d["filename"]).exists()
        out.append(d)
    return out


def _active_caption_count() -> int:
    with _connect() as conn:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM captions WHERE active = 1"
            ).fetchone()[0]
        except sqlite3.Error:
            return 0


def _print_list() -> None:
    rows = _recordings_overview()
    if not rows:
        print("No recordings in the database.")
        return
    print()
    print(f"{'id':>4}  {'status':<10}  {'mark':>4}  {'clips':>5}  {'file':<6}  filename")
    print("-" * 78)
    for d in rows:
        flag = "OK" if d["on_disk"] else "MISSING"
        unprocessed = d["markers"] > 0 and d["clips"] == 0
        marker = "  <-- not posted yet" if unprocessed else ""
        print(
            f"{d['id']:>4}  {d['status']:<10}  {d['markers']:>4}  {d['clips']:>5}  "
            f"{flag:<6}  {d['filename']}{marker}"
        )
    print()
    print("Recover one with:  python -m zerino.cli.reprocess --recording-id <id>")
    print()


def _warn_if_caption_pool_too_small(marker_count: int) -> None:
    """Zernio rejects identical post text to the same account within 24h
    ([409]). Each clip draws a random caption from the pool, so a pool smaller
    than the number of clips guarantees some clips share text -> 409. Warn so
    the operator can add captions before posting."""
    n = _active_caption_count()
    if n < marker_count:
        print()
        print(
            f"  WARNING: only {n} active caption(s) in the pool but this recording has "
            f"{marker_count} clip(s)."
        )
        print(
            "  Zernio blocks duplicate post text to the same account within 24h, so "
            "clips that reuse a caption will be rejected [409]."
        )
        print(
            f"  Add more first:  python -m zerino.cli.captions add \"your caption #hashtags\"  "
            f"(aim for >= {marker_count})"
        )
        print()


def _clear_redoable_clips(recording_id: int) -> int:
    """Delete this recording's non-'completed' clip rows so they get re-cut and
    re-rendered. Completed clips (already posted) are KEPT, so --force never
    re-posts something that already went out — it only redoes failed/stuck
    ones. Returns the number of rows deleted."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM clips WHERE recording_id=? AND status != 'completed'",
                (recording_id,),
            )
            return cur.rowcount or 0
    except sqlite3.Error as e:
        log.warning("could not clear redoable clips for recording id=%s: %s", recording_id, e)
        return 0


def _reprocess_one(recording_id: int, svc: ClipService, *, force: bool = False) -> bool:
    overview = {d["id"]: d for d in _recordings_overview()}
    rec = overview.get(recording_id)
    if rec is None:
        log.error("recording id=%s not found. Run --list to see valid ids.", recording_id)
        return False
    if rec["markers"] == 0:
        log.warning("recording id=%s has no markers — nothing to recover.", recording_id)
        return False
    if not rec["on_disk"]:
        log.error(
            "source file MISSING on disk: %s\n"
            "  -> Make sure the OBS recording was STOPPED so the file finalized, "
            "and that it's in your recordings/ folder.",
            RECORDINGS_DIR / rec["filename"],
        )
        return False

    _warn_if_caption_pool_too_small(rec["markers"])

    log.info(
        "reprocess: recording id=%s file=%s markers=%d existing_clips=%d force=%s",
        recording_id, rec["filename"], rec["markers"], rec["clips"], force,
    )
    if force:
        cleared = _clear_redoable_clips(recording_id)
        log.info(
            "reprocess --force: cleared %d non-completed clip row(s) for recording id=%s "
            "so they re-cut and re-render with the current code (already-posted clips kept).",
            cleared, recording_id,
        )
    # Same code path the daemon uses at stream-end. Idempotent without --force:
    # existing clips are skipped, so already-posted markers are not re-sent.
    svc.process_recording(recording_id)

    # Mark the recording done so a restarted daemon won't re-sweep it.
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE recordings SET status='completed' WHERE id=?",
                (recording_id,),
            )
    except sqlite3.Error:
        log.warning("could not update recording id=%s status to completed", recording_id)

    log.info("reprocess: recording id=%s done — check the console schedule above and your Zernio dashboard.", recording_id)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover a recording whose clips never got cut/posted "
                    "(daemon stopped before the OBS recording finished).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="Show recordings, marker/clip counts, and whether the file is on disk.")
    group.add_argument("--recording-id", type=int, default=None,
                       help="Recover this recording id (see --list).")
    group.add_argument("--all-pending", action="store_true",
                       help="Recover every recording with markers but no clips yet.")
    parser.add_argument("--force", action="store_true",
                        help="Re-cut and re-render even if clips already exist (deletes the "
                             "recording's non-completed clip rows first; already-posted clips "
                             "are kept). Use after fixing a bad render, e.g. a corrupt face file.")
    args = parser.parse_args()

    if args.list:
        _print_list()
        return

    # Anything that renders/posts needs ffmpeg etc. — same gate as the daemon.
    try:
        run_capture_healthcheck()
    except HealthcheckError as e:
        log.error("startup healthcheck failed: %s", e)
        raise SystemExit(1)

    svc = ClipService()

    if args.recording_id is not None:
        ok = _reprocess_one(args.recording_id, svc, force=args.force)
        raise SystemExit(0 if ok else 1)

    # --all-pending: recordings that have markers but no clips yet.
    targets = [d["id"] for d in _recordings_overview() if d["markers"] > 0 and d["clips"] == 0]
    if not targets:
        log.info("nothing to recover — every recording with markers already has clips.")
        return
    log.info("recovering %d recording(s): %s", len(targets), targets)
    any_ok = False
    for rid in targets:
        any_ok = _reprocess_one(rid, svc, force=args.force) or any_ok
    raise SystemExit(0 if any_ok else 1)


if __name__ == "__main__":
    main()
