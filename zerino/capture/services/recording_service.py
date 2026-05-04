from pathlib import Path
import threading
import time

from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.db.repositories.marker_repository import MarkerRepository
from zerino.capture.services.queue_service import PipelineQueueService


class RecordingService:
    def __init__(self, state, recording_repo=None, marker_repo=None, pipeline_queue_service=None, lock=None):
        self.state = state
        self.recording_repo = recording_repo or RecordingRepository()
        self.marker_repo = marker_repo or MarkerRepository()
        self.lock = lock or threading.Lock()
        self.pipeline_queue_service = pipeline_queue_service or PipelineQueueService()
        self.state.setdefault("processed_files", set())
        self.state.setdefault("pipeline_queue", [])
        self.state.setdefault("markers_temp", [])
        self.state.setdefault("active_monitors", [])
        self.state.setdefault("session_active", True)

    def handle_new_recording(self, file_path: Path) -> None:
        file_path = Path(file_path)
        file_key = str(file_path.resolve())

        with self.lock:
            if file_key in self.state["processed_files"]:
                print(f"Skipping already processed: {file_path.name}")
                return
            if self.state.get("current_recording") is not None:
                print(f"Recording already active, ignoring: {file_path.name}")
                return
            if self.state.get("session_active") is False:
                print("No active session")
                return
            self.state["processed_files"].add(file_key)

        print(f"Handling new file: {file_path.name}")
        threading.Thread(target=self._process_file_async, args=(file_path,), daemon=True).start()

    def _process_file_async(self, file_path: Path) -> None:
        file_path = Path(file_path)
        print(f"Async processing: {file_path.name}")

        if not self.wait_for_file_ready(file_path):
            print(f"File {file_path.name} not ready (timed out)")
            return

        if not self.is_valid_recording_file(file_path):
            print(f"File {file_path.name} not valid")
            return

        self.start_recording(file_path)

    def wait_for_file_ready(self, file_path: Path, timeout: int = 45) -> bool:
        start_time = time.time()
        last_size = -1
        print(f"⏳ Waiting for {file_path.name} (timeout: {timeout}s)")

        while time.time() - start_time < timeout:
            if not file_path.exists():
                time.sleep(0.5)
                continue

            current_size = file_path.stat().st_size
            if current_size > 0 and current_size == last_size:
                print(f"✅ File stable at {current_size:,} bytes")
                return True

            last_size = current_size
            time.sleep(0.5)

        print(f"❌ Timeout after {timeout}s")
        return False

    def is_valid_recording_file(self, file_path: Path) -> bool:
        valid_extensions = {".mp4", ".mkv", ".mov"}
        if file_path.suffix.lower() not in valid_extensions:
            print(f"Invalid file type: {file_path.name}")
            return False

        if not file_path.exists():
            print(f"File does not exist: {file_path.name}")
            return False

        try:
            size = file_path.stat().st_size
        except Exception as e:
            print(f"Error reading file size: {e}")
            return False

        if size < 1024:
            print(f"File too small (OBS not started): {file_path.name}")
            return False

        return True

    def start_recording(self, file_path: Path) -> None:
        file_path = Path(file_path)
        filename = file_path.name

        with self.lock:
            if self.state.get("current_recording") is not None:
                print("Attempted to start recording while one is active")
                return

            recording_id = self.recording_repo.create_recording(filename=filename)
            now = time.time()

            self.state["recording_id"] = recording_id
            self.state["start_time"] = now
            self.state["is_recording"] = True
            self.state["current_recording"] = {
                "id": recording_id,
                "filename": filename,
                "filepath": str(file_path),
                "start_time": now,
                "last_size": file_path.stat().st_size,
                "stable_count": 0,
                "status": "recording",
            }

        print(f"Recording started | ID: {recording_id}")

        streamer_id = self.state.get("current_streamer_id")
        markers = self.state.get("markers_temp", [])
        self.state["markers_temp"] = []
        for ts in markers:
            self.marker_repo.insert_marker(
                recording_id=recording_id,
                streamer_id=streamer_id,
                timestamp=ts,
                note=None,
            )
            print(f"Flushed marker @ {ts}s")

        thread = threading.Thread(target=self.monitor_recording, args=(file_path,), daemon=True)
        thread.start()
        self.state.setdefault("active_monitors", []).append(thread)

    def monitor_recording(self, file_path: Path) -> None:
        print(f"Monitoring recording: {file_path.name}")
        while True:
            if self.update_recording_progress(file_path):
                self.finish_recording()
                break
            time.sleep(0.5)

    def update_recording_progress(self, file_path: Path) -> bool:
        with self.lock:
            recording = self.state.get("current_recording")
            if not recording:
                return False

            try:
                current_size = file_path.stat().st_size
            except Exception as e:
                print(f"Error reading file size: {e}")
                return False

            last_size = recording.get("last_size", 0)
            stable_count = recording.get("stable_count", 0)

            if current_size < 10 * 1024 * 1024:
                recording["stable_count"] = 0
                recording["last_size"] = current_size
                return False

            if current_size > last_size:
                recording["stable_count"] = 0
                print(f"📈 Growing: {current_size:,} bytes")
            else:
                recording["stable_count"] = stable_count + 1
                print(f"⏸️  Stable: {recording['stable_count']} checks")

            recording["last_size"] = current_size
            if recording["stable_count"] >= 60:
                print("🛑 Recording stopped (stable for 10s)")
                return True
            return False

    def finish_recording(self) -> None:
        with self.lock:
            recording = self.state.get("current_recording")
            if not recording:
                print("No active recording to finish")
                return

            recording_id = recording.get("id")
            filename = recording.get("filename")
            if not recording_id or not filename:
                print("Invalid recording data")
                return

            self.recording_repo.mark_recording_completed(recording_id)
            self.queue_finished_recording(recording_id, filename)
            self.state["processed_files"].add(str(Path(recording.get("filepath", filename)).resolve()))
            self.state["current_recording"] = None
            self.state["is_recording"] = False

        print(f"Recording finished: {filename}")

    def queue_finished_recording(self, recording_id: int, filename: str) -> None:
        self.pipeline_queue_service.enqueue_recording_finished(recording_id, filename)
        print(f"📦 Queued recording for pipeline: {filename}")
