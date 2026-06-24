"""CP2 (RED): asymmetric anchored windowing + clamping."""
import pytest

from zerino.detection.core.window import window_candidate


def test_anchor_is_max_salience_event(ev, params):
    evs = [ev(10.0, weight=1.0), ev(12.0, weight=3.0), ev(14.0, weight=1.0)]
    cand = window_candidate(evs, score=5.0, params=params, duration=100.0)
    assert cand.anchor_t == 12.0


def test_window_is_asymmetric_with_climax_in_65_to_70_pct(ev, params):
    cand = window_candidate([ev(20.0)], score=1.0, params=params, duration=100.0)
    assert cand.win_start == pytest.approx(12.0)   # 20 - pre(8)
    assert cand.win_end == pytest.approx(24.0)     # 20 + post(4)
    frac = (cand.anchor_t - cand.win_start) / (cand.win_end - cand.win_start)
    assert 0.65 <= frac <= 0.70


def test_window_clamps_to_media_start_and_meets_min_dur(ev, params):
    # anchor=3, pre=8 -> raw start -5 -> clamp to 0; extend to min_dur within bounds
    cand = window_candidate([ev(3.0)], score=1.0, params=params, duration=100.0)
    assert cand.win_start == 0.0
    assert (cand.win_end - cand.win_start) >= params.min_dur


def test_window_clamps_to_media_end(ev, params):
    cand = window_candidate([ev(99.0)], score=1.0, params=params, duration=100.0)
    assert cand.win_end == pytest.approx(100.0)
