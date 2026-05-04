from pathlib import Path
from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.capture.services.recording_service import RecordingService
from zerino.capture.handlers.recording_handler_worker import RecordingHandler
from watchdog.observers import Observer
import time

class StartupScanHandler:
    def __init__(
        self,
        recording_repository: RecordingRepository,
        recording_service: RecordingService,
        state,
        recording_dir: str = "recordings",
    ):
        self.recording_repository = recording_repository
        self.recording_service = recording_service
        self.state = state
        self.recording_dir = Path(recording_dir)

    def scan_existing_recordings(self):
        """Scan the recordings folder at startup and resume any unfinished recordings."""
        if not self.recording_dir.exists():
            print(f"[START SCAN] Folder not found: {self.recording_dir}")
            return

        for file_path in self.recording_dir.iterdir():
            if not file_path.is_file():
                continue

            try:
                self.handle_existing_recording(file_path)
            except Exception as e:
                print(f"[STARTUP SCAN FAILED] {file_path.name}: {e}")

    def mark_recording_processing(self, recording_id):
        """Mark a recording row as processing in the database."""
        return self.recording_repository.mark_recording_processing(recording_id)

    def mark_recording_completed(self, recording_id):
        """Mark a recording row as completed in the database."""
        return self.recording_repository.mark_recording_completed(recording_id)

    def mark_recording_failed(self, recording_id):
        """Mark a recording row as failed in the database."""
        return self.recording_repository.mark_recording_failed(recording_id)

    def handle_existing_recording(self, file_path):
        """Resume processing for a recording that already exists on disk."""
        filename = file_path.name
        row = self.recording_repository.get_by_filename(filename)

        if row is None:
            recording_id = self.recording_repository.create_recording(filename)
        else:
            recording_id = row[0]
            status = row[2]
            if status == "completed":
                return

        self.mark_recording_processing(recording_id)

        try:
            self.mark_recording_completed(recording_id)
        except Exception:
            self.mark_recording_failed(recording_id)
            raise


def main():
    print("Starting clip engine system...")

    state = {
        "processed_files": set()
    }

    recording_repo = RecordingRepository()
    recording_service = RecordingService(state)

    start_scan_service = StartupScanHandler(
        recording_repository=recording_repo,
        recording_service=recording_service,
        state=state,
    )

    start_scan_service.scan_existing_recordings()

    handler = RecordingHandler(state=state, recording_service=recording_service)
    observer = Observer()
    observer.schedule(handler, path="recordings", recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()

if __name__ == '__main__':
    main()