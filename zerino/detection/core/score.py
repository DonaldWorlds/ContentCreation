"""Stages 2-3 — cluster + score (Phase 1)."""
from __future__ import annotations

from zerino.detection.events import Event
from zerino.detection.core.params import CoreParams


def cluster(events: list[Event], gap: float) -> list[list[Event]]:
    """Single-linkage over a sorted timeline: start a new cluster when the gap to
    the previous event exceeds `gap`."""
    if not events:
        return []
    ordered = sorted(events, key=lambda e: e.t)
    clusters: list[list[Event]] = [[ordered[0]]]
    for e in ordered[1:]:
        if e.t - clusters[-1][-1].t > gap:
            clusters.append([e])
        else:
            clusters[-1].append(e)
    return clusters


def score_cluster(events: list[Event], params: CoreParams) -> float:
    """base = sum(weight * confidence); n = len; span = last.t - first.t.

    score = base * (1 + cluster_bonus * (n - 1) / (1 + span))
    -> n == 1 gives score == base (no bonus); same n/base, smaller span scores higher.
    """
    base = sum(e.weight * e.confidence for e in events)
    n = len(events)
    if n <= 1:
        return base
    span = max(e.t for e in events) - min(e.t for e in events)
    return base * (1 + params.cluster_bonus * (n - 1) / (1 + span))
