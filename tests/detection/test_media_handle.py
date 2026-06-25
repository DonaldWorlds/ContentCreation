"""CP2 (RED): MediaHandle — one shared decode reused for audio + gated OCR.

Reds on NotImplementedError until CP3. Verifies the handle exposes the timebase, frame
dimensions, whole-file audio PCM, and streamed frame access for the two-stage gate.
"""
from pathlib import Path

import pytest

from zerino.detection.media import MediaHandle

FIXTURES = Path(__file__).parent / "fixtures" / "fortnite_golden"
SEG = FIXTURES / "v25_seg1_src515-580.mp4"

pytestmark = pytest.mark.skipif(not SEG.exists(),
                                reason="golden media is machine-local")


def test_open_resolves_timebase_and_dims():
    h = MediaHandle.open(SEG)
    assert h.width == 1920 and h.height == 1080
    assert h.timebase is not None and h.timebase.fps in (30.0, 60.0)


def test_audio_pcm_returns_mono_samples():
    h = MediaHandle.open(SEG)
    pcm, sr = h.audio_pcm(sr=16000)
    assert sr == 16000
    assert len(pcm) > 0


def test_frames_at_yields_requested_times():
    h = MediaHandle.open(SEG)
    frames = list(h.frames_at([1.0, 5.0], region={"x": 0.0, "y": 0.5, "w": 0.33, "h": 0.16}))
    assert len(frames) == 2
