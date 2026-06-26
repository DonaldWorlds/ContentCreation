"""CP2 (RED for the auto-trigger): the Windows capture daemon's recording-finished handler
should, when ZERINO_DETECTION_AUTORUN is ON, ALSO run detect_recording AFTER process_recording
(reusing the existing recording-finished signal — clip_worker.py:31). OFF (default) -> daemon
unchanged. Detection import stays lazy (never module-top). Auto-POST is a SEPARATE switch
(ZERINO_DETECTION_AUTOPOST), gated inside detect_recording and covered by test_autopost_flag.

Until CP3 wires the hook, `test_autorun_on_triggers_detection_after_process` fails on a clean
assertion (detect_recording not called) — feature not implemented, NOT an import error.
"""
from pathlib import Path

from zerino.capture.workers import clip_worker as cw_mod
from zerino.capture.workers.clip_worker import ClipWorker


class _StopQueue:
    """Yields the given jobs once, then raises KeyboardInterrupt to end ClipWorker.run()."""
    def __init__(self, jobs):
        self._jobs = list(jobs)
        self.task_done_count = 0

    def get_job(self, timeout=1):
        if self._jobs:
            return self._jobs.pop(0)
        raise KeyboardInterrupt

    def task_done(self):
        self.task_done_count += 1


def _worker(order, jobs):
    class SpyClipService:
        def process_recording(self, rid):
            order.append(f"process:{rid}")
    return ClipWorker(clip_service=SpyClipService(), pipeline_queue_service=_StopQueue(jobs))


def test_recording_finished_runs_process_recording(monkeypatch):
    """§4: the manual F8/F9 flow (process_recording) still runs on recording-finished."""
    monkeypatch.delenv("ZERINO_DETECTION_AUTORUN", raising=False)
    order = []
    w = _worker(order, [{"type": "recording_finished", "recording_id": 7}])
    w.run()
    assert order == ["process:7"]


def test_autorun_off_does_not_trigger_detection(monkeypatch):
    """Default OFF -> detection never runs in the daemon (today's behavior)."""
    monkeypatch.delenv("ZERINO_DETECTION_AUTORUN", raising=False)
    order = []
    monkeypatch.setattr("zerino.cli.detect.detect_recording",
                        lambda rid, **kw: order.append(f"detect:{rid}"))
    w = _worker(order, [{"type": "recording_finished", "recording_id": 7}])
    w.run()
    assert order == ["process:7"]


def test_autorun_on_triggers_detection_after_process(monkeypatch):
    """ON -> detect_recording runs ONCE, AFTER process_recording (additive)."""
    monkeypatch.setenv("ZERINO_DETECTION_AUTORUN", "1")
    order = []
    monkeypatch.setattr("zerino.cli.detect.detect_recording",
                        lambda rid, **kw: order.append(f"detect:{rid}"))
    w = _worker(order, [{"type": "recording_finished", "recording_id": 7}])
    w.run()
    assert order == ["process:7", "detect:7"]


def test_detection_import_is_lazy_not_module_top():
    """Lazy-import guard: clip_worker must not import detection at module top, so a daemon
    with autorun OFF pulls in no detection/OCR/GPU code."""
    src = Path(cw_mod.__file__).read_text().splitlines()
    offenders = [ln for ln in src
                 if (ln.startswith("import ") or ln.startswith("from "))
                 and ("zerino.detection" in ln or "zerino.cli.detect" in ln)]
    assert offenders == []
