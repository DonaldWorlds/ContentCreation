"""Horizontal (16:9) processor for long-form video: YouTube, Twitter.

Whisper transcription is mandatory for this posting type:
- YouTube: SRT sidecar (caption=youtube_srt mode)
- Twitter: burned-in subtitles (caption=burned mode) — REQUIRES ffmpeg with libass

Whisper model: `small` (~500MB), per locked v1 decisions.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from zerino.composition.composition_rules import build_processing_config
from zerino.config import get_logger
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.processors.base import Processor, ProcessorResult

HORIZONTAL_PLATFORMS = ("youtube", "twitter")
WHISPER_MODEL_SIZE = "small"


@dataclass
class _Segment:
    start: float
    end: float
    text: str


def _format_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: Iterable[_Segment]) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def _ffmpeg_has_subtitles_filter() -> bool:
    """Probe ffmpeg for the libass-backed `subtitles` filter."""
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


class HorizontalProcessor(Processor):
    posting_type = "horizontal"

    def __init__(self):
        self.log = get_logger("zerino.processors.horizontal")
        self._whisper_model = None  # lazy-load on first use

    def _get_whisper(self):
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            self.log.info("loading faster-whisper model size=%s (first run downloads ~500MB)", WHISPER_MODEL_SIZE)
            self._whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        return self._whisper_model

    def transcribe(self, input_path: Path) -> list[_Segment]:
        model = self._get_whisper()
        self.log.info("transcribing %s", input_path.name)
        segments_iter, info = model.transcribe(str(input_path), beam_size=5)
        out: list[_Segment] = []
        for seg in segments_iter:
            out.append(_Segment(start=seg.start, end=seg.end, text=seg.text))
        self.log.info("transcribed %d segments (lang=%s)", len(out), info.language)
        return out

    def _render_video(self, input_path: Path, output_path: Path, platform: str, burn_subs_from: Path | None = None) -> None:
        metadata = probe_metadata(input_path)
        config = build_processing_config(metadata, platform=platform, style="default")

        target_w = config["canvas_width"]
        target_h = config["canvas_height"]
        scaler = "lanczos"
        fps = 60

        # 16:9 fit-to-canvas with padding (preserves source framing — no aggressive crop on long-form)
        vf_chain = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags={scaler},"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,fps={fps}"
        )

        if burn_subs_from is not None:
            # libass `subtitles=` filter; path must be escaped for ffmpeg's filter syntax
            srt_escaped = str(burn_subs_from).replace(":", r"\:").replace(",", r"\,").replace("'", r"\'")
            vf_chain = f"{vf_chain},subtitles='{srt_escaped}'"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf_chain,
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        self.log.debug("ffmpeg cmd: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")

    def process(self, input_path: Path | str, platform: str, output_dir: Path | str) -> ProcessorResult:
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        platform = platform.lower()
        if platform not in HORIZONTAL_PLATFORMS:
            raise ValueError(
                f"HorizontalProcessor does not support platform={platform!r}. "
                f"Supported: {HORIZONTAL_PLATFORMS}"
            )

        output_path = output_dir / f"{input_path.stem}__{platform}.mp4"
        srt_path = output_dir / f"{input_path.stem}__{platform}.srt"

        self.log.info("horizontal render start: %s -> %s (platform=%s)", input_path.name, output_path.name, platform)

        segments = self.transcribe(input_path)
        srt_text = _segments_to_srt(segments)
        srt_path.write_text(srt_text, encoding="utf-8")
        self.log.info("wrote SRT sidecar: %s", srt_path)

        sidecars: dict[str, Path] = {}

        if platform == "youtube":
            # SRT sidecar; no burning
            self._render_video(input_path, output_path, platform=platform, burn_subs_from=None)
            sidecars["srt"] = srt_path
        elif platform == "twitter":
            if not _ffmpeg_has_subtitles_filter():
                raise RuntimeError(
                    "Twitter requires burned-in subtitles, but the installed ffmpeg has no `subtitles` "
                    "filter (libass missing). Run `brew reinstall ffmpeg` and try again."
                )
            self._render_video(input_path, output_path, platform=platform, burn_subs_from=srt_path)
            # SRT was used to burn subs; drop it from sidecars (Twitter doesn't need a separate file)
            srt_path.unlink(missing_ok=True)

        self.log.info("horizontal render done: %s", output_path)
        return ProcessorResult(
            output_path=output_path,
            sidecars=sidecars,
            metadata={"platform": platform, "segment_count": len(segments)},
        )
