"""Cluster-span windowing: a clip covers the whole fight [first-pre, last+post].

Single-event clusters reduce to the old asymmetric anchored window (climax ~67%) — single
highlights are unchanged. Multi-event clusters (a team wipe) span first->last so every kill
lands in ONE clip; anchor_t stays the climax for metadata/ranking; over-long fights cap by
trimming the lead-in (the climax + aftermath survive)."""
import pytest

from zerino.detection.core.window import window_candidate


def test_anchor_is_max_salience_event(ev, params):
    evs = [ev(10.0, weight=1.0), ev(12.0, weight=3.0), ev(14.0, weight=1.0)]
    cand = window_candidate(evs, score=5.0, params=params, duration=100.0)
    assert cand.anchor_t == 12.0


# --- single-event cluster: unchanged old behavior (climax at ~67%) ---

def test_single_event_window_is_asymmetric_with_climax_in_65_to_70_pct(ev, params):
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


# --- multi-event cluster: clip SPANS the whole fight (the new intended behavior) ---

def test_window_spans_cluster_first_to_last(ev):
    # a 4-kill team wipe spread over time -> ONE clip from first-pre to last+post,
    # containing every kill (this is what cluster_gap=24 + cluster-span buys us).
    from zerino.detection.core.params import CoreParams
    p = CoreParams(pre=5.0, post=8.0, cluster_gap=24.0, cluster_bonus=1.0,
                   score_threshold=1.0, clip_budget=3, min_dur=30.0, max_dur=60.0)
    evs = [ev(372.0), ev(394.0), ev(398.0), ev(410.0)]   # span 38s, +pads 51s < max_dur
    cand = window_candidate(evs, score=6.0, params=p, duration=691.0)
    assert cand.win_start == pytest.approx(372.0 - p.pre)    # first - pre
    assert cand.win_end == pytest.approx(410.0 + p.post)     # last + post
    # every kill is inside the single clip
    assert all(cand.win_start <= e.t <= cand.win_end for e in evs)
    assert cand.anchor_t == 410.0   # climax = latest max-salience kill


def test_window_caps_long_fight_by_trimming_lead_in(ev):
    # span (10..80 = 70s) + pads exceeds max_dur -> trim the LEAD-IN, keep climax + aftermath
    from zerino.detection.core.params import CoreParams
    p = CoreParams(pre=5.0, post=8.0, cluster_gap=24.0, cluster_bonus=1.0,
                   score_threshold=1.0, clip_budget=3, min_dur=30.0, max_dur=55.0)
    evs = [ev(10.0), ev(45.0), ev(80.0)]
    cand = window_candidate(evs, score=4.0, params=p, duration=691.0)
    assert (cand.win_end - cand.win_start) == pytest.approx(p.max_dur)
    assert cand.win_end == pytest.approx(80.0 + p.post)   # climax + aftermath preserved
    assert cand.win_start == pytest.approx(cand.win_end - p.max_dur)  # lead-in trimmed
