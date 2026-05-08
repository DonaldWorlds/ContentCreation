from __future__ import annotations

import subprocess
from pathlib import Path

from zerino.config import get_logger
from zerino.ffmpeg.ffmpeg_utils import get_video_duration_seconds

log = get_logger("zerino.ffmpeg.clip_generator")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLIPS_DIR = BASE_DIR / "clips"
RECORDINGS_DIR = BASE_DIR / "recordings"


class ClipGeneratorProcess:
    def generate_clip(self, video_file, start, end):
        if not video_file:
            raise ValueError("video_file is missing")
        input_path = RECORDINGS_DIR / video_file

        if not input_path.exists():
            raise FileNotFoundError(f"Video file not found: {input_path}")

        duration = get_video_duration_seconds(input_path)
        if duration is None:
            raise RuntimeError(f"Could not determine video duration for {video_file}")

        log.debug("input=%s duration=%.2fs", input_path, duration)

        start = max(0, start)
        end = min(end, duration)

        if start >= end:
            raise ValueError(f"Invalid clip range: {start} >= {end}")

        base_name = Path(video_file).stem
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = CLIPS_DIR / f"{base_name}_clip_{int(start)}_{int(end)}.mp4"

        if output_path.exists():
            output_path.unlink()

        log.info("cutting clip start=%.2fs end=%.2fs -> %s", start, end, output_path.name)

        # Stream-copy the cut — no re-encode. This avoids the "wonky motion
        # at the start" caused by the encoder warming up its rate control on
        # a tiny isolated chunk. The final quality encode happens in
        # export_generator (slow preset, CRF 20). Cuts snap to the nearest
        # source keyframe; with OBS's typical 2 s GOP that's negligible
        # imprecision and invisible to viewers.
        command = [
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", str(input_path),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-map", "0",
            str(output_path),
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except FileNotFoundError as e:
            # Healthcheck should catch this on startup, but if a user runs the
            # generator directly we still want a clear error.
            raise RuntimeError(
                "ffmpeg is not on PATH. Install it (macOS: `brew install ffmpeg`, "
                "Windows: install + add to PATH) and try again."
            ) from e

        if result.returncode != 0:
            # Clean up half-written output so we don't ship junk on retry.
            output_path.unlink(missing_ok=True)
            log.error("ffmpeg cut failed (rc=%d): %s", result.returncode, result.stderr.strip())
            raise RuntimeError(
                f"ffmpeg cut failed (rc={result.returncode}): {result.stderr.strip()[:500]}"
            )

        log.info("cut OK -> %s", output_path)
        return str(output_path)
