# app/batch_schedule/main.py
"""
Batch-schedule CLI entrypoint.

Responsibilities:
- Wire dependencies (handler, planner, service, repos, queue)
- Run ONE batch cycle:
    items = handler.discover()
    times = planner.plan(len(items))
    jobs = service.run_batch(items, times, targets_for_item=..., build_content_for_item=...)
- Print a summary

Non-responsibilities:
- No scanning logic (handler owns that)
- No scheduling rules (planner owns that)
- No Zernio calls (worker/publisher owns that)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from zerino.publishing.batch.batch_schedule_handler import BatchScheduleHandler
from zerino.publishing.batch.batch_schedule_planner import BatchSchedulePlanner
from zerino.publishing.batch.batch_schedule_service import BatchScheduleService

from zerino.publishing.queue.job_queue import JobQueue
from zerino.publishing.publish_job import PlatformTarget

from zerino.publishing.job_events import JobEventStore
from zerino.publishing.scheduled_events import SqliteScheduledStore


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def id_gen() -> str:
    import uuid
    return uuid.uuid4().hex


@dataclass
class BatchConfig:
    # renamed to match handler naming (you can keep old names, but this reduces confusion)
    source_root: Path
    destination_root: Path
    processed_history_file: Path

    start_at: datetime
    interval_minutes: int = 120

    allowed_platform_folders: tuple[str, ...] = ("instagram", "tiktok", "youtube")


def run_batch_once(config: BatchConfig) -> int:
    # 1) Wire dependencies
    clock = Clock()
    queue = JobQueue()

    scheduled_events_repo = SqliteScheduledStore()
    job_events_repo = JobEventStore()

    handler = BatchScheduleHandler(
        source_root=config.source_root,
        destination_root=str(config.destination_root),
        processed_history_file=str(config.processed_history_file),
        allowed_platform_folders=config.allowed_platform_folders,
    )

    planner = BatchSchedulePlanner(
        start_at=config.start_at,
        interval_minutes=config.interval_minutes,
    )

    service = BatchScheduleService(
        scheduled_events_repo=scheduled_events_repo,
        job_events_repo=job_events_repo,
        queue=queue,
        id_gen=id_gen,
        clock=clock,
    )

    # 2) Discover items
    items = handler.process_exports()
    if not items:
        print("No new batch items found.")
        return 0

    # 3) Plan times (1:1 with items)
    scheduled_times = planner.plan(len(items))

    # 4) Map item -> targets + content
    def targets_for_item(item) -> list[PlatformTarget]:
        account_id_by_platform = {
            "twitter": "twitter"
            '''"instagram": "ig_account_id",
            "tiktok": "tt_account_id",
            "youtube": "yt_account_id",'''
        }
        return [
            PlatformTarget(
                platform=item.platform,
                social_account_id=account_id_by_platform[item.platform],
            )
        ]

    def build_content_for_item(item) -> dict:
        # NOTE: ensure your BatchItem exposes dest_path; if it uses another name,
        # update this to match (e.g., item.path, item.moved_path, etc.)
        return {
            "media_path": str(item.dest_path),
            "caption": getattr(item, "caption", None),
            "metadata": {"source": "batch_schedule"},
        }

    # 5) Create + persist + enqueue
    jobs = service.run_batch(
        items=items,
        scheduled_times=scheduled_times,
        targets_for_item=targets_for_item,
        build_content_for_item=build_content_for_item,
    )

    # 6) Print summary
    print(f"Batch created {len(jobs)} jobs from {len(items)} items.")
    for job in jobs[:10]:
        print(f"- {job.platform} {job.social_account_id} @ {job.scheduled_at.isoformat()}")

    return len(jobs)


if __name__ == "__main__":
    cfg = BatchConfig(
        source_root=Path("clip_engine/exports"),
        destination_root=Path("content_exports"),
        processed_history_file=Path("content_exports/processed_history.json"),
        start_at=datetime.now(timezone.utc),
        interval_minutes=120,
        allowed_platform_folders=("instagram", "tiktok", "youtube"),
    )
    run_batch_once(cfg)