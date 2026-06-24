"""Stage 6 — dedupe by merging overlapping candidates (Phase 1)."""
from __future__ import annotations

from zerino.detection.events import Candidate
from zerino.detection.core.params import CoreParams
from zerino.detection.core.score import score_cluster


def _overlaps(a: Candidate, b: Candidate) -> bool:
    return a.win_start < b.win_end and b.win_start < a.win_end


def _merge(a: Candidate, b: Candidate, params: CoreParams) -> Candidate:
    events = tuple(a.events) + tuple(b.events)
    anchor = max(events, key=lambda e: (e.weight * e.confidence, e.t))
    start = min(a.win_start, b.win_start)
    end = max(a.win_end, b.win_end)
    if end - start > params.max_dur:
        end = start + params.max_dur
    return Candidate(
        anchor_t=anchor.t, win_start=start, win_end=end,
        score=score_cluster(list(events), params), events=events,
    )


def dedupe(candidates: list[Candidate], params: CoreParams) -> list[Candidate]:
    """Merge candidates whose windows overlap into one (union events, re-anchor on the
    highest-salience event, union window, re-score). Iterate until stable."""
    cands = list(candidates)
    changed = True
    while changed:
        changed = False
        result: list[Candidate] = []
        for c in cands:
            for i, r in enumerate(result):
                if _overlaps(c, r):
                    result[i] = _merge(c, r, params)
                    changed = True
                    break
            else:
                result.append(c)
        cands = result
    return cands
