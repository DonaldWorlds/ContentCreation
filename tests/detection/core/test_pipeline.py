"""CP2 (RED): end-to-end core — dense cluster survives, weak isolated event dropped."""
import pytest

from zerino.detection.core.pipeline import run


def test_pipeline_drops_weak_and_keeps_dense_cluster(ev, params):
    # dense cluster of 3 kills (0-2s) -> base 3, span 2, n 3 -> score 3*(1+2/3)=5.0
    # plus one weak isolated event far away -> score 0.01 < threshold 1.0 -> dropped
    events = [ev(0.0), ev(1.0), ev(2.0), ev(50.0, weight=0.1, confidence=0.1)]
    cands = run(events, params, duration=100.0)
    assert len(cands) == 1
    assert cands[0].score == pytest.approx(5.0)
    assert cands[0].anchor_t == 2.0   # equal salience -> tie-break latest t
