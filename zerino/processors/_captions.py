"""Shared captioning helpers used by every video processor.

- Lazy-loads faster-whisper model `small` (per locked v1 decisions).
- Generates SRT files.
- Detects whether the installed ffmpeg supports the libass `subtitles` filter.

Strategy used by callers:
  1. Always run `transcribe_to_srt` and write a sidecar SRT.
  2. If `has_subtitles_filter()` is True: append `subtitles_filter(srt_path)` to
     the ffmpeg -vf chain so captions are burned into the video. Then the
     SRT sidecar can be removed (kept only if the burn-in fails).
  3. If False: render the video without captions and keep the SRT sidecar.
     Log a clear warning telling the user to `brew reinstall ffmpeg`.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

WHISPER_MODEL_SIZE = "small"

_log = logging.getLogger("zerino.processors.captions")
_whisper_model = None  # process-wide cache


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _format_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: Iterable[Segment]) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _log.info("loading faster-whisper model size=%s (first run downloads ~500MB)", WHISPER_MODEL_SIZE)
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model


def transcribe_to_srt(input_path: Path, srt_path: Path) -> int:
    """Transcribe `input_path` and write an SRT file. Returns segment count."""
    model = _get_whisper()
    _log.info("transcribing %s", input_path.name)
    segments_iter, info = model.transcribe(str(input_path), beam_size=5)
    segments = [Segment(start=s.start, end=s.end, text=s.text) for s in segments_iter]
    srt_path.write_text(_segments_to_srt(segments), encoding="utf-8")
    _log.info("wrote SRT (%d segments, lang=%s) -> %s", len(segments), info.language, srt_path)
    return len(segments)


def has_subtitles_filter() -> bool:
    """True if ffmpeg has the libass-backed `subtitles` filter."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "filter=subtitles"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "Unknown filter" not in (result.stdout + result.stderr)


def subtitles_filter(srt_path: Path) -> str:
    """Return the ffmpeg `-vf`-compatible subtitles filter snippet for `srt_path`."""
    escaped = str(srt_path).replace(":", r"\:").replace(",", r"\,").replace("'", r"\'")
    return f"subtitles='{escaped}'"
