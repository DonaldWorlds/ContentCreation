"""Vertical (9:16) processor for short-form video: TikTok, YouTube Shorts, Reels.

Always generates Whisper captions (model `small`) as a pre-styled .ass file
(2-word chunks, top-center, FontSize=26, white bold Arial with 2 px black
outline). Burn-or-fallback:
- libass available → burn the .ass into the video, then delete the file.
- libass missing → render plain video, keep .ass as a sidecar.

Delegates the underlying ffmpeg to ExportGenerator, which preserves the
quality-critical settings (lanczos / CRF 20 / preset slow / golden_zone 0.42).
See memory: zerino_quality_critical_code.md.
"""

from __future__ import annotations

from pathlib import Path

from zerino.config import get_logger
from zerino.ffmpeg.export_generator import ExportGenerator
from zerino.processors._captions import (
    has_subtitles_filter,
    transcribe_to_ass,
)
from zerino.processors.base import Processor, ProcessorResult

VERTICAL_PLATFORMS = ("tiktok", "youtube_shorts", "instagram_reels", "twitter")


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
        ass_path = output_dir / f"{input_path.stem}__{platform}.ass"

        self.log.info("vertical render start: %s -> %s (platform=%s)", input_path.name, output_path.name, platform)

        chunk_count = transcribe_to_ass(input_path, ass_path)

        sidecars: dict[str, Path] = {}
        if has_subtitles_filter():
            self.log.info("burning captions into video (libass available)")
            self.generator.run_export(
                str(input_path), str(output_path),
                platform=platform, subtitles_path=str(ass_path),
            )
            ass_path.unlink(missing_ok=True)
        else:
            self.log.warning(
                "libass missing — rendering without burned-in captions; "
                "ASS sidecar kept. Run `brew reinstall ffmpeg` to enable burning."
            )
            self.generator.run_export(str(input_path), str(output_path), platform=platform)
            sidecars["ass"] = ass_path

        self.log.info("vertical render done: %s", output_path)
        return ProcessorResult(
            output_path=output_path,
            sidecars=sidecars,
            metadata={"platform": platform, "segment_count": chunk_count},
        )
