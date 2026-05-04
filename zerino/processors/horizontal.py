"""Horizontal (16:9) processor — Twitter only.

YouTube long-form was dropped per v1 scope: long-form lives in a separate
project. Only Twitter remains as a 16:9 target.

Always generates Whisper captions (model `small`). Burn-or-fallback strategy:
- If ffmpeg has libass: captions are burned in; SRT sidecar removed.
- If not: video rendered without burning, SRT sidecar kept; warns to
  `brew reinstall ffmpeg`. (Previously this path raised — now it produces
  the video so the user has something to upload.)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zerino.composition.composition_rules import build_processing_config
from zerino.config import get_logger
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.processors._captions import (
    has_subtitles_filter,
    subtitles_filter,
    transcribe_to_srt,
)
from zerino.processors.base import Processor, ProcessorResult

HORIZONTAL_PLATFORMS = ("twitter",)


class HorizontalProcessor(Processor):
    posting_type = "horizontal"

    def __init__(self):
        self.log = get_logger("zerino.processors.horizontal")

    def _render_video(self, input_path: Path, output_path: Path, platform: str, burn_subs_from: Path | None) -> None:
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
            vf_chain = f"{vf_chain},{subtitles_filter(burn_subs_from)}"

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

        segment_count = transcribe_to_srt(input_path, srt_path)

        sidecars: dict[str, Path] = {}
        if has_subtitles_filter():
            self.log.info("burning captions into video (libass available)")
            self._render_video(input_path, output_path, platform=platform, burn_subs_from=srt_path)
            srt_path.unlink(missing_ok=True)
        else:
            self.log.warning(
                "libass missing — rendering without burned-in captions; "
                "SRT sidecar kept. Run `brew reinstall ffmpeg` to enable burning."
            )
            self._render_video(input_path, output_path, platform=platform, burn_subs_from=None)
            sidecars["srt"] = srt_path

        self.log.info("horizontal render done: %s", output_path)
        return ProcessorResult(
            output_path=output_path,
            sidecars=sidecars,
            metadata={"platform": platform, "segment_count": segment_count},
        )
