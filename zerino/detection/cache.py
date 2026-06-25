"""Detector-version-aware idempotency (DETECTION_DECISIONS.md §2).

Replaces the float-exact clip_exists for the detection path. Keyed on
(source_hash, detector_version, profile_version); DetectionService checks
already_detected() and SKIPS before emit, so re-runs don't duplicate (and don't trip
the detections UNIQUE index).
"""
from __future__ import annotations


def source_hash(path, *, chunk_bytes: int = 1 << 20) -> str:
    """Stable content hash of a recording: file size + head/middle/tail chunks (so we
    don't read a multi-GB VOD end-to-end, while still being content-sensitive)."""
    import hashlib
    import os

    size = os.path.getsize(path)
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        for pos in (0, max(0, size // 2 - chunk_bytes // 2), max(0, size - chunk_bytes)):
            f.seek(pos)
            h.update(f.read(chunk_bytes))
    return h.hexdigest()


def already_detected(conn, source_hash: str, detector_version: str, profile_version: str) -> bool:
    """True iff detections already exist for this (source_hash, detector_version,
    profile_version) — caller skips re-emit."""
    row = conn.execute(
        "SELECT 1 FROM detections WHERE source_hash=? AND detector_version=? "
        "AND profile_version=? LIMIT 1",
        (source_hash, detector_version, profile_version),
    ).fetchone()
    return row is not None
