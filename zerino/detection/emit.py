"""Emit detected candidates into the EXISTING marker/clip path (Phase 0.5).

Per DETECTION_DECISIONS.md §6 + §2: for each candidate, write a normal
`kind='gameplay'` marker (timestamp = anchor) and a `detections` row carrying the
event metadata + idempotency keys, then return window dicts with EXPLICIT start/end
so they go straight to ClipService.create_clips — bypassing the fixed-60s
process_single_marker. Reuses the live render path; does NOT modify it, render, or post.
"""
from __future__ import annotations

import json

from zerino.detection.schema import ensure_detections_table


def persist_candidates(
    conn,
    recording_id: int,
    candidates: list,
    *,
    streamer_id,
    source_hash: str,
    detector_version: str,
    profile_version: str,
    game_id: str,
) -> list[dict]:
    """Persist markers + detections rows; return windows for create_clips.

    Returns: [{"marker_id": int, "start": float, "end": float, "kind": "gameplay"}, ...]
    Does NOT create clip rows, render, or post.
    """
    ensure_detections_table(conn)
    windows: list[dict] = []

    for cand in candidates:
        cur = conn.execute(
            "INSERT INTO markers (recording_id, streamer_id, timestamp, kind, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (recording_id, streamer_id, cand.anchor_t, "gameplay", None),
        )
        marker_id = cur.lastrowid

        anchor = max(cand.events, key=lambda e: e.weight) if cand.events else None
        conn.execute(
            "INSERT INTO detections "
            "(recording_id, marker_id, clip_id, t_anchor, win_start, win_end, score, "
            " event_type, source, confidence, weight, meta, game_id, "
            " source_hash, detector_version, profile_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recording_id, marker_id, None, cand.anchor_t, cand.win_start, cand.win_end,
                cand.score,
                anchor.type if anchor else None,
                anchor.source if anchor else None,
                anchor.confidence if anchor else None,
                anchor.weight if anchor else None,
                json.dumps([
                    {"t": e.t, "type": e.type, "source": e.source,
                     "confidence": e.confidence, "weight": e.weight, "meta": e.meta}
                    for e in cand.events
                ]),
                game_id, source_hash, detector_version, profile_version,
            ),
        )

        windows.append({
            "marker_id": marker_id,
            "start": cand.win_start,
            "end": cand.win_end,
            "kind": "gameplay",
        })

    conn.commit()
    return windows
