from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zerino.config import get_logger

log = get_logger("zerino.ffmpeg.utils")


def _run_ffprobe(args: list[str]) -> bytes:
    """Run ffprobe with the given args; raise RuntimeError on any failure
    with a clear message instead of letting FileNotFoundError leak out."""
    try:
        return subprocess.check_output(args, stderr=subprocess.PIPE, timeout=30)
    except FileNotFoundError as e:
        raise RuntimeError(
            "ffprobe is not on PATH. Install ffmpeg (which ships ffprobe) "
            "and ensure it's on PATH."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffprobe timed out after 30s: {' '.join(args)}") from e


def get_video_duration_seconds(video_file: str | Path) -> float | None:
    video_file = Path(video_file)

    if not video_file.exists():
        log.error("video file does not exist: %s", video_file)
        return None

    try:
        raw = _run_ffprobe([
            "ffprobe",
            "-v", "quiet",
            "-show_format",
            "-show_streams",
            "-print_format", "json",
            str(video_file),
        ])
        data = json.loads(raw.decode("utf-8"))
    except subprocess.CalledProcessError as e:
        log.error("ffprobe failed for %s: %s", video_file, e.stderr.decode("utf-8", "replace") if e.stderr else "")
        return None
    except (json.JSONDecodeError, RuntimeError) as e:
        log.error("could not probe %s: %s", video_file, e)
        return None

    if "format" not in data or "duration" not in data["format"]:
        log.error("duration missing from ffprobe output for %s", video_file)
        return None

    return float(data["format"]["duration"])


def probe_metadata(input_path):
    raw = _run_ffprobe([
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(input_path),
    ])
    data = json.loads(raw.decode("utf-8"))
    video_stream = next(
        (s for s in data["streams"] if s.get("codec_type") == "video"),
        None,
    )

    if not video_stream:
        raise RuntimeError(f"No video stream found in {input_path}")

    width = int(video_stream["width"])
    height = int(video_stream["height"])

    if "avg_frame_rate" in video_stream and video_stream["avg_frame_rate"] != "0/0":
        num, den = video_stream["avg_frame_rate"].split("/")
        fps = float(num) / float(den)
    elif "r_frame_rate" in video_stream and video_stream["r_frame_rate"] != "0/0":
        num, den = video_stream["r_frame_rate"].split("/")
        fps = float(num) / float(den)
    else:
        fps = 30.0

    duration = float(data["format"]["duration"]) if "duration" in data["format"] else None

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
    }
