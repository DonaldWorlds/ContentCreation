import time
from zerino.capture.services.export_service import ExportService
from zerino.capture.services.queue_service import PipelineQueueService

class ExportWorker:
    def __init__(self, export_queue):
        self.export_service = ExportService()
        self.export_queue = export_queue

    def run(self):
        print("🚀 Export Worker Started...")

        while True:
            try:
                print("[WORKER] Waiting for export job...")
                job = self.export_queue.get_job(timeout=1)
                if not job:
                    continue

                try:
                    if job["type"] == "export_ready":
                        export_id = job["export_id"]
                        self.process_export_job(export_id)
                    else:
                        print(f"[WORKER] Unknown job type: {job.get('type')}")
                finally:
                    self.export_queue.task_done()

            except Exception as e:
                print(f"[WORKER] Worker error: {e}")
                time.sleep(2)

    def process_export_job(self, export_id):
        try:
            print(f"[WORKER] Processing export_id={export_id}")
            self.export_service.process_export(export_id)
            print(f"[WORKER] Finished export_id={export_id}")
        except Exception as e:
            print(f"[WORKER] Failed export_id={export_id}: {e}")