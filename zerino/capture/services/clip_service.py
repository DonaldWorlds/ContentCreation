from pathlib import Path

from zerino.db.repositories.clip_repository import ClipRepository
from zerino.db.repositories.marker_repository import MarkerRepository
from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.ffmpeg.clip_generator import ClipGeneratorProcess
from zerino.publishing.clip_to_posts import queue_clips_for_posting


class ClipService:
    CLIP_DURATION = 30
    PRE_BUFFER = 10

    def __init__(self, clip_repo=None, marker_repo=None, recording_repo=None, generator=None):
        self.clip_repo = clip_repo or ClipRepository()
        self.marker_repo = marker_repo or MarkerRepository()
        self.recording_repo = recording_repo or RecordingRepository()
        self.generator = generator or ClipGeneratorProcess()

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

        video_file = recording["filename"]
        clip_specs: list[tuple[int, Path]] = []

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
            clip_specs.append((clip_id, Path(output_path)))

        print(f"Created {len(clip_specs)} clips for recording {recording_id}")

        # Hand the batch off to the publishing bridge:
        # first clip posts immediately, the rest are scheduled +120 min apart.
        if clip_specs:
            queue_clips_for_posting(clip_specs)

        

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