"""Stage 5 — asymmetric anchored windowing (Phase 1)."""
from __future__ import annotations

from zerino.detection.events import Event, Candidate
from zerino.detection.core.params import CoreParams


def _salience(e: Event):
    # climax = highest weight*confidence; tie-break on the latest t
    return (e.weight * e.confidence, e.t)


def window_candidate(
    events: list[Event], score: float, params: CoreParams, duration: float
) -> Candidate:
    """Anchor on the climax; asymmetric window [anchor-pre, anchor+post] clamped to
    [0, duration]; extend to min_dur within bounds; cap at max_dur."""
    anchor = max(events, key=_salience)

    start = max(0.0, anchor.t - params.pre)
    end = min(duration, anchor.t + params.post)

    # Clamping at a media boundary can shorten the window below min_dur; grow the
    # opposite side back within [0, duration].
    if end - start < params.min_dur:
        end = min(duration, start + params.min_dur)
        if end - start < params.min_dur:
            start = max(0.0, end - params.min_dur)

    if end - start > params.max_dur:
        end = start + params.max_dur

    return Candidate(
        anchor_t=anchor.t, win_start=start, win_end=end, score=score, events=tuple(events)
    )
