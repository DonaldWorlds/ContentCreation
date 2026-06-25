"""Thin call site wiring the detection flow (Phase 1):

    adapter.detect -> core.run -> emit.persist_candidates -> (optional) ClipService.create_clips

Reuses the existing render+post path via an injected ClipService; never duplicates it.
"""
from __future__ import annotations

from zerino.detection.profile import GameProfile
from zerino.detection.adapters.base import DetectorAdapter
from zerino.detection.core.pipeline import run
from zerino.detection import emit, cache
from zerino.detection.schema import ensure_detections_table


def detect_and_emit(
    adapter: DetectorAdapter,
    profile: GameProfile,
    recording_id: int,
    conn,
    *,
    media,
    duration: float,
    streamer_id,
    source_hash: str,
    render: bool = False,
    clip_service=None,
) -> list[dict]:
    """Run one recording through detect -> core -> emit, optionally rendering via clip_service.

    Idempotent (DETECTION_DECISIONS.md §2): if this (source_hash, detector_version,
    profile_version) was already detected, skip re-emit and return [] — re-runs don't
    duplicate clips (and don't trip the detections UNIQUE index).
    """
    ensure_detections_table(conn)
    if cache.already_detected(conn, source_hash, adapter.detector_version, profile.profile_version):
        return []
    events = adapter.detect(media, profile)
    candidates = run(events, profile.core_params(), duration)
    windows = emit.persist_candidates(
        conn, recording_id, candidates,
        streamer_id=streamer_id, source_hash=source_hash,
        detector_version=adapter.detector_version,
        profile_version=profile.profile_version, game_id=profile.game_id,
    )
    if render and clip_service is not None:
        clip_service.create_clips(recording_id, windows)
    return windows
