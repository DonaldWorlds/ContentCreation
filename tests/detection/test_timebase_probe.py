"""CP2 (RED): probe_timebase against a REAL OBS recording (the deferred Phase-0.5 VFR check).

Evidence (Windows VFR sweep): every OBS .mkv is HEVC CFR (r_frame_rate == avg_frame_rate),
30 or 60 fps, 1ms-rounded PTS. probe_timebase must report CFR, fps from avg_frame_rate,
and NOT mistake the ms-rounding for VFR. Reds on NotImplementedError until CP3.
"""
from pathlib import Path

import pytest

from zerino.detection.timebase import Timebase, probe_timebase

FIXTURES = Path(__file__).parent / "fixtures" / "fortnite_golden"
SEG = FIXTURES / "v25_seg1_src515-580.mp4"  # native 1080p60 CFR cut

pytestmark = pytest.mark.skipif(not SEG.exists(),
                                reason="golden media is machine-local (regenerate via scratchpad/cut_golden.py)")


def test_probe_reports_cfr_and_fps():
    tb = probe_timebase(SEG)
    assert isinstance(tb, Timebase)
    assert tb.is_vfr is False           # OBS recordings are CFR despite ms-rounded PTS
    assert tb.fps in (30.0, 60.0)       # read from avg_frame_rate, never hardcoded
    assert tb.duration > 0


def test_frame_to_sec_uses_fps_for_cfr():
    tb = probe_timebase(SEG)
    # CFR path: frame index maps by fps, anchored to PTS-seconds (not index/avg_fps drift)
    assert tb.frame_to_sec(tb.fps) == pytest.approx(1.0, abs=0.05)
