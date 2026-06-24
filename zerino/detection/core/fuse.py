"""Stage 1 — fuse: merge events from all sources onto one timeline (Phase 1)."""
from __future__ import annotations

from zerino.detection.events import Event


def fuse(events: list[Event]) -> list[Event]:
    """Concatenate events from all sources and sort by t ascending (stable)."""
    return sorted(events, key=lambda e: e.t)
