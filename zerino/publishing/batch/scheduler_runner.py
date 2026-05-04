from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

from zerino.publishing.scheduled_events import SqliteScheduledStore
from zerino.publishing.job_events import JobEventStore
from zerino.publishing.publish_job import PublishJob

from zerino.publishing.batch.zernio_publisher import publish_scheduled_job

logger = logging.getLogger(__name__)


def _extract_post_id(create_result: Any) -> str | None:
    # same best-effort logic
    if isinstance(create_result, dict):
        return (
            create_result.get("id")
            or create_result.get("postId")
            or create_result.get("field_id")
            or create_result.get("data", {}).get("id")
            or create_result.get("data", {}).get("field_id")
        )
    return None

def _job_from_payload(payload: dict[str, Any]) -> PublishJob:
    allowed = set(PublishJob.__annotations__.keys())
    cleaned = {k: v for k, v in payload.items() if k in allowed}
    return PublishJob(**cleaned)


def run_scheduler_loop(
    *,
    db_path: str = "jobs.sqlite3",
    poll_seconds: float = 5.0,
    claim_limit: int = 20,
) -> None:
    scheduled_store = SqliteScheduledStore(db_path=db_path)
    events = JobEventStore(db_path=db_path)

    logger.info("Scheduler runner started. db=%s poll=%.1fs", db_path, poll_seconds)

    while True:
        try:
            due = scheduled_store.claim_due_jobs(limit=claim_limit)
        except Exception:
            logger.exception("Failed to claim due jobs")
            time.sleep(poll_seconds)
            continue

        if not due:
            time.sleep(poll_seconds)
            continue

        logger.info("Claimed %d due job(s)", len(due))

        for row in due:
            job_id = row.id
            try:
                scheduled_store.mark_processing(job_id)
                events.log_job_event(job_id, "processing", "scheduler_runner: marked processing")

                job = _job_from_payload(row.payload)

                result = publish_scheduled_job(job)
                # result can be dict or list[dict]; normalize to first
                first = result[0] if isinstance(result, list) and result else result

                post_id = _extract_post_id(first)
                if not post_id:
                    raise RuntimeError(f"No post id returned from Zernio create. result={first}")

                scheduled_store.mark_submitted(job_id, post_id)
                events.log_job_event(job_id, "submitted", f"zernio_post_id={post_id}")

            except Exception as e:
                logger.exception("Job %s failed", job_id)
                scheduled_store.mark_failed(job_id, str(e))
                events.log_job_event(job_id, "failed", str(e))

        # small delay so we don't spin hard if many jobs are due
        time.sleep(0.1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_scheduler_loop(db_path="jobs.sqlite3", poll_seconds=5.0, claim_limit=20)