"""CP2 (RED): clip-budget shortlist keeps the top-N by score."""
from zerino.detection.events import Candidate
from zerino.detection.core.budget import budget


def _c(score):
    return Candidate(anchor_t=score, win_start=score, win_end=score + 1.0, score=score, events=())


def test_keeps_top_n_by_score():
    out = budget([_c(1.0), _c(5.0), _c(3.0), _c(9.0)], 2)
    assert [c.score for c in out] == [9.0, 5.0]


def test_keeps_all_when_under_budget():
    assert len(budget([_c(1.0), _c(2.0)], 5)) == 2
