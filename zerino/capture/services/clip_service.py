from __future__ import annotations

from pathlib import Path

from zerino.config import RECORDINGS_DIR, get_logger
from zerino.db.repositories.clip_repository import ClipRepository
from zerino.db.repositories.marker_repository import MarkerRepository
from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.models import ClipJob
from zerino.publishing.clip_to_posts import queue_clip_jobs_for_posting

log = get_logger("zerino.capture.clip_service")


class ClipService:
    CLIP_DURATION = 30
    PRE_BUFFER = 10

    # Marker kind → render layout. F8 (talking_head) is just-the-face → square
    # fill. F9 (gameplay) is face + game → split (vstack) at 9:16.
    KIND_TO_LAYOUT = {
        "talking_head": "square",
        "gameplay": "split",
    }

    def __init__(self, clip_repo=None, marker_repo=None, recording_repo=None):
        self.clip_repo = clip_repo or ClipRepository()
        self.marker_repo = marker_repo or MarkerRepository()
        self.recording_repo = recording_repo or RecordingRepository()

    def process_single_marker(self, marker):
        marker_time = marker["timestamp"]
        start = max(0, marker_time - self.PRE_BUFFER)
        end = marker_time + (self.CLIP_DURATION - self.PRE_BUFFER)

        if start >= end:
            return None

        kind = marker.get("kind") or "talking_head"
        return {
            "marker_id": marker["id"],
            "start": start,
            "end": end,
            "kind": kind,
        }

    def generate_clip_windows(self, markers):
        windows = []
        for marker in markers:
            window = self.process_single_marker(marker)
            if window:
                windows.append(window)
        return windows

    def create_clips(self, recording_id, windows):
        """Build cut specs for each marker window and hand them to the
        publishing bridge for one-pass render-and-post.

        No intermediate cut file is produced; the source recording is
        seek-into-place once per platform render. Each clip row represents a
        logical (source, start, end) triple — per-platform render status is
        tracked at the post level (posts table).
        """
        if not windows:
            log.info("no clip windows to create recording_id=%s", recording_id)
            return

        recording = self.recording_repo.get_recording(recording_id)
        if not recording:
            log.error("recording not found recording_id=%s", recording_id)
            return

        video_file = recording["filename"]
        source_path = RECORDINGS_DIR / video_file
        if not source_path.exists():
            log.error(
                "source recording missing on disk: %s (recording_id=%s)",
                source_path, recording_id,
            )
            return

        jobs: list[ClipJob] = []

        for window in windows:
            marker_id = window["marker_id"]
            start = window["start"]
            end = window["end"]
            kind = window.get("kind") or "talking_head"
            layout = self.KIND_TO_LAYOUT.get(kind, "square")

            if marker_id is None or start is None or end is None:
                continue

            if self.clip_repo.clip_exists(recording_id, start, end):
                log.info(
                    "clip already exists recording_id=%s marker_id=%s start=%s end=%s — skipping",
                    recording_id, marker_id, start, end,
                )
                continue

            # Create the DB row up front. `video_file` points to the SOURCE
            # recording (no intermediate cut exists in the new flow); the
            # logical clip is fully described by (source, start, end).
            clip_id = self.clip_repo.create_clip(
                recording_id=recording_id,
                marker_id=marker_id,
                video_file=video_file,
                start=start,
                end=end,
            )
            self.clip_repo.mark_processing(clip_id)
            jobs.append(ClipJob(
                clip_id=clip_id,
                source_path=source_path,
                start=float(start),
                end=float(end),
                layout=layout,
            ))
            log.info(
                "clip job queued clip_id=%s recording_id=%s start=%.2f end=%.2f kind=%s layout=%s",
                clip_id, recording_id, start, end, kind, layout,
            )

        if not jobs:
            log.info("no new jobs to queue for recording_id=%s", recording_id)
            return

        log.info("queuing %d clip job(s) for recording_id=%s", len(jobs), recording_id)
        try:
            queue_clip_jobs_for_posting(jobs)
        except Exception as e:
            # Catastrophic failure of the whole batch. Per-platform failures
            # inside the queue function are logged + skipped without raising,
            # so reaching this branch means something more global broke.
            err = f"{type(e).__name__}: {str(e)[:480]}"
            log.exception("batch render+queue failed for recording_id=%s", recording_id)
            for j in jobs:
                self.clip_repo.mark_failed(j.clip_id, err)
            return

        # All jobs rendered + queued. Mark the clip rows completed — they
        # represent the logical clip, not a physical file.
        for j in jobs:
            self.clip_repo.mark_completed(j.clip_id, str(source_path))

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
