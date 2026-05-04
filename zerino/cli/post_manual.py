import logging

from zerino.publishing.queue.job_queue import JobQueue
from zerino.publishing.workers.post_worker import ManualPostWorker
from zerino.publishing.publish_job import PublishJob, PlatformTarget
from zerino.publishing.zernio.oauth import (
    connect_social_account_interactive,
    pick_existing_profile_id,
)
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    queue = JobQueue()
    worker = ManualPostWorker(queue=queue)

    logger.info("System initialized")

    profile_id = pick_existing_profile_id()
    twitter_account_id = connect_social_account_interactive(profile_id, "twitter")

    job = PublishJob(
        id="job_001",
        mode="manual",
        content="Hello from Zernio",
        media_paths=["/Users/donaldk/Desktop/Scrrb.png"],
        scheduled_for=datetime.now(timezone.utc) + timedelta(minutes=2),
        timezone="UTC",
        targets=[PlatformTarget(platform="twitter", account_id=twitter_account_id)],
    )

    queue.enqueue(job)
    worker.process_next_job()

if __name__ == "__main__":
    main()