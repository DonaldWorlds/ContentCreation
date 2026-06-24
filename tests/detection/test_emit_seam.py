"""CP2 (RED): emit must persist a kind='gameplay' marker + a detections row and return
windows with EXPLICIT bounds (bypassing the fixed-60s window) — without rendering/posting."""
from zerino.detection.events import Event, Candidate
from zerino.detection.emit import persist_candidates


def _candidate():
    return Candidate(
        anchor_t=12.0, win_start=4.0, win_end=24.0, score=3.5,
        events=(Event(t=12.0, type="KILL", source="ocr_killfeed",
                      confidence=0.9, weight=1.0),),
    )


def _persist(conn, recording_id):
    return persist_candidates(
        conn, recording_id, [_candidate()],
        streamer_id=None, source_hash="h", detector_version="d1",
        profile_version="p1", game_id="fortnite",
    )


def test_writes_gameplay_marker_at_anchor(tmp_db_conn, recording_id):
    _persist(tmp_db_conn, recording_id)
    row = tmp_db_conn.execute(
        "SELECT kind, timestamp FROM markers WHERE recording_id=?", (recording_id,)
    ).fetchone()
    assert row == ("gameplay", 12.0)


def test_writes_detections_row_with_idempotency_keys(tmp_db_conn, recording_id):
    _persist(tmp_db_conn, recording_id)
    row = tmp_db_conn.execute(
        "SELECT source_hash, detector_version, win_start, win_end FROM detections"
    ).fetchone()
    assert row == ("h", "d1", 4.0, 24.0)


def test_returns_window_with_explicit_bounds_and_gameplay_kind(tmp_db_conn, recording_id):
    windows = _persist(tmp_db_conn, recording_id)
    w = windows[0]
    assert w["start"] == 4.0 and w["end"] == 24.0 and w["kind"] == "gameplay"
    assert "marker_id" in w


def test_does_not_create_clips_or_post(tmp_db_conn, recording_id):
    _persist(tmp_db_conn, recording_id)
    assert tmp_db_conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
