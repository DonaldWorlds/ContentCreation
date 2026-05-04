"""Vertical (9:16) processor for short-form video: TikTok, Reels, Shorts.

Delegates to the existing ExportGenerator which preserves the
quality-critical ffmpeg settings (lanczos scaler, CRF 20, preset slow,
golden_zone center_bias 0.42) — see memory: zerino_quality_critical_code.md.
"""

from __future__ import annotations

from pathlib import Path

from zerino.config import get_logger
from zerino.ffmpeg.export_generator import ExportGenerator
from zerino.processors.base import Processor, ProcessorResult

VERTICAL_PLATFORMS = ("tiktok", "youtube_shorts", "instagram_reels")


class VerticalProcessor(Processor):
    posting_type = "vertical"

    def __init__(self):
        self.log = get_logger("zerino.processors.vertical")
        self.generator = ExportGenerator()

    def process(self, input_path: Path | str, platform: str, output_dir: Path | str) -> ProcessorResult:
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        platform = platform.lower()
        if platform not in VERTICAL_PLATFORMS:
            raise ValueError(
                f"VerticalProcessor does not support platform={platform!r}. "
                f"Supported: {VERTICAL_PLATFORMS}"
            )

        output_path = output_dir / f"{input_path.stem}__{platform}.mp4"

        self.log.info("vertical render start: %s -> %s (platform=%s)", input_path.name, output_path.name, platform)
        self.generator.run_export(str(input_path), str(output_path), platform=platform)
        self.log.info("vertical render done: %s", output_path)

        return ProcessorResult(output_path=output_path, metadata={"platform": platform})
