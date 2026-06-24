"""Stage 7 — clip-budget shortlist (Phase 1)."""
from __future__ import annotations

from zerino.detection.events import Candidate


def budget(candidates: list[Candidate], clip_budget: int) -> list[Candidate]:
    """Sort by score descending and keep the top `clip_budget` (ranked shortlist)."""
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:clip_budget]
