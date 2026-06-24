"""CP2 (RED): overlapping candidates merge into one; non-overlapping kept."""
from zerino.detection.events import Candidate
from zerino.detection.core.dedupe import dedupe


def test_overlapping_candidates_merge(ev, params):
    c1 = Candidate(anchor_t=10.0, win_start=2.0, win_end=18.0, score=3.0,
                   events=(ev(10.0, weight=1.0),))
    c2 = Candidate(anchor_t=15.0, win_start=11.0, win_end=27.0, score=5.0,
                   events=(ev(15.0, weight=2.0),))  # overlaps c1 (11 < 18)
    out = dedupe([c1, c2], params)
    assert len(out) == 1
    # re-anchored on the higher-salience event (weight 2.0 at t=15)
    assert out[0].anchor_t == 15.0


def test_non_overlapping_candidates_kept(ev, params):
    c1 = Candidate(anchor_t=10.0, win_start=2.0, win_end=18.0, score=3.0, events=(ev(10.0),))
    c2 = Candidate(anchor_t=50.0, win_start=42.0, win_end=58.0, score=3.0, events=(ev(50.0),))
    assert len(dedupe([c1, c2], params)) == 2
