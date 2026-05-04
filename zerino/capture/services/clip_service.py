from zerino.db.repositories.clip_repository import ClipRepository
from zerino.db.repositories.marker_repository import MarkerRepository
from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.ffmpeg.clip_generator import ClipGeneratorProcess
from zerino.capture.services.export_service import ExportService


class ClipService:
    CLIP_DURATION = 30
    PRE_BUFFER = 10

    def __init__(self, clip_repo=None, marker_repo=None,export_service=None, recording_repo=None, generator=None):
        self.clip_repo = clip_repo or ClipRepository()
        self.marker_repo = marker_repo or MarkerRepository()
        self.recording_repo = recording_repo or RecordingRepository()
        self.generator = generator or ClipGeneratorProcess()
        self.export_service = export_service or ExportService()

    def process_single_marker(self, marker):
        marker_time = marker["timestamp"]
        start = max(0, marker_time - self.PRE_BUFFER)
        end = marker_time + (self.CLIP_DURATION - self.PRE_BUFFER)

        if start >= end:
            return None

        return {"marker_id": marker["id"], "start": start, "end": end}

    def generate_clip_windows(self, markers):
        windows = []
        for marker in markers:
            window = self.process_single_marker(marker)
            if window:
                windows.append(window)
        return windows

    def create_clips(self, recording_id, windows):
        if not windows:
            print(f"No clip windows to create for recording {recording_id}")
            return

        recording = self.recording_repo.get_recording(recording_id)
        if not recording:
            print(f"Recording not found: {recording_id}")
            return

        created_count = 0
        video_file = recording["filename"]

        for window in windows:
            marker_id = window["marker_id"]
            start = window["start"]
            end = window["end"]

            if marker_id is None or start is None or end is None:
                continue

            if self.clip_repo.clip_exists(recording_id, start, end):
                continue

            output_path = self.generator.generate_clip(video_file, start, end)
            if not output_path:
                print(f"Failed to generate clip for marker {marker_id}")
                continue

            clip_id = self.clip_repo.create_clip(
                recording_id=recording_id,
                marker_id=marker_id,
                video_file=video_file,
                start=start,
                end=end,
            )
            self.clip_repo.mark_completed(clip_id, output_path)
            self.export_service.process_exports_for_clip(clip_id)
            created_count += 1

        print(f"Created {created_count} clips for recording {recording_id}")

        

    def process_recording(self, recording_id):
        markers = self.marker_repo.get_markers_for_recording(recording_id)

        if not markers:
            print(f"No markers found for recording {recording_id}")
            return

        windows = self.generate_clip_windows(markers)

        if not windows:
            print(f"No clip windows generated for recording {recording_id}")
            return

        self.create_clips(recording_id, windows)