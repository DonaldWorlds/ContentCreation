"""Core data contracts for the detection layer (CP1-approved, PROJECT_REVIEW.md §E).

Pure data — no logic. The game-agnostic core consumes/produces these; adapters
emit `Event`s already identity-filtered to the operator (Decision 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    t: float            # source-relative seconds (from the timebase util, never frame_idx/avg_fps)
    type: str           # adapter-defined: "KILL", "MULTI_ELIM", "MADE_SHOT_3PT", "DUNK", ...
    source: str         # detector id: "ocr_killfeed", "audio_onset", "scoreboard_delta", ...
    confidence: float   # 0..1
    weight: float       # base rarity/value of this event type
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    anchor_t: float
    win_start: float
    win_end: float
    score: float
    events: tuple = ()  # the cluster behind this window
