"""Regression: detect_recording must query only columns that exist on `recordings`.

The first live test hit `sqlite3.OperationalError: no such column: streamer_id` because the
recordings lookup selected streamer_id (that column lives on `markers`, not `recordings`).
This exercises detect_recording's DB lookup against the REAL schema (conftest init_db).
"""
import types

import zerino.cli.detect as d


def test_detect_recording_reads_real_recordings_schema(tmp_db_conn, recording_id, tmp_path, monkeypatch):
    # the recording_id fixture inserted filename "2026-01-01 00-00-00.mkv" — give it a real file
    src = tmp_path / "2026-01-01 00-00-00.mkv"
    src.write_bytes(b"\x00" * 4096)

    # stub the heavy bits (no ffmpeg/OCR); we only care that the DB lookup + flow run clean
    monkeypatch.setattr(d.MediaHandle, "open",
                        classmethod(lambda cls, p: types.SimpleNamespace(
                            source_path=str(p), face_source_path=None,
                            timebase=types.SimpleNamespace(duration=10.0))))
    monkeypatch.setattr(d.FortniteAdapter, "detect", lambda self, media, profile: [])
    monkeypatch.delenv("ZERINO_DETECTION_AUTORUN", raising=False)
    monkeypatch.delenv("ZERINO_DETECTION_AUTOPOST", raising=False)

    # Before the fix this raised "no such column: streamer_id"; now it resolves cleanly.
    windows = d.detect_recording(recording_id, conn=tmp_db_conn, recordings_dir=tmp_path)
    assert windows == []   # no events -> no candidates -> nothing emitted, autopost OFF -> no post
