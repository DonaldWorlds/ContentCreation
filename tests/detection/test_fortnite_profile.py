"""CP2 (GREEN by design): the calibrated fortnite.yaml loads + carries the locked values.

Validates the Phase-2 profile deliverable (not feature logic) — gamertag identity with
alias support, both HUD regions (left feed + center banner), recall-weighted event rarity
(MULTI_ELIM >> KILL), and the split default layout (Decision 5).
"""
from zerino.detection.profile import load_profile


def test_fortnite_profile_loads_with_calibrated_values():
    p = load_profile("fortnite")            # real zerino/detection/profiles/fortnite.yaml
    assert p.game_id == "fortnite"
    assert p.default_layout == "split"                       # Decision 5
    assert p.player_identity["gamertag"] == "kkthedon_"      # Decision 1
    assert "aliases" in p.player_identity                    # gamertag changes across VODs
    # both calibrated HUD regions present (fractional 0-1)
    assert {"elim_feed", "elim_banner"} <= set(p.hud_regions)
    for r in p.hud_regions.values():
        assert {"x", "y", "w", "h"} == set(r)
    # recall weighted toward high-value events (DETECTION_DECISIONS §5)
    assert p.event_weights["MULTI_ELIM"] > p.event_weights["KILL"]
    # core math knobs project cleanly. Cluster-span windowing: a multi-kill cluster is ONE
    # clip spanning [first-pre, last+post], so pre/post are lead-in/aftermath pads (NOT a
    # climax offset) and pre>post is no longer required. cluster_gap=24 bundles a solo team
    # wipe; min_dur=30 is the monetization floor; max_dur holds a ~42s 4-kill wipe + pads.
    cp = p.core_params()
    assert cp.pre > 0 and cp.post > 0
    assert cp.cluster_gap == 24.0
    assert cp.min_dur == 30.0
    assert cp.max_dur >= cp.min_dur
    assert cp.clip_budget >= 1
