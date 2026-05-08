from __future__ import annotations

from pathlib import Path

from zerino.config import get_logger
from zerino.db.repositories.clip_repository import ClipRepository
from zerino.db.repositories.marker_repository import MarkerRepository
from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.ffmpeg.clip_generator import ClipGeneratorProcess
from zerino.publishing.clip_to_posts import queue_clips_for_posting

log = get_logger("zerino.capture.clip_service")


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
            log.info("no clip windows to create recording_id=%s", recording_id)
            return

        recording = self.recording_repo.get_recording(recording_id)
        if not recording:
            log.error("recording not found recording_id=%s", recording_id)
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
                log.info(
                    "clip already exists recording_id=%s marker_id=%s start=%s end=%s — skipping",
                    recording_id, marker_id, start, end,
                )
                continue

            # Create the DB row FIRST, so a generation failure leaves a
            # 'failed' clip record the user can see / retry — instead of a
            # silent crash with no DB trace.
            clip_id = self.clip_repo.create_clip(
                recording_id=recording_id,
                marker_id=marker_id,
                video_file=video_file,
                start=start,
                end=end,
            )
            self.clip_repo.mark_processing(clip_id)

            try:
                output_path = self.generator.generate_clip(video_file, start, end)
            except Exception as e:
                # Truncate to keep the DB column readable; full traceback is
                # in the log file.
                err = f"{type(e).__name__}: {str(e)[:480]}"
                log.exception(
                    "clip generation failed clip_id=%s recording_id=%s marker_id=%s start=%s end=%s",
                    clip_id, recording_id, marker_id, start, end,
                )
                self.clip_repo.mark_failed(clip_id, err)
                continue

            if not output_path:
                msg = "generator returned no output_path"
                log.error("clip generation produced no file clip_id=%s: %s", clip_id, msg)
                self.clip_repo.mark_failed(clip_id, msg)
                continue

            self.clip_repo.mark_completed(clip_id, output_path)
            clip_specs.append((clip_id, Path(output_path)))
            log.info("clip ready clip_id=%s -> %s", clip_id, output_path)

        log.info("created %d clip(s) for recording_id=%s", len(clip_specs), recording_id)

        # Hand the batch off to the publishing bridge:
        # first clip posts immediately, the rest are scheduled +120 min apart.
        if clip_specs:
            queue_clips_for_posting(clip_specs)

    def process_recording(self, recording_id):
        markers = self.marker_repo.get_markers_for_recording(recording_id)

        if not markers:
            log.info("no markers found recording_id=%s", recording_id)
            return

        windows = self.generate_clip_windows(markers)

        if not windows:
            log.info("no clip windows generated recording_id=%s", recording_id)
            return

        self.create_clips(recording_id, windows)
