"""CP2 (RED): the thin call site — adapter -> run -> emit -> (optional) create_clips."""
from zerino.detection.events import Event
from zerino.detection.adapters.base import DetectorAdapter
from zerino.detection.profile import GameProfile
from zerino.detection.service import detect_and_emit


def _profile(**over):
    base = dict(
        game_id="fortnite", profile_version="1", detector_version="d1",
        player_identity={"gamertag": "X"}, hud_regions={}, event_weights={"KILL": 1.0},
        score_threshold=1.0, cluster_gap=5.0, cluster_bonus=1.0, pre=8.0, post=4.0,
        clip_budget=3, min_dur=8.0, max_dur=45.0,
    )
    base.update(over)
    return GameProfile(**base)


class FakeAdapter(DetectorAdapter):
    game_id = "fortnite"
    detector_version = "d1"

    def __init__(self, events):
        self._events = events

    def detect(self, media, profile):
        return self._events


class SpyClipService:
    def __init__(self):
        self.calls = []

    def create_clips(self, recording_id, windows):
        self.calls.append((recording_id, windows))


def _kills(*ts):
    return [Event(t=t, type="KILL", source="ocr", confidence=1.0, weight=1.0) for t in ts]


def test_detect_and_emit_persists_marker_and_detection_and_skips_render(tmp_db_conn, recording_id):
    spy = SpyClipService()
    windows = detect_and_emit(
        FakeAdapter(_kills(0.0, 1.0, 2.0)), _profile(), recording_id, tmp_db_conn,
        media=None, duration=100.0, streamer_id=None, source_hash="h",
        render=False, clip_service=spy,
    )
    assert tmp_db_conn.execute(
        "SELECT kind FROM markers WHERE recording_id=?", (recording_id,)
    ).fetchone()[0] == "gameplay"
    assert tmp_db_conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
    assert len(windows) == 1
    assert spy.calls == []   # render=False -> create_clips NOT called


def test_detect_and_emit_render_true_calls_create_clips(tmp_db_conn, recording_id):
    spy = SpyClipService()
    windows = detect_and_emit(
        FakeAdapter(_kills(0.0, 1.0)), _profile(), recording_id, tmp_db_conn,
        media=None, duration=100.0, streamer_id=None, source_hash="h",
        render=True, clip_service=spy,
    )
    assert len(spy.calls) == 1
    assert spy.calls[0][0] == recording_id
    assert spy.calls[0][1] == windows
