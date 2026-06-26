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
    """Clips post ~2h apart and captions now CYCLE through the active pool, so
    a caption is reused only after pool_size * 2h. To stay past Zernio's 24h
    duplicate-text [409] window the pool needs >= 13 active captions
    (13 * 2h = 26h > 24h) — regardless of how many clips there are. Warn only
    when it's below that."""
    min_for_24h = 13
    n = _active_caption_count()
    if n < min_for_24h:
        print()
        print(
            f"  NOTE: {n} active caption(s) in the pool. Clips post 2h apart and captions "
            f"cycle, so a pool < {min_for_24h} can reuse a caption inside Zernio's 24h window "
            f"and get [409]-rejected."
        )
        print(
            f"  Add a few more for safety:  python -m zerino.cli.captions add \"caption #tags\"  "
            f"(aim for >= {min_for_24h})"
        )
        print()


def _clear_redoable_clips(recording_id: int) -> tuple[int, int]:
    """Delete this recording's clips so they re-cut/re-render — EXCEPT clips
    that already produced a *published* post (re-doing those would duplicate
    on Zernio). Also deletes the non-published posts of the redone clips so the
    re-dispatch is clean. Returns (clips_deleted, clips_kept_published).

    Why not filter on clip.status: a clip flips to 'completed' once its batch
    is processed even if every render FAILED and zero posts were created (a
    broken face does exactly that). So 'completed' does NOT mean "posted" — the
    only safe "already live, don't redo" signal is a published POST row.
    """
    try:
        with _connect() as conn:
            published = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT cl.id FROM clips cl "
                    "JOIN posts p ON p.clip_id = cl.id "
                    "WHERE cl.recording_id = ? AND p.status = 'published'",
                    (recording_id,),
                )
            }
            all_ids = {
                r[0] for r in conn.execute(
                    "SELECT id FROM clips WHERE recording_id = ?", (recording_id,)
                )
            }
            redo = sorted(all_ids - published)
            if redo:
                ph = ",".join("?" * len(redo))
                # drop only the NON-published posts of the redone clips first
                # (keeps FK clean), then the clip rows themselves.
                conn.execute(
                    f"DELETE FROM posts WHERE clip_id IN ({ph}) AND status != 'published'",
                    redo,
                )
                conn.execute(f"DELETE FROM clips WHERE id IN ({ph})", redo)
            return len(redo), len(published)
    except sqlite3.Error as e:
        log.warning("could not clear redoable clips for recording id=%s: %s", recording_id, e)
        return 0, 0


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
        cleared, kept = _clear_redoable_clips(recording_id)
        log.info(
            "reprocess --force: cleared %d clip(s) to re-cut/re-render; kept %d already-"
            "published clip(s) that won't be re-posted. recording id=%s",
            cleared, kept, recording_id,
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
    parser.add_argument("--detect", action="store_true",
                        help="Run highlight DETECTION on the recording (emit detected windows + "
                             "detections rows) instead of re-cutting saved F8/F9 markers. "
                             "Idempotent. Auto-post is gated by the ZERINO_DETECTION_AUTOPOST "
                             "master flag (default OFF); when ON, detected windows ride the same "
                             "create_clips/post path F8/F9 uses.")
    parser.add_argument("--game", default="fortnite",
                        help="GameProfile id for --detect (default: fortnite).")
    args = parser.parse_args()

    if args.list:
        _print_list()
        return

    if args.detect:
        if args.recording_id is None:
            log.error("--detect requires --recording-id N")
            raise SystemExit(2)
        try:
            run_capture_healthcheck()
        except HealthcheckError as e:
            log.error("startup healthcheck failed: %s", e)
            raise SystemExit(1)
        from zerino.cli.detect import autopost_enabled, detect_recording
        conn = _connect()
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            windows = detect_recording(args.recording_id, game=args.game, conn=conn)
        finally:
            conn.close()
        log.info("reprocess --detect: recording id=%s -> %d detected window(s) (autopost=%s)",
                 args.recording_id, len(windows), autopost_enabled())
        raise SystemExit(0)

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
