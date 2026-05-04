import time

from zerino.capture.services.clip_service import ClipService
from zerino.ffmpeg.clip_generator import ClipGeneratorProcess
from zerino.capture.services.queue_service import PipelineQueueService


class ClipWorker:
    def __init__(self, clip_service=None, pipeline_queue_service=None):
        self.clip_service = clip_service or ClipService()
        self.pipeline_queue_service = pipeline_queue_service or PipelineQueueService()
        

    def run(self):
        print("Clip Worker started")
        try:
            while True:
                job = self.pipeline_queue_service.get_job(timeout=1)
                if not job:
                    continue

                try:
                    if job["type"] == "recording_finished":
                        recording_id = job["recording_id"]
                        self.clip_service.process_recording(recording_id)
                    else:
                        print(f"[CLIP WORKER] Unknown job type: {job.get('type')}")
                finally:
                    self.pipeline_queue_service.task_done()

        except KeyboardInterrupt:
            print("Clip Worker stopped")