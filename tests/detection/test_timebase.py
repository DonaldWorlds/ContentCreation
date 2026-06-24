"""CP2 (RED): the timebase utility must map frames/samples to source-relative seconds,
and must use real PTS on VFR sources (not index/avg_fps)."""
import pytest

from zerino.detection.timebase import Timebase


def test_frame_to_sec_cfr_uses_fps():
    tb = Timebase(fps=30.0, is_vfr=False, duration=10.0)
    assert tb.frame_to_sec(90) == pytest.approx(3.0)


def test_frame_to_sec_vfr_uses_pts_not_index_over_fps():
    # VFR: frame 3 truly lands at 0.20s; index/fps (3/30 = 0.10) would be wrong.
    tb = Timebase(fps=30.0, is_vfr=True, duration=10.0,
                  frame_pts_sec=(0.0, 0.05, 0.12, 0.20))
    assert tb.frame_to_sec(3) == pytest.approx(0.20)


def test_sample_to_sec_uses_sample_rate():
    tb = Timebase(fps=30.0, is_vfr=False, duration=10.0)
    assert tb.sample_to_sec(48000, 48000) == pytest.approx(1.0)
