import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

# JobQueue is the in-memory queue that stores job objects waiting to be processed.
# It does not define the job data itself; it only holds, orders, and returns jobs
# so workers can dequeue them and process them one at a time.


class JobQueue:
    def __init__(self):
        # deque is a double-ended queue.
        # It lets us add items to the right and remove them from the left very efficiently.
        # That makes it a good simple queue structure.
        self._items = deque()
        logger.debug("JobQueue initialized with empty deque")

    def enqueue(self, job: Any):
        # Add a new job to the back of the queue.
        # This is the "put work in the queue" step.
        self._items.append(job)
        logger.info("Job enqueued. Queue size is now %d", len(self._items))
        return job

    def dequeue(self):
        # Remove and return the job at the front of the queue.
        # If the queue is empty, raise an error so the worker knows there is no work.
        if not self._items:
            logger.warning("Attempted to dequeue from an empty queue")
            raise IndexError("dequeue from an empty queue")
        job = self._items.popleft()
        logger.info("Job dequeued. Queue size is now %d", len(self._items))
        return job

    def is_empty(self):
        # Check whether the queue currently has any jobs in it.
        empty = len(self._items) == 0
        logger.debug("Queue empty check: %s", empty)
        return empty

    def __len__(self):
        # Return how many jobs are currently waiting in the queue.
        size = len(self._items)
        logger.debug("Queue length requested: %d", size)
        return size