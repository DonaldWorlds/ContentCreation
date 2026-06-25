"""CP2 (RED): GameProfile -> CoreParams mapping + YAML loader."""
import textwrap

from zerino.detection.profile import GameProfile, load_profile
from zerino.detection.core.params import CoreParams


def _profile(**over):
    base = dict(
        game_id="fortnite", profile_version="1", detector_version="d1",
        player_identity={"gamertag": "X"}, hud_regions={}, event_weights={"KILL": 1.0},
        score_threshold=1.0, cluster_gap=5.0, cluster_bonus=1.0, pre=8.0, post=4.0,
        clip_budget=3, min_dur=8.0, max_dur=45.0,
    )
    base.update(over)
    return GameProfile(**base)


def test_core_params_maps_fields():
    cp = _profile().core_params()
    assert isinstance(cp, CoreParams)
    assert (cp.pre, cp.post, cp.cluster_gap, cp.cluster_bonus, cp.score_threshold,
            cp.clip_budget, cp.min_dur, cp.max_dur) == (8.0, 4.0, 5.0, 1.0, 1.0, 3, 8.0, 45.0)


def test_load_profile_reads_yaml(tmp_path):
    (tmp_path / "fortnite.yaml").write_text(textwrap.dedent("""
        game_id: fortnite
        profile_version: "2"
        detector_version: "d9"
        player_identity: {gamertag: "Neo"}
        hud_regions: {killfeed: {x: 0.7, y: 0.1, w: 0.2, h: 0.2}}
        event_weights: {KILL: 1.0, MULTI_ELIM: 2.5}
        score_threshold: 1.5
        cluster_gap: 6.0
        cluster_bonus: 1.2
        pre: 8.0
        post: 4.0
        clip_budget: 5
        min_dur: 8.0
        max_dur: 45.0
        default_layout: split
    """))
    p = load_profile("fortnite", profiles_dir=tmp_path)
    assert p.game_id == "fortnite"
    assert p.profile_version == "2"
    assert p.score_threshold == 1.5
    assert p.player_identity == {"gamertag": "Neo"}
    assert p.clip_budget == 5
