from queue import Queue, Empty

class PipelineQueueService:
    def __init__(self, state=None, queue=None, lock=None):
        self.state = state or {}
        self.queue = queue or Queue()
        self.lock = lock

    def enqueue_recording_finished(self, recording_id: int, filename: str) -> None:
        self.queue.put({
            "type": "recording_finished",
            "recording_id": recording_id,
            "filename": filename,
        })

    def enqueue_export_ready(self, export_id: int) -> None:
        self.queue.put({
            "type": "export_ready",
            "export_id": export_id,
        })

    def get_job(self, timeout: int = 1):
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None

    def task_done(self) -> None:
        self.queue.task_done()

    def is_empty(self) -> bool:
        return self.queue.empty()

    def put(self, item):
        self.queue.put(item)

    def get(self, timeout=None):
        return self.queue.get(timeout=timeout)