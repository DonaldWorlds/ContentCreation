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


def _optional_int(value) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value) -> str | None:
    if value in (None, "", "N/A", "unknown"):
        return None
    return str(value)


def has_video_stream(path: str | Path) -> bool:
    """True only if `path` exists and ffprobe finds a real video stream
    (codec_type=video with non-zero width/height).

    Non-raising — ANY problem returns False: missing file, audio-only/empty
    container, zero-dimension stream, corrupt file, or ffprobe error. A
    camera / Elgato Cam Link that drops mid-record leaves a file that pairs
    fine by name or timestamp but has no usable video; the split renderer then
    dies with "No video stream found" and takes the whole batch with it. Use
    this to validate a face/clean-webcam recording before relying on it.
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        raw = _run_ffprobe([
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,width,height",
            "-print_format", "json",
            str(path),
        ])
        data = json.loads(raw.decode("utf-8"))
    except (RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError):
        return False
    streams = data.get("streams", [])
    if not streams:
        return False
    s = streams[0]
    return (
        s.get("codec_type") == "video"
        and bool(_optional_int(s.get("width")))
        and bool(_optional_int(s.get("height")))
    )


def probe_metadata(input_path):
    """Probe a media file with ffprobe; return a metadata dict.

    Returned keys (any may be None if ffprobe didn't report them):

    Video shape
        width, height, fps, duration

    Video format (added by S0.1 — used downstream to convert color when the
    source isn't BT.709, to warn on 10-bit sources we'd quantize to 8-bit,
    and to detect bitrate-starved OBS recordings via S9.1):
        pix_fmt, color_space, color_primaries, color_transfer, color_range,
        video_bit_rate

    Audio (added by S0.1 — used downstream to controlled-downmix surround
    audio, to resample to 48 kHz before the leveler, and to detect weak
    audio encodes):
        audio_codec, audio_sample_rate, audio_channels, audio_channel_layout,
        audio_bit_rate

    Callers in the render path today read only width/height/fps/duration;
    the new keys are additive and backward-compatible.
    """
    raw = _run_ffprobe([
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(input_path),
    ])
    data = json.loads(raw.decode("utf-8"))
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise RuntimeError(f"No video stream found in {input_path}")

    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"),
        None,
    ) or {}

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

    duration = float(fmt["duration"]) if "duration" in fmt else None

    # Stream-level video bitrate is often absent on OBS recordings; fall back
    # to the format-level overall bitrate (less accurate but better than None).
    video_bit_rate = _optional_int(video_stream.get("bit_rate")) or _optional_int(fmt.get("bit_rate"))

    return {
        # existing
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        # video format (S0.1)
        "pix_fmt": _optional_str(video_stream.get("pix_fmt")),
        "color_space": _optional_str(video_stream.get("color_space")),
        "color_primaries": _optional_str(video_stream.get("color_primaries")),
        "color_transfer": _optional_str(video_stream.get("color_transfer")),
        "color_range": _optional_str(video_stream.get("color_range")),
        "video_bit_rate": video_bit_rate,
        # audio (S0.1)
        "audio_codec": _optional_str(audio_stream.get("codec_name")),
        "audio_sample_rate": _optional_int(audio_stream.get("sample_rate")),
        "audio_channels": _optional_int(audio_stream.get("channels")),
        "audio_channel_layout": _optional_str(audio_stream.get("channel_layout")),
        "audio_bit_rate": _optional_int(audio_stream.get("bit_rate")),
    }


if __name__ == "__main__":
    # Quick CLI: `python -m zerino.ffmpeg.ffmpeg_utils <path>` prints the full
    # probe_metadata dict as JSON. Used to verify S0.1 and to spot-check
    # recordings before/after pipeline changes.
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m zerino.ffmpeg.ffmpeg_utils <path-to-mp4>", file=sys.stderr)
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(probe_metadata(path), indent=2))
