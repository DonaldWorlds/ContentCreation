import logging
from typing import Any
from zerino.publishing.services.manual_post_service import ManualPostJob

logger = logging.getLogger(__name__)


class PostModeRouter:
    def __init__(self, queue: Any):
        self.queue = queue
        logger.debug("PostModeRouter initialized with queue=%s", type(queue).__name__)

    def route(self, job: ManualPostJob):
        logger.info("Routing job with mode=%s", job.mode)

        if job.mode == "manual":
            logger.debug("Manual mode detected; enqueueing job")
            return self.enqueue_manual_job(job)

        logger.error("Unsupported mode received: %s", job.mode)
        raise ValueError(f"Unsupported mode: {job.mode}")

    def enqueue_manual_job(self, job: ManualPostJob):
        logger.debug("Enqueueing manual job: %s", job)
        result = self.queue.enqueue(job)
        logger.info("Manual job enqueued successfully")
        return result