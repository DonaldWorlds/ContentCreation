"""Fixtures for core tests: a synthetic Event factory + canonical CoreParams."""
import pytest

from zerino.detection.events import Event
from zerino.detection.core.params import CoreParams


@pytest.fixture
def ev():
    def _make(t, weight=1.0, confidence=1.0, type="KILL", source="ocr"):
        return Event(t=t, type=type, source=source, confidence=confidence, weight=weight)
    return _make


@pytest.fixture
def params():
    # pre/post -> climax at 8/12 = 66.7% (in the 65-70% band)
    return CoreParams(
        pre=8.0, post=4.0, cluster_gap=5.0, cluster_bonus=1.0,
        score_threshold=1.0, clip_budget=3, min_dur=8.0, max_dur=45.0,
    )
