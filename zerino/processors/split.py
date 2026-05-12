"""Split (9:16) processor: face on top + gameplay on bottom, vstacked.

Used for F9 (gameplay) markers — when the streamer is playing a game with
their facecam overlaid on the gameplay scene in OBS, this composes both
into one 1080x1920 clip:

    +----------------+
    |   facecam      |  1080 x 960   (top half — face only, source-cropped)
    +----------------+
    |   gameplay     |  1080 x 960   (bottom half — game region, facecam-free)
    +----------------+
                       captions land just below the seam (over gameplay)

Facecam coords default to the bottom-left of a 1920x1080 OBS recording with
a 480x270 facecam overlay (FACE_BOX). Gameplay is cropped from the right
1440x1080 of the frame (GAME_BOX) so the facecam overlay is excluded.

If your OBS scene puts the facecam elsewhere, edit FACE_BOX / GAME_BOX
below. A future iteration can read these from per-account / per-streamer
config; for v1 they live as module constants.
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

SPLIT_PLATFORMS = ("tiktok", "youtube_shorts", "facebook_reels", "twitter")

LAYOUT_NAME = "split"
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
HALF_HEIGHT = CANVAS_HEIGHT // 2  # 960

# Source crop boxes — (x, y, w, h) on the original 1920x1080 recording.
# FACE_BOX: bottom-left 480x270 overlay (OBS default facecam position the
# user confirmed). GAME_BOX: the right 1440x1080 area, which excludes the
# facecam region entirely.
FACE_BOX = (0, 810, 480, 270)
GAME_BOX = (480, 0, 1440, 1080)

# Captions just below the seam (y=960) — over the top of the gameplay half
# where they're readable without covering the face. Alignment=8 (top-center)
# measures MarginV from the top of the canvas.
CAPTION_MARGIN_V = 1020


class SplitProcessor(Processor):
    posting_type = "split"

    def __init__(self):
        self.log = get_logger("zerino.processors.split")
        self.generator = ExportGenerator()

    def process_clip_job(
        self,
        job: ClipJob,
        platform: str,
        output_dir: Path | str,
    ) -> ProcessorResult:
        source_path = job.source_path
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        platform = platform.lower()
        if platform not in SPLIT_PLATFORMS:
            raise ValueError(
                f"SplitProcessor does not support platform={platform!r}. "
                f"Supported: {SPLIT_PLATFORMS}"
            )

        # Layout-keyed filename — same render serves every platform on this
        # layout, output_dir is `renders/split/`, no per-platform suffix.
        base_name = f"{source_path.stem}_clip_{int(job.start)}_{int(job.end)}__split"
        output_path = output_dir / f"{base_name}.mp4"
        ass_path = output_dir / f"{base_name}.ass"

        self.log.info(
            "split render start: clip_id=%s src=%s [%.2fs-%.2fs] -> %s (platform=%s)",
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
                play_res_x=CANVAS_WIDTH, play_res_y=CANVAS_HEIGHT,
                margin_v=CAPTION_MARGIN_V,
            )
        else:
            chunk_count = transcribe_source_slice(
                source_path, ass_path, job.start, job.end,
                play_res_x=CANVAS_WIDTH, play_res_y=CANVAS_HEIGHT,
                margin_v=CAPTION_MARGIN_V,
            )

        sidecars: dict[str, Path] = {}
        if has_subtitles_filter():
            self.log.info("burning captions into video (libass available)")
            self.generator.run_split_export_from_source(
                str(source_path), str(output_path), job.start, job.end,
                face_box=FACE_BOX, game_box=GAME_BOX,
                canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT,
                platform=platform,
                subtitles_path=str(ass_path),
                margin_v_for_subs=CAPTION_MARGIN_V,
            )
            if owns_ass:
                ass_path.unlink(missing_ok=True)
        else:
            self.log.warning(
                "libass missing — rendering without burned-in captions; "
                "ASS sidecar kept. Install an ffmpeg build with libass."
            )
            self.generator.run_split_export_from_source(
                str(source_path), str(output_path), job.start, job.end,
                face_box=FACE_BOX, game_box=GAME_BOX,
                canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT,
                platform=platform,
            )
            sidecars["ass"] = ass_path

        self.log.info("split render done: clip_id=%s -> %s", job.clip_id, output_path)
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
                "face_box": FACE_BOX,
                "game_box": GAME_BOX,
            },
        )
