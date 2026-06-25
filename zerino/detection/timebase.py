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


def _fps(rate: str) -> float:
    """Parse an ffprobe rational like '60/1' -> 60.0 (0.0 on '0/0')."""
    num, _, den = rate.partition("/")
    try:
        n, d = float(num), float(den or 1.0)
        return n / d if d else 0.0
    except ValueError:
        return 0.0


def probe_timebase(path) -> "Timebase":
    """Probe a real file's timebase via ffprobe.

    CFR (the OBS case, verified on Windows): r_frame_rate == avg_frame_rate -> is_vfr
    False, fps from avg_frame_rate; frame_to_sec uses index/fps anchored to PTS-seconds.
    True VFR (r != avg): read per-frame PTS into frame_pts_sec so times never come from
    index/avg_fps. The ms-rounded PTS of CFR OBS files is NOT treated as VFR (the rates
    match exactly).
    """
    import json
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate,r_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    s = data["streams"][0]
    avg, r = _fps(s.get("avg_frame_rate", "0/0")), _fps(s.get("r_frame_rate", "0/0"))
    duration = float(data["format"]["duration"])
    fps = avg or r
    # differing rates => genuine VFR; equal (or one missing) => CFR
    is_vfr = bool(avg and r and abs(avg - r) > 0.01)

    if is_vfr:
        pts_out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "frame=best_effort_timestamp_time",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
        pts = tuple(float(x) for x in pts_out.stdout.split() if x.strip())
        return Timebase(fps=round(fps, 3), is_vfr=True, duration=duration, frame_pts_sec=pts)

    return Timebase(fps=round(fps, 3), is_vfr=False, duration=duration)
