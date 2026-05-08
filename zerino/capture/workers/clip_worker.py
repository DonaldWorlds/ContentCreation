from __future__ import annotations

from zerino.capture.services.clip_service import ClipService
from zerino.capture.services.queue_service import PipelineQueueService
from zerino.config import get_logger

log = get_logger("zerino.capture.clip_worker")


class ClipWorker:
    def __init__(self, clip_service=None, pipeline_queue_service=None):
        self.clip_service = clip_service or ClipService()
        self.pipeline_queue_service = pipeline_queue_service or PipelineQueueService()

    def run(self):
        log.info("clip worker started")
        try:
            while True:
                job = self.pipeline_queue_service.get_job(timeout=1)
                if not job:
                    continue

                # Inner try/except so ONE bad job doesn't take the worker down.
                # task_done() must always run, even on exception, or the queue
                # join() will hang and producers will think work is in flight.
                try:
                    job_type = job.get("type")
                    if job_type == "recording_finished":
                        recording_id = job.get("recording_id")
                        log.info("processing recording_finished recording_id=%s", recording_id)
                        self.clip_service.process_recording(recording_id)
                        log.info("done recording_id=%s", recording_id)
                    else:
                        log.warning("unknown job type: %r", job_type)
                except Exception:
                    log.exception("clip worker: job failed job=%r", job)
                finally:
                    self.pipeline_queue_service.task_done()

        except KeyboardInterrupt:
            log.info("clip worker stopped (KeyboardInterrupt)")
