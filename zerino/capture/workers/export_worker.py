from __future__ import annotations

import time

from zerino.capture.services.export_service import ExportService
from zerino.config import get_logger

log = get_logger("zerino.capture.export_worker")


class ExportWorker:
    def __init__(self, export_queue):
        self.export_service = ExportService()
        self.export_queue = export_queue

    def run(self):
        log.info("export worker started")

        while True:
            try:
                job = self.export_queue.get_job(timeout=1)
                if not job:
                    continue

                try:
                    job_type = job.get("type")
                    if job_type == "export_ready":
                        export_id = job.get("export_id")
                        self.process_export_job(export_id)
                    else:
                        log.warning("unknown job type: %r", job_type)
                except Exception:
                    log.exception("export worker: job failed job=%r", job)
                finally:
                    self.export_queue.task_done()

            except Exception:
                # Defensive: if get_job itself ever raises (queue corruption,
                # etc.) don't kill the loop. Pause briefly then retry.
                log.exception("export worker: outer loop error")
                time.sleep(2)

    def process_export_job(self, export_id):
        log.info("processing export_id=%s", export_id)
        try:
            self.export_service.process_export(export_id)
            log.info("finished export_id=%s", export_id)
        except Exception:
            log.exception("failed export_id=%s", export_id)
            raise
