from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from zerino.publishing.publish_job import PublishJob, PlatformTarget


@dataclass
class BatchScheduleService:
    """
    Orchestration layer:
    - Converts BatchItems + scheduled times into PublishJobs
    - Persists schedule state
    - Emits audit events
    - Enqueues jobs
    """

    scheduled_events_repo: object
    job_events_repo: object
    queue: object
    id_gen: object
    clock: object  # should expose .now() -> datetime (tz-aware recommended)

    def create_jobs(
        self,
        items: Sequence[object],  # ideally: Sequence[BatchItem]
        scheduled_times: Sequence[datetime],
        targets_for_item: callable,
        build_content_for_item: callable,
    ) -> list[PublishJob]:
        """
        Pure conversion step (no enqueue). Easy to test.

        - targets_for_item(item) -> list[PlatformTarget]
        - build_content_for_item(item) -> dict (caption/title/media_path/etc.)
        """
        if len(items) != len(scheduled_times):
            raise ValueError("items and scheduled_times must be the same length")

        jobs: list[PublishJob] = []

        for item, scheduled_for in zip(items, scheduled_times):
            targets: list[PlatformTarget] = targets_for_item(item)
            content: dict = build_content_for_item(item)

            for target in targets:
                job_id = self.id_gen()

                job = PublishJob(
                    id=job_id,
                    platform=target.platform,
                    social_account_id=target.social_account_id,
                    scheduled_at=scheduled_for,
                    media_path=content.get("media_path"),
                    caption=content.get("caption"),
                    metadata={
                        **content.get("metadata", {}),
                        "batch_item": getattr(item, "clip_id", None),
                    },
                    status="scheduled",
                )
                jobs.append(job)

        return jobs

    def persist_and_enqueue(self, jobs: Sequence[PublishJob]) -> None:
        """
        Side effects:
        - persist schedule records
        - emit queued events
        - enqueue jobs
        """
        if not jobs:
            return

        # 1) Persist durable scheduled state
        self.scheduled_events_repo.create_jobs(list(jobs))

        # 2) Audit + queue
        now = self.clock.now()
        for job in jobs:
            self.job_events_repo.append(job.id, "queued", {"at": now.isoformat()})
            self.queue.enqueue(job)

    def run_batch(
        self,
        items: Sequence[object],
        scheduled_times: Sequence[datetime],
        targets_for_item: callable,
        build_content_for_item: callable,
    ) -> list[PublishJob]:
        """
        Convenience method: create -> persist -> enqueue.
        """
        jobs = self.create_jobs(items, scheduled_times, targets_for_item, build_content_for_item)
        self.persist_and_enqueue(jobs)
        return jobs

        