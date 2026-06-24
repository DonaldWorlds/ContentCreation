"""Core orchestrator (Phase 1): fuse -> cluster -> score -> threshold -> window -> dedupe -> budget."""
from __future__ import annotations

from zerino.detection.events import Event, Candidate
from zerino.detection.core.params import CoreParams
from zerino.detection.core.fuse import fuse
from zerino.detection.core.score import cluster, score_cluster
from zerino.detection.core.window import window_candidate
from zerino.detection.core.dedupe import dedupe
from zerino.detection.core.budget import budget


def run(events: list[Event], params: CoreParams, duration: float) -> list[Candidate]:
    """Run the full game-agnostic core over a list of (already identity-filtered) events."""
    clusters = cluster(fuse(events), params.cluster_gap)

    candidates: list[Candidate] = []
    for c in clusters:
        s = score_cluster(c, params)
        if s >= params.score_threshold:
            candidates.append(window_candidate(c, s, params, duration))

    return budget(dedupe(candidates, params), params.clip_budget)
