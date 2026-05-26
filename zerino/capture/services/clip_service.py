from __future__ import annotations

from pathlib import Path

from zerino.config import RECORDINGS_DIR, get_logger
from zerino.ffmpeg.ffmpeg_utils import has_video_stream
from zerino.db.repositories.clip_repository import ClipRepository
from zerino.db.repositories.marker_repository import MarkerRepository
from zerino.db.repositories.recording_repository import RecordingRepository
from zerino.models import ClipJob
from zerino.publishing.clip_to_posts import queue_clip_jobs_for_posting

log = get_logger("zerino.capture.clip_service")

# Clean-webcam recordings (OBS Source Record plugin) land here, next to the
# game recordings in RECORDINGS_DIR. The watchdog watches RECORDINGS_DIR
# non-recursively, so these never trigger their own clip runs — they're
# only looked up as the pair for a finished game recording.
FACE_RECORDINGS_DIR = RECORDINGS_DIR / "face"

# A game recording and its face partner are written by the same Record press,
# but the two outputs can differ by a second or so. Pair by closest mtime
# within this window; outside it, assume no pair (single-source fallback).
FACE_PAIR_WINDOW_SEC = 15.0


class ClipService:
    # 60 s is the sweet spot across the four target platforms:
    #   - YouTube Shorts: max 60 s. 60.0s still uploads AS a Short.
    #   - TikTok: long-form monetization tiers (Creator Rewards in some
    #     regions) require >= 60 s; 60.0s qualifies in most.
    #   - Facebook Reels: max 90 s. 60 s is well under.
    #   - Twitter / X: 140 s default ceiling. 60 s is fine.
    # 61+ would push YouTube uploads out of the Shorts feed (~10x less
    # reach), so 60 is the global compromise. PRE_BUFFER stays at 10 so
    # the streamer's pre-marker context survives; the 50 s of post-marker
    # room covers the actual reaction.
    CLIP_DURATION = 60
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

    def _find_face_pair(self, game_path: Path) -> Path | None:
        """Find the clean-webcam recording paired with `game_path`.

        OBS writes the main recording and its Source Record webcam file with
        the SAME start-timestamp name — game "2026-05-26 00-18-20.mkv" pairs
        with face "2026-05-26 00-18-20.mp4". That shared start timestamp is
        the reliable pair key, so we match by FILENAME first and validate the
        match actually contains video.

        Only if no valid name match exists do we fall back to closest-mtime
        pairing. mtime is the file's FINISH time, so a spurious/empty webcam
        file created at stream-end (camera dropped and OBS rolled a new file)
        can sit closer in mtime than the real webcam file and get mis-picked —
        the exact failure that put a corrupt face on a whole batch. Every
        candidate is validated with has_video_stream(); files with no real
        video are skipped. Returns None (single-source fallback) if nothing
        valid pairs. Defensive: any error returns None (never blocks the run).
        """
        try:
            if not FACE_RECORDINGS_DIR.is_dir():
                return None
            candidates = sorted(FACE_RECORDINGS_DIR.glob("*.mp4"))
            if not candidates:
                return None

            stem = game_path.stem  # OBS start-timestamp, e.g. "2026-05-26 00-18-20"

            # 1) NAME match — the webcam file carrying the game recording's
            #    start timestamp. Correct even when a later, empty file has a
            #    closer mtime. Substring match tolerates any suffix OBS appends.
            named = [f for f in candidates if stem and stem in f.stem]
            for f in named:
                if has_video_stream(f):
                    log.info("paired face by NAME %s with game %s", f.name, game_path.name)
                    return f
                log.warning(
                    "name-matched face %s has NO video stream (camera/capture dropped) — "
                    "skipping it; the webcam recording for %s looks broken.",
                    f.name, game_path.name,
                )

            # 2) FALLBACK — closest mtime within the window, validated. Only
            #    reached when no name match exists at all.
            game_mtime = game_path.stat().st_mtime
            in_window = sorted(
                (
                    (abs(f.stat().st_mtime - game_mtime), f)
                    for f in candidates
                    if abs(f.stat().st_mtime - game_mtime) <= FACE_PAIR_WINDOW_SEC
                ),
                key=lambda t: t[0],
            )
            for delta, f in in_window:
                if has_video_stream(f):
                    log.info(
                        "paired face by mtime %s (delta %.1fs) with game %s (no name match)",
                        f.name, delta, game_path.name,
                    )
                    return f
                log.warning(
                    "mtime candidate face %s (delta %.1fs) has no video stream — skipping",
                    f.name, delta,
                )

            log.warning(
                "no valid face recording paired with %s — rendering single-source "
                "(gameplay-only) for this batch. Check recordings/face/ and your webcam.",
                game_path.name,
            )
            return None
        except Exception:
            log.exception("face-pair lookup failed for %s — single-source fallback", game_path)
            return None

    def process_single_marker(self, marker):
        """Compute the (start, end) clip window for a single marker.

        Window is always exactly CLIP_DURATION seconds long. When the
        marker lands within PRE_BUFFER seconds of recording start, the
        pre-roll is clamped to 0 and the post-roll extends accordingly —
        so the clip stays full length instead of being truncated.

        Pre-S3.1 bug: `end = marker_time + (CLIP_DURATION - PRE_BUFFER)`
        anchored the end off the raw marker_time, ignoring the start
        clamp. A marker at t=5 (PRE_BUFFER=10) produced start=0, end=25 —
        a 25-second clip instead of 30. By anchoring end off the clamped
        start we get the full CLIP_DURATION every time.
        """
        marker_time = float(marker["timestamp"])
        start = max(0.0, marker_time - self.PRE_BUFFER)
        end = start + self.CLIP_DURATION

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

        # Resolve the clean-webcam pair once for the whole recording. Split
        # (F9) and square (F8) jobs use it; vertical ignores it. None when
        # the operator hasn't set up Source Record (single-source fallback).
        face_source_path = self._find_face_pair(source_path)

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
                face_source_path=face_source_path,
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
