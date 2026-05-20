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

SPLIT_PLATFORMS = (
    "tiktok", "youtube_shorts", "facebook_reels", "twitter",
    "instagram_reels", "pinterest",
)

LAYOUT_NAME = "split"
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
HALF_HEIGHT = CANVAS_HEIGHT // 2  # 960

# Source crop boxes — (x, y, w, h) on the original 1920x1080 recording.
# THESE MUST MATCH THE OPERATOR'S OBS SCENE. If you move/resize the webcam
# or the game capture in OBS, update these to the exact OBS Edit Transform
# Position (x, y) + Bounding Box Size (w, h) values, or the split clip
# crops the wrong region.
#
# Current values match the operator's live OBS scene:
#   Webcam:       Position X=0, Y=777, Bounding Box 546 x 303 (small
#                 bottom-left overlay; rest of canvas is full-screen game).
#   Game capture: full-screen behind the webcam.
#
# GAME_BOX is the FULL source frame (0,0,1920,1080), NOT the right half.
# The old right-half crop (960..1920) pushed the player + crosshair to the
# edge and cut off the CENTRE of the action. With the full frame, the
# downstream cover-scale (force_original_aspect_ratio=increase + centre
# crop) lands the CENTRE of the gameplay — crosshair, character, kills —
# centred in the 1080x960 bottom panel. A 16:9 source covering a 1.125:1
# panel shows the centre ~63% of the width (far-edge HUD corners trimmed);
# the action stays centred and big. The bottom-left webcam (ends at x=546,
# y=777) is cropped out by the centre crop, so it never bleeds into the
# game half.
#
# FACE QUALITY NOTE: the face region is upscaled to fill the 1080x960 top
# panel. 546x303 (~165k source px) is a ~3.2x linear upscale -> SOFT face.
# If too soft, enlarge the OBS webcam box and bump FACE_BOX to match
# (e.g. 960x540 = ~1.8x upscale = sharper). Tradeoff: a bigger webcam box
# covers more gameplay in the live stream.
FACE_BOX = (0, 777, 546, 303)
GAME_BOX = (0, 0, 1920, 1080)

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

        # Dual-source when a paired face recording is present: compose the
        # clean game (centred, no bleed) + the clean full webcam (sharp).
        # Falls back to the single-source crop (FACE_BOX/GAME_BOX from the
        # one recording) when no face pair — e.g. operator hasn't set up the
        # Source Record plugin yet, or the pairing found no match.
        face_path = job.face_source_path
        dual = face_path is not None and Path(face_path).exists()
        if dual:
            self.log.info("split: dual-source (face=%s)", Path(face_path).name)

        def _render(subs: str | None) -> None:
            if dual:
                self.generator.run_dual_split_export_from_source(
                    str(source_path), str(face_path), str(output_path),
                    job.start, job.end,
                    canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT,
                    platform=platform,
                    subtitles_path=subs,
                    margin_v_for_subs=CAPTION_MARGIN_V,
                )
            else:
                self.generator.run_split_export_from_source(
                    str(source_path), str(output_path), job.start, job.end,
                    face_box=FACE_BOX, game_box=GAME_BOX,
                    canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT,
                    platform=platform,
                    subtitles_path=subs,
                    margin_v_for_subs=CAPTION_MARGIN_V if subs else None,
                )

        sidecars: dict[str, Path] = {}
        if has_subtitles_filter():
            self.log.info("burning captions into video (libass available)")
            _render(str(ass_path))
            # Keep .ass sidecar next to the mp4 — see vertical.py for why.
            if owns_ass:
                sidecars["ass"] = ass_path
        else:
            self.log.warning(
                "libass missing — rendering without burned-in captions; "
                "ASS sidecar kept. Install an ffmpeg build with libass."
            )
            _render(None)
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
