"""
Capture daemon — single entrypoint that starts every capture-side worker
in one process. Run this BEFORE you start streaming.

What it does (all running concurrently):
  1. Watchdog observer on `recordings/` — detects new OBS recordings starting
     and stopping (auto-detects "stream ended" via file size stability).
  2. F8 hotkey listener — every press inserts a marker row at the current
     in-recording timestamp.
  3. Clip worker — when a recording finishes, cuts every marker into a clip,
     hands the batch off to `clip_to_posts.queue_clips_for_posting()` which
     renders each clip and queues post rows (1st immediate, rest +120 min).

Usage:
    # Mac / Linux
    python -m zerino.capture.main

    # Windows
    venv\\Scripts\\python.exe -m zerino.capture.main

The scheduler (`scheduler_runner`) is a SEPARATE daemon that dispatches the
queued post rows to Zernio. Run it alongside this one (see ops/README.md
for launchd / Task Scheduler setup so it stays alive across reboots).
"""
from __future__ import annotations

import threading
import time

from watchdog.observers import Observer

from zerino.capture.handlers.recording_handler_worker import RecordingHandler
from zerino.capture.services.clip_service import ClipService
from zerino.capture.services.marker_service import MarkerService
from zerino.capture.services.queue_service import PipelineQueueService
from zerino.capture.services.recording_service import RecordingService
from zerino.capture.workers.clip_worker import ClipWorker
from zerino.capture.workers.marker_worker import MarkerIngestWorker
from zerino.config import RECORDINGS_DIR, get_logger
from zerino.db.repositories.streamer_repository import StreamerRepository
from zerino.healthcheck import HealthcheckError, run_capture_healthcheck

DEFAULT_STREAMER_NAME = "default"
DEFAULT_STREAMER_PLATFORM = "twitch"


def _ensure_default_streamer() -> int:
    """Return the id of a streamer named 'default', creating one if needed.

    The capture flow attaches markers to a streamer_id. We auto-provision
    a default streamer so the user doesn't have to do any pre-setup.
    """
    repo = StreamerRepository()
    row = repo.get_streamer_by_name(DEFAULT_STREAMER_NAME)
    if row:
        return row[0]
    return repo.create_streamer(DEFAULT_STREAMER_NAME, DEFAULT_STREAMER_PLATFORM)


def main() -> None:
    log = get_logger("zerino.capture.main")
    log.info("=== Zerino capture daemon starting ===")

    try:
        run_capture_healthcheck()
    except HealthcheckError as e:
        log.error("startup healthcheck failed: %s", e)
        raise SystemExit(1)

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    streamer_id = _ensure_default_streamer()

    # Shared state (with a lock — marker/recording services use it)
    lock = threading.Lock()
    state: dict = {
        "lock": lock,
        "processed_files": set(),
        "markers_temp": [],
        "active_monitors": [],
        "session_active": True,
        "current_streamer_id": streamer_id,
    }

    # CRITICAL: ONE queue shared between the recording service (producer)
    # and the clip worker (consumer). PipelineQueueService default-constructs
    # a new in-memory queue per instance, so without this both halves would
    # talk to separate queues and recording_finished events would be lost.
    queue_service = PipelineQueueService(state=state, lock=lock)

    # --- Recording watchdog ---------------------------------------------- #
    recording_service = RecordingService(
        state, lock=lock, pipeline_queue_service=queue_service,
    )
    handler = RecordingHandler(state=state, recording_service=recording_service)
    observer = Observer()
    observer.schedule(handler, path=str(RECORDINGS_DIR), recursive=False)
    observer.start()
    log.info("watchdog: watching %s", RECORDINGS_DIR)

    # --- Marker hotkey listener (F8) ------------------------------------- #
    marker_service = MarkerService(state, lock=lock)
    marker_worker = MarkerIngestWorker(state=state, marker_service=marker_service)
    threading.Thread(target=marker_worker.start, daemon=True, name="marker-hotkey").start()
    log.info("marker hotkey listener: F8 = talking_head (square), F9 = gameplay (split)")

    # --- Clip worker (queue consumer) ------------------------------------ #
    clip_service = ClipService()
    clip_worker = ClipWorker(
        clip_service=clip_service, pipeline_queue_service=queue_service,
    )
    threading.Thread(target=clip_worker.run, daemon=True, name="clip-worker").start()
    log.info("clip worker: ready")

    log.info("=== All capture workers running. Ctrl+C to stop. ===")
    log.info(
        "Now: start your OBS recording, press F8 for talking-head moments or "
        "F9 for gameplay moments during the stream, then end recording."
    )
    log.info(
        "OBS tip: in Settings → Output, enable 'Automatically record when streaming' "
        "+ Recording → 'Use stream encoder' so one Start Streaming click drives "
        "both outputs from a single encoder. Two encoders contend for CPU and "
        "produce stuttery recordings on long sessions."
    )
    log.info(
        "Alternate entry point: drop a video file with `python -m zerino.cli.clip_file --file <path>`"
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Capture daemon stopping...")
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
