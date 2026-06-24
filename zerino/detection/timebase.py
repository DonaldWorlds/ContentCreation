"""Canonical timebase — map frame index / audio sample → source-relative seconds.

Phase 0.5. VFR-aware: for VFR sources, frame time comes from real per-frame PTS,
NOT `index / avg_fps` (which drifts). The detector's times must land on the same
PTS-seconds timeline the renderer's `-ss` uses.

`probe_timebase` (real-file ffprobe) is intentionally deferred — it's validated
against a real OBS recording on Windows (the pending VFR check).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Timebase:
    fps: float
    is_vfr: bool
    duration: float
    frame_pts_sec: tuple = ()  # per-frame PTS in seconds (VFR); empty for CFR

    def frame_to_sec(self, index: int) -> float:
        if self.is_vfr:
            return self.frame_pts_sec[index]
        return index / self.fps

    def sample_to_sec(self, sample_index: int, sample_rate: int) -> float:
        return sample_index / sample_rate


def probe_timebase(path) -> "Timebase":
    raise NotImplementedError(
        "probe_timebase: implement with ffprobe + validate VFR on a real OBS recording (Windows)"
    )
