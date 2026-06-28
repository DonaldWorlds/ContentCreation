"""Stage 5 — cluster-span windowing (Phase 1; multi-kill update).

A detected cluster is a SEQUENCE (e.g. a solo team wipe), not a single moment: the clip must
span the whole fight — [first_event - pre, last_event + post] — so every kill lands in ONE
clip. `anchor_t` stays the climax (highest weight*confidence) for marker metadata + ranking.

For a single-event cluster first == last, so this reduces to the old asymmetric
[anchor - pre, anchor + post] window (climax at pre/(pre+post) ~ 67%) — single highlights are
unchanged. When the span exceeds max_dur we trim the LEAD-IN so the climax + its aftermath
survive (a team wipe ends on the last kill).
"""
from __future__ import annotations

from zerino.detection.events import Event, Candidate
from zerino.detection.core.params import CoreParams


def _salience(e: Event):
    # climax = highest weight*confidence; tie-break on the latest t
    return (e.weight * e.confidence, e.t)


def window_candidate(
    events: list[Event], score: float, params: CoreParams, duration: float
) -> Candidate:
    """Span the cluster: [first-pre, last+post] clamped to [0, duration]; grow to min_dur
    (lead-in first, so the climax stays late) within bounds; cap at max_dur by trimming the
    lead-in (keep the climax). Single-event clusters reduce to the old anchored window."""
    anchor = max(events, key=_salience)
    first = min(e.t for e in events)
    last = max(e.t for e in events)

    start = max(0.0, first - params.pre)
    end = min(duration, last + params.post)

    # Clamping at a media boundary (or a single-moment cluster) can shorten the window below
    # min_dur; grow the lead-in first (keeps the climax late), then the trailing side.
    if end - start < params.min_dur:
        start = max(0.0, end - params.min_dur)
        if end - start < params.min_dur:
            end = min(duration, start + params.min_dur)

    # An over-long fight is capped by trimming the LEAD-IN so the climax + aftermath survive.
    if end - start > params.max_dur:
        start = end - params.max_dur

    return Candidate(
        anchor_t=anchor.t, win_start=start, win_end=end, score=score, events=tuple(events)
    )
