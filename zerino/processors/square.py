"""Square (1:1) processor for talking-head-style clips on profile accounts
that prefer 1080x1080 over 9:16.

Mirrors VerticalProcessor in flow — audio-slice extraction, Whisper
transcription, one-pass accurate-seek re-encode with captions burned in —
but passes layout='square' so composition_rules picks the 1080x1080
canvas and _captions writes an ASS file with PlayResY=1080 and a
proportionally-placed MarginV.

Lives on the same platform set as VerticalProcessor (tiktok / youtube_shorts /
facebook_reels / twitter). Which renders go to which accounts is decided
by accounts.layout, not by platform — same platform can run both layouts
on different account profiles.
"""

from __future__ import annotations

from pathlib import Path

from zerino.config import get_logger
from zerino.ffmpeg.export_generator import ExportGenerator
from zerino.models import ClipJob
from zerino.processors._captions import (
    has_subtitles_filter,
    transcribe_source_slice,
    write_ass_from_segments,
)
from zerino.processors.base import Processor, ProcessorResult

# Same platform list as VerticalProcessor — layout is per-account, not per-platform.
SQUARE_PLATFORMS = ("tiktok", "youtube_shorts", "facebook_reels", "twitter")

LAYOUT_NAME = "square"
PLAY_RES_X = 1080
PLAY_RES_Y = 1080


class SquareProcessor(Processor):
    posting_type = "square"

    def __init__(self):
        self.log = get_logger("zerino.processors.square")
        self.generator = ExportGenerator()

    def process_clip_job(
        self,
        job: ClipJob,
        platform: str,
        output_dir: Path | str,
    ) -> ProcessorResult:
        """One-pass render of a ClipJob for a single platform, square layout."""
        source_path = job.source_path
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        platform = platform.lower()
        if platform not in SQUARE_PLATFORMS:
            raise ValueError(
                f"SquareProcessor does not support platform={platform!r}. "
                f"Supported: {SQUARE_PLATFORMS}"
            )

        # Layout-keyed filename — same render serves every platform on this
        # layout, output_dir is `renders/square/`, no per-platform suffix.
        base_name = f"{source_path.stem}_clip_{int(job.start)}_{int(job.end)}__square"
        output_path = output_dir / f"{base_name}.mp4"
        ass_path = output_dir / f"{base_name}.ass"

        self.log.info(
            "square render start: clip_id=%s src=%s [%.2fs-%.2fs] -> %s (platform=%s)",
            job.clip_id, source_path.name, job.start, job.end, output_path.name, platform,
        )

        chunk_count = -1
        owns_ass = True

        if job.transcript_path is not None and Path(job.transcript_path).exists():
            ass_path = Path(job.transcript_path)
            owns_ass = False
            self.log.info("reusing pre-computed transcript: %s", ass_path)
        elif "karaoke_segments" in job.metadata:
            # `in`-check, not truthy: empty list = silent clip, still cached.
            chunk_count = write_ass_from_segments(
                job.metadata["karaoke_segments"], ass_path,
                play_res_x=PLAY_RES_X, play_res_y=PLAY_RES_Y,
            )
        else:
            chunk_count = transcribe_source_slice(
                source_path, ass_path, job.start, job.end,
                play_res_x=PLAY_RES_X, play_res_y=PLAY_RES_Y,
            )

        sidecars: dict[str, Path] = {}
        if has_subtitles_filter():
            self.log.info("burning captions into video (libass available)")
            self.generator.run_export_from_source(
                str(source_path), str(output_path), job.start, job.end,
                platform=platform, subtitles_path=str(ass_path),
                layout=LAYOUT_NAME,
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
                platform=platform, layout=LAYOUT_NAME,
            )
            sidecars["ass"] = ass_path

        self.log.info("square render done: clip_id=%s -> %s", job.clip_id, output_path)
        return ProcessorResult(
            output_path=output_path,
            sidecars=sidecars,
            metadata={
                "platform": platform,
                "layout": LAYOUT_NAME,
                "segment_count": chunk_count,
                "clip_id": job.clip_id,
                "start": job.start,
                "end": job.end,
            },
        )
