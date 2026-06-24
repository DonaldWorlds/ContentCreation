"""CP2 (RED): the detections migration must be additive — create the new table,
leave markers/clips untouched, and be idempotent (Storage decision A1)."""
from zerino.detection.schema import ensure_detections_table


def _table_sql(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def test_creates_detections_table(tmp_db_conn):
    ensure_detections_table(tmp_db_conn)
    assert _table_sql(tmp_db_conn, "detections") is not None


def test_leaves_markers_and_clips_unchanged(tmp_db_conn):
    before = (_table_sql(tmp_db_conn, "markers"), _table_sql(tmp_db_conn, "clips"))
    ensure_detections_table(tmp_db_conn)
    after = (_table_sql(tmp_db_conn, "markers"), _table_sql(tmp_db_conn, "clips"))
    assert after == before


def test_is_idempotent(tmp_db_conn):
    ensure_detections_table(tmp_db_conn)
    ensure_detections_table(tmp_db_conn)  # second run must not raise
