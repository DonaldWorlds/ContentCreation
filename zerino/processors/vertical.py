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
from zerino.models import ClipJob
from zerino.processors._captions import (
    has_subtitles_filter,
    transcribe_source_slice,
    transcribe_to_ass,
)
from zerino.processors.base import Processor, ProcessorResult

VERTICAL_PLATFORMS = ("tiktok", "youtube_shorts", "facebook_reels", "twitter")


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

    def process_clip_job(
        self,
        job: ClipJob,
        platform: str,
        output_dir: Path | str,
    ) -> ProcessorResult:
        """One-pass render of a ClipJob for a single platform.

        Audio-slice extraction (fast, audio-only), Whisper transcription on
        the slice (or reuse of `job.transcript_path` if pre-computed), then
        one accurate-seek re-encode pass that produces the final clip with
        captions burned in.
        """
        source_path = job.source_path
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        platform = platform.lower()
        if platform not in VERTICAL_PLATFORMS:
            raise ValueError(
                f"VerticalProcessor does not support platform={platform!r}. "
                f"Supported: {VERTICAL_PLATFORMS}"
            )

        base_name = f"{source_path.stem}_clip_{int(job.start)}_{int(job.end)}"
        output_path = output_dir / f"{base_name}__{platform}.mp4"
        ass_path = output_dir / f"{base_name}__{platform}.ass"

        self.log.info(
            "vertical render start: clip_id=%s src=%s [%.2fs-%.2fs] -> %s (platform=%s)",
            job.clip_id, source_path.name, job.start, job.end, output_path.name, platform,
        )

        chunk_count = -1
        owns_ass = True  # whether we created ass_path and may delete it after burn

        if job.transcript_path is not None and Path(job.transcript_path).exists():
            ass_path = Path(job.transcript_path)
            owns_ass = False
            self.log.info("reusing pre-computed transcript: %s", ass_path)
        else:
            chunk_count = transcribe_source_slice(source_path, ass_path, job.start, job.end)

        sidecars: dict[str, Path] = {}
        if has_subtitles_filter():
            self.log.info("burning captions into video (libass available)")
            self.generator.run_export_from_source(
                str(source_path), str(output_path), job.start, job.end,
                platform=platform, subtitles_path=str(ass_path),
                layout="vertical",
            )
            if owns_ass:
                ass_path.unlink(missing_ok=True)
        else:
            self.log.warning(
                "libass missing — rendering without burned-in captions; "
                "ASS sidecar kept. Install an ffmpeg build with libass."
            )
            self.generator.run_export_from_source(
                str(source_path), str(output_path), job.start, job.end,
                platform=platform, layout="vertical",
            )
            sidecars["ass"] = ass_path

        self.log.info("vertical render done: clip_id=%s -> %s", job.clip_id, output_path)
        return ProcessorResult(
            output_path=output_path,
            sidecars=sidecars,
            metadata={
                "platform": platform,
                "segment_count": chunk_count,
                "clip_id": job.clip_id,
                "start": job.start,
                "end": job.end,
            },
        )
