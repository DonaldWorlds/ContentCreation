"""Disk cleanup for Zernio's pipeline output directories.

Without this command, `recordings/`, `clips/`, and `renders/` grow forever.
A few weeks of streaming = hundreds of GB.

Usage:
    # Show what WOULD be deleted, never delete:
    python -m zerino.cli.cleanup all --dry-run

    # Delete recordings older than 7 days (recordings are big, get rid fast):
    python -m zerino.cli.cleanup recordings --days 7

    # Delete clips older than 30 days:
    python -m zerino.cli.cleanup clips --days 30

    # Delete renders older than 30 days, EXCEPT files still referenced by
    # a pending/processing post (those would orphan the post):
    python -m zerino.cli.cleanup renders --days 30

    # All three at once with their defaults:
    python -m zerino.cli.cleanup all
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from zerino.config import CLIPS_DIR, DB_PATH, RECORDINGS_DIR, RENDERS_DIR

DEFAULT_RECORDING_DAYS = 7
DEFAULT_CLIP_DAYS = 30
DEFAULT_RENDER_DAYS = 30


def _files_older_than(root: Path, days: int) -> list[Path]:
    if not root.exists():
        return []
    cutoff = time.time() - days * 86400
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name in (".gitkeep", ".DS_Store"):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                out.append(p)
        except OSError:
            continue
    return out


def _protected_render_paths() -> set[str]:
    """Render files referenced by posts that haven't reached a terminal state.

    Terminal = published, failed, cancelled. We refuse to delete files
    pointed at by pending/processing posts because doing so would orphan
    the dispatch.
    """
    if not Path(DB_PATH).exists():
        return set()
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                "SELECT render_path FROM posts "
                "WHERE status IN ('pending', 'processing') "
                "  AND render_path IS NOT NULL"
            ).fetchall()
            return {r[0] for r in rows}
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def _bytes_human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _delete_files(files: list[Path], dry_run: bool, label: str) -> tuple[int, int]:
    """Delete files (unless dry_run). Return (count, bytes_freed)."""
    total_bytes = 0
    deleted = 0
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if dry_run:
            print(f"  [dry-run] would delete {p} ({_bytes_human(size)})")
            total_bytes += size
            deleted += 1
            continue
        try:
            p.unlink()
            deleted += 1
            total_bytes += size
        except OSError as e:
            print(f"  ERR could not delete {p}: {e}")
    verb = "Would free" if dry_run else "Freed"
    print(f"{label}: {deleted} file(s) — {verb} {_bytes_human(total_bytes)}")
    return deleted, total_bytes


def cmd_recordings(days: int, dry_run: bool) -> None:
    files = _files_older_than(RECORDINGS_DIR, days)
    print(f"\n[recordings] {len(files)} file(s) older than {days}d in {RECORDINGS_DIR}")
    _delete_files(files, dry_run, "recordings")


def cmd_clips(days: int, dry_run: bool) -> None:
    files = _files_older_than(CLIPS_DIR, days)
    print(f"\n[clips] {len(files)} file(s) older than {days}d in {CLIPS_DIR}")
    _delete_files(files, dry_run, "clips")


def cmd_renders(days: int, dry_run: bool) -> None:
    files = _files_older_than(RENDERS_DIR, days)
    protected = _protected_render_paths()
    safe: list[Path] = []
    skipped = 0
    for p in files:
        # render_path on the posts table can be either an absolute path or
        # a relative-to-repo path, depending on caller. Compare both.
        if str(p) in protected or p.name in {Path(rp).name for rp in protected}:
            skipped += 1
            continue
        safe.append(p)
    print(
        f"\n[renders] {len(files)} file(s) older than {days}d in {RENDERS_DIR} "
        f"({skipped} skipped — referenced by pending/processing posts)"
    )
    _delete_files(safe, dry_run, "renders")


def cmd_all(args: argparse.Namespace) -> None:
    cmd_recordings(args.recording_days, args.dry_run)
    cmd_clips(args.clip_days, args.dry_run)
    cmd_renders(args.render_days, args.dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete old recordings/clips/renders")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("recordings", help="Delete recordings older than --days")
    p_rec.add_argument("--days", type=int, default=DEFAULT_RECORDING_DAYS)
    p_rec.add_argument("--dry-run", action="store_true")

    p_clip = sub.add_parser("clips", help="Delete clips older than --days")
    p_clip.add_argument("--days", type=int, default=DEFAULT_CLIP_DAYS)
    p_clip.add_argument("--dry-run", action="store_true")

    p_rend = sub.add_parser("renders", help="Delete renders older than --days (skips active posts)")
    p_rend.add_argument("--days", type=int, default=DEFAULT_RENDER_DAYS)
    p_rend.add_argument("--dry-run", action="store_true")

    p_all = sub.add_parser("all", help="Run recordings + clips + renders cleanup")
    p_all.add_argument("--recording-days", type=int, default=DEFAULT_RECORDING_DAYS)
    p_all.add_argument("--clip-days", type=int, default=DEFAULT_CLIP_DAYS)
    p_all.add_argument("--render-days", type=int, default=DEFAULT_RENDER_DAYS)
    p_all.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.cmd == "recordings":
        cmd_recordings(args.days, args.dry_run)
    elif args.cmd == "clips":
        cmd_clips(args.days, args.dry_run)
    elif args.cmd == "renders":
        cmd_renders(args.days, args.dry_run)
    elif args.cmd == "all":
        cmd_all(args)


if __name__ == "__main__":
    main()
