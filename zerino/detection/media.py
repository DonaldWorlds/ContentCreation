"""MediaHandle — ONE shared decode reused for the audio gate + gated OCR.

Hard import boundary: ffmpeg/PIL/numpy imported INSIDE methods, never at module top,
so the Mac side / live daemon import nothing heavy (DETECTION_DECISIONS.md §0).
"""
from __future__ import annotations

from pathlib import Path

from zerino.config import get_logger
from zerino.detection.timebase import Timebase, probe_timebase

log = get_logger("zerino.detection.media")

# A single-frame seek+decode is normally well under 1s; 20s is ~20-40x that. Past it the
# ffmpeg call is treated as STALLED (a bad seek that would otherwise hang the whole pass —
# it once froze detection for an hour), killed, and the frame skipped. Per-frame seeking is
# kept (NOT batch fps decode) because it samples the exact frames OCR reads best: a batch
# `fps` filter sampled different frames and tanked golden recall 1.00 -> 0.40.
FRAME_EXTRACT_TIMEOUT_SEC = 20.0


class MediaHandle:
    """Wraps one recording: resolved face pair + canonical Timebase + lazy decode.

    Built once per recording and passed to DetectorAdapter.detect(); the adapter reuses
    it for both the cheap audio pass and the gated frame OCR (no second full decode).
    """

    def __init__(self, source_path, *, face_source_path=None, timebase: Timebase | None = None,
                 width: int | None = None, height: int | None = None):
        self.source_path = str(source_path)
        self.face_source_path = face_source_path
        self.timebase = timebase
        self.width = width
        self.height = height

    @classmethod
    def open(cls, source_path) -> "MediaHandle":
        """Probe (probe_timebase + dims) and resolve the time-aligned face pair
        (reuse ClipService._find_face_pair, Decision 5; defensive -> None on any error)."""
        import json
        import subprocess

        source_path = Path(source_path)
        tb = probe_timebase(source_path)

        dims = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(source_path)],
            capture_output=True, text=True,
        )
        s = json.loads(dims.stdout)["streams"][0]

        face = None
        try:  # reuse the EXISTING, tested face-pair logic (no new layout code)
            from zerino.capture.services.clip_service import ClipService
            face = ClipService()._find_face_pair(source_path)
        except Exception:
            face = None

        return cls(source_path, face_source_path=face, timebase=tb,
                   width=int(s["width"]), height=int(s["height"]))

    def audio_pcm(self, sr: int = 16000):
        """Decode whole-file mono PCM at `sr`. Returns (np.ndarray float32 in [-1,1], sr)."""
        import subprocess

        import numpy as np

        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", self.source_path,
             "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
            capture_output=True,
        )
        pcm = np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, sr

    def frames_at(self, times, region=None):
        """Yield (t_sec, RGB np.ndarray) for each source-relative time, optionally cropped to
        a fractional region {x,y,w,h}. One ffmpeg input-seek per frame (the exact frames OCR
        reads best); each call is bounded by FRAME_EXTRACT_TIMEOUT_SEC so a stalled seek is
        killed and skipped instead of hanging the whole pass. Never buffers the whole VOD."""
        import io
        import subprocess

        import numpy as np
        from PIL import Image

        vf = []
        if region is not None and self.width and self.height:
            x = int(region["x"] * self.width)
            y = int(region["y"] * self.height)
            w = int(region["w"] * self.width)
            h = int(region["h"] * self.height)
            vf = ["-vf", f"crop={w}:{h}:{x}:{y}"]

        for t in times:
            try:
                out = subprocess.run(
                    ["ffmpeg", "-v", "error", "-ss", f"{float(t):.3f}", "-i", self.source_path,
                     *vf, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                    capture_output=True, timeout=FRAME_EXTRACT_TIMEOUT_SEC,
                )
            except subprocess.TimeoutExpired:
                # ffmpeg stalled on this seek (run() kills it); skip the frame, keep going so
                # one bad spot can't hang the whole pass (the freeze we hit on rec34).
                log.warning("frames_at: ffmpeg stalled >%.0fs at t=%.3f in %s — skipped",
                            FRAME_EXTRACT_TIMEOUT_SEC, float(t), self.source_path)
                continue
            if not out.stdout:
                continue  # no frame at this time (e.g. past EOF) — skip, don't crash
            try:
                img = np.asarray(Image.open(io.BytesIO(out.stdout)).convert("RGB"))
            except Exception:
                continue
            yield (float(t), img)
