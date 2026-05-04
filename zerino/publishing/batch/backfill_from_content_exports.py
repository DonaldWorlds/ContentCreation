# app/batch_schedule/backfill_from_content_exports.py
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zerino.publishing.batch.batch_schedule_planner import BatchSchedulePlanner
from zerino.publishing.job_events import JobEventStore
from zerino.publishing.scheduled_events import SqliteScheduledStore


SUPPORTED_EXTS = {".mp4", ".mov", ".m4v"}


@dataclass(frozen=True)
class BackfillConfig:
    content_root: Path = Path("content_exports")
    allowed_platform_folders: tuple[str, ...] = ("instagram", "tiktok", "youtube")

    # scheduling params
    start_at: datetime = datetime.now(timezone.utc)
    interval_minutes: int = 120
    timezone_name: str = "UTC"

    # db
    db_path: str = "jobs.sqlite3"

    # if True, don't write to DB; just print what would happen
    dry_run: bool = False

    # limit how many clips to schedule in one run (None = all)
    limit: int | None = None


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("scheduled datetime must be timezone-aware")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iter_media_files(platform_dir: Path) -> Iterable[Path]:
    # Adjust if you want recursive scanning
    for p in sorted(platform_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            yield p


def _make_clip_key(content_root: Path, media_path: Path) -> str:
    # Stable dedupe key across runs: relative path under content_exports
    # Example: "instagram/clip_001.mp4"
    return media_path.relative_to(content_root).as_posix()


def _has_json1(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
        return True
    except sqlite3.OperationalError:
        return False


def _clip_key_exists(db_path: str, clip_key: str) -> bool:
    """
    True if there is already a scheduled_jobs row whose payload_json contains this clip_key.

    Prefers SQLite JSON1 extension if available; falls back to LIKE.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if _has_json1(conn):
            row = conn.execute(
                """
                SELECT 1
                FROM scheduled_jobs
                WHERE json_extract(payload_json, '$.clip_key') = ?
                LIMIT 1
                """,
                (clip_key,),
            ).fetchone()
            return row is not None

        # Fallback: string match (works anywhere)
        like = f'%\"clip_key\": \"{clip_key}\"%'
        row = conn.execute(
            """
            SELECT 1
            FROM scheduled_jobs
            WHERE payload_json LIKE ?
            LIMIT 1
            """,
            (like,),
        ).fetchone()
        return row is not None


def _build_publish_payload(
    *,
    media_path: Path,
    platform: str,
    timezone_name: str,
    scheduled_for: datetime,
    clip_key: str,
) -> dict:
    """
    This payload must be enough to reconstruct your PublishJob later.

    NOTE: Adjust platform_targets shape to match your publisher/worker expectations.
    You commented your worker expects keys like {"platform": "...", "accountId": "..."}.
    """
    account_id_by_platform = {
        "instagram": "ig_account_id",
        "tiktok": "tt_account_id",
        "youtube": "yt_account_id",
    }

    return {
        "clip_key": clip_key,  # used for dedupe
        "content": "",  # caption/text (fill later if you have a source)
        "platform_targets": [
            {"platform": platform, "accountId": account_id_by_platform[platform]}
        ],
        "media_paths": [str(media_path)],
        "scheduled_for": scheduled_for.astimezone(timezone.utc).isoformat(),
        "timezone": timezone_name,
        "metadata": {"source": "backfill_from_content_exports"},
    }


def backfill(config: BackfillConfig) -> int:
    scheduled_store = SqliteScheduledStore(db_path=config.db_path)
    events = JobEventStore(db_path=config.db_path)

    # 1) Collect eligible media files from content_exports
    candidates: list[tuple[str, Path, str]] = []
    for platform in config.allowed_platform_folders:
        platform_dir = config.content_root / platform
        if not platform_dir.exists():
            continue

        for media_path in _iter_media_files(platform_dir):
            clip_key = _make_clip_key(config.content_root, media_path)

            # 2) Dedupe against scheduled_jobs
            if _clip_key_exists(config.db_path, clip_key):
                continue

            candidates.append((platform, media_path, clip_key))
            if config.limit is not None and len(candidates) >= config.limit:
                break

    if not candidates:
        print("Backfill: nothing new to schedule from content_exports.")
        return 0

    # 3) Plan times 1:1
    planner = BatchSchedulePlanner(start_at=config.start_at, interval_minutes=config.interval_minutes)
    times = planner.plan(len(candidates))

    # 4) Insert scheduled jobs
    created = 0
    for (platform, media_path, clip_key), scheduled_for in zip(candidates, times):
        job_id = uuid.uuid4().hex
        run_at_utc = _utc_iso(scheduled_for)

        payload = _build_publish_payload(
            media_path=media_path,
            platform=platform,
            timezone_name=config.timezone_name,
            scheduled_for=scheduled_for,
            clip_key=clip_key,
        )

        if config.dry_run:
            print(f"[DRY RUN] would insert job_id={job_id} run_at_utc={run_at_utc} clip_key={clip_key}")
            print(json.dumps(payload, indent=2))
            created += 1
            continue

        scheduled_store.insert_scheduled_job(
            job_id=job_id,
            run_at_utc=run_at_utc,
            timezone_name=config.timezone_name,
            payload=payload,
            max_attempts=5,
        )
        events.log_job_event(job_id, "backfilled", f"clip_key={clip_key} media={media_path}")
        created += 1

    print(f"Backfill complete. Inserted {created} scheduled_jobs rows.")
    return created


if __name__ == "__main__":
    cfg = BackfillConfig(
        content_root=Path("content_exports"),
        allowed_platform_folders=("instagram", "tiktok", "youtube"),
        start_at=datetime.now(timezone.utc),
        interval_minutes=120,
        timezone_name="UTC",
        db_path="jobs.sqlite3",
        dry_run=False,
        limit=None,
    )
    backfill(cfg)