# app/workers/manual_post_worker.py
import logging
from zerino.publishing.queue.job_queue import JobQueue
from zerino.publishing.publish_job import PublishJob
from zerino.publishing.publisher.manual_zernio_publisher import publish_job  # service-layer function

logger = logging.getLogger(__name__)

class ManualPostWorker:
    def __init__(self, queue: JobQueue):
        self.queue = queue

    def process_next_job(self):
        if self.queue.is_empty():
            logger.info("No jobs to process")
            return None

        job: PublishJob = self.queue.dequeue()

        try:
            result = publish_job(job)
            logger.info("Job %s submitted to Zernio", job.id)
            return result
        except Exception:
            logger.exception("Job %s failed", getattr(job, "id", "unknown"))
            raise