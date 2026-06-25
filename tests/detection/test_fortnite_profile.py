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
    # core math knobs project cleanly + PRE > POST (climax ~67%)
    cp = p.core_params()
    assert cp.pre > cp.post
    assert cp.clip_budget >= 1
