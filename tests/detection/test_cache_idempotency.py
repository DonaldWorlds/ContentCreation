"""CP2 (RED): detector-version-aware idempotency (DETECTION_DECISIONS.md §2).

source_hash is stable per file; already_detected() lets DetectionService SKIP re-emit on
(source_hash, detector_version, profile_version) so re-runs don't duplicate (and don't trip
the detections UNIQUE index). Reds on NotImplementedError until CP3.
"""
from zerino.detection.cache import source_hash, already_detected
from zerino.detection.schema import ensure_detections_table


def test_source_hash_is_stable(tmp_path):
    f = tmp_path / "rec.mkv"
    f.write_bytes(b"\x00\x01\x02" * 100000)
    assert source_hash(f) == source_hash(f)


def test_already_detected_false_then_true(tmp_db_conn, recording_id):
    ensure_detections_table(tmp_db_conn)
    assert already_detected(tmp_db_conn, "hash-1", "fortnite-0.1.0", "1") is False
    tmp_db_conn.execute(
        "INSERT INTO detections (recording_id, t_anchor, win_start, win_end, score, "
        "source_hash, detector_version, profile_version) VALUES (?,?,?,?,?,?,?,?)",
        (recording_id, 10.0, 6.0, 18.0, 2.0, "hash-1", "fortnite-0.1.0", "1"),
    )
    tmp_db_conn.commit()
    assert already_detected(tmp_db_conn, "hash-1", "fortnite-0.1.0", "1") is True
    # different detector_version => not yet detected (re-run allowed)
    assert already_detected(tmp_db_conn, "hash-1", "fortnite-0.2.0", "1") is False
