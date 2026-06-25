"""CP2 (RED): the Fortnite adapter contract (no media needed for the contract checks).

detect() must return identity-filtered Events; until CP3 it reds on NotImplementedError.
Also pins the adapter's identity/version metadata the emit + idempotency path depends on.
"""
import pytest

from zerino.detection.adapters.base import DetectorAdapter
from zerino.detection.adapters.fortnite import FortniteAdapter
from zerino.detection.events import Event
from zerino.detection.profile import load_profile


def test_is_a_detector_adapter():
    a = FortniteAdapter()
    assert isinstance(a, DetectorAdapter)
    assert a.game_id == "fortnite"
    assert a.detector_version  # non-empty; feeds detections idempotency key


def test_detect_returns_identity_filtered_events():
    a = FortniteAdapter()
    profile = load_profile("fortnite")           # real zerino/detection/profiles/fortnite.yaml
    events = a.detect(media=None, profile=profile)   # CP3 will implement
    assert isinstance(events, list)
    assert all(isinstance(e, Event) for e in events)
    # adapter pre-filters to the operator; core never sees enemy/teammate elims
    assert all(e.type in {"KILL", "KNOCK", "MULTI_ELIM", "VICTORY"} for e in events)
