"""CoreParams — the math knobs the core needs (a decoupled subset of GameProfile)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CoreParams:
    pre: float            # PRE padding (s); PRE > POST -> climax at pre/(pre+post)
    post: float           # POST padding (s)
    cluster_gap: float    # events within this gap (s) join one cluster
    cluster_bonus: float  # strength of the density bonus
    score_threshold: float
    clip_budget: int
    min_dur: float
    max_dur: float
