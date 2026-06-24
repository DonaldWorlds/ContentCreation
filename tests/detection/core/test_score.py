"""CP2 (RED): clustering + scoring, incl. the key density property."""
import pytest

from zerino.detection.core.score import cluster, score_cluster


def test_cluster_splits_on_gap(ev, params):
    clusters = cluster([ev(0.0), ev(2.0), ev(20.0)], params.cluster_gap)
    assert [len(c) for c in clusters] == [2, 1]


def test_cluster_joins_within_gap(ev, params):
    clusters = cluster([ev(0.0), ev(4.0), ev(8.0)], params.cluster_gap)
    assert len(clusters) == 1 and len(clusters[0]) == 3


def test_isolated_event_score_is_base(ev, params):
    # n == 1 -> score == base == weight*confidence (no clustering bonus)
    assert score_cluster([ev(10.0, weight=2.0, confidence=0.5)], params) == pytest.approx(1.0)


def test_dense_cluster_outranks_sparse_same_count(ev, params):
    dense = [ev(0.0), ev(1.0), ev(2.0)]       # span 2
    sparse = [ev(0.0), ev(30.0), ev(60.0)]    # span 60, same n + weights
    assert score_cluster(dense, params) > score_cluster(sparse, params)
