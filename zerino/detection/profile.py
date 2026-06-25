"""GameProfile — per-game config (YAML on disk + typed dataclass), and CoreParams source.

DETECTION_DECISIONS.md §3. The full profile carries identity + HUD regions + weights;
`core_params()` projects the math knobs the game-agnostic core needs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zerino.detection.core.params import CoreParams


@dataclass
class GameProfile:
    game_id: str
    profile_version: str
    detector_version: str
    player_identity: dict          # {"gamertag": ...} (Fortnite) | {"team": ...} (2K)
    hud_regions: dict              # name -> {x,y,w,h} fractional (carried; used by adapters)
    event_weights: dict            # rarity table (carried; applied by adapters)
    score_threshold: float
    cluster_gap: float
    cluster_bonus: float
    pre: float
    post: float
    clip_budget: int
    min_dur: float
    max_dur: float
    default_layout: str = "split"

    def core_params(self) -> CoreParams:
        return CoreParams(
            pre=self.pre, post=self.post, cluster_gap=self.cluster_gap,
            cluster_bonus=self.cluster_bonus, score_threshold=self.score_threshold,
            clip_budget=self.clip_budget, min_dur=self.min_dur, max_dur=self.max_dur,
        )


def load_profile(game_id: str, profiles_dir: Path | None = None) -> "GameProfile":
    """Load profiles/{game_id}.yaml into a GameProfile."""
    import yaml  # lazy: keep the Mac daemon / live capture path import-light
    base = Path(profiles_dir) if profiles_dir is not None else Path(__file__).parent / "profiles"
    with open(base / f"{game_id}.yaml") as f:
        data = yaml.safe_load(f)
    return GameProfile(**data)
