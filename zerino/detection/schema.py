"""Additive `detections` table migration (Phase 0.5).

Storage decision A1 (DETECTION_DECISIONS.md §2): a NEW `detections` table only —
`markers` and `clips` are never altered. Detected clips reuse a normal
`kind='gameplay'` marker so the existing create_clips/FK path is untouched.
"""
from __future__ import annotations

_DETECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS detections (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id     INTEGER NOT NULL,
    marker_id        INTEGER,
    clip_id          INTEGER,
    t_anchor         REAL NOT NULL,
    win_start        REAL NOT NULL,
    win_end          REAL NOT NULL,
    score            REAL NOT NULL,
    event_type       TEXT,
    source           TEXT,
    confidence       REAL,
    weight           REAL,
    meta             TEXT,
    game_id          TEXT,
    source_hash      TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    profile_version  TEXT NOT NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(recording_id) REFERENCES recordings(id) ON DELETE CASCADE,
    FOREIGN KEY(marker_id)    REFERENCES markers(id)     ON DELETE SET NULL,
    FOREIGN KEY(clip_id)      REFERENCES clips(id)       ON DELETE SET NULL
)
"""


def ensure_detections_table(conn) -> None:
    """Create the `detections` table + indexes if absent. Idempotent. Non-destructive."""
    conn.execute(_DETECTIONS_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_detections_recording ON detections(recording_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_detections_idem "
        "ON detections(source_hash, detector_version, profile_version, t_anchor)"
    )
    conn.commit()
