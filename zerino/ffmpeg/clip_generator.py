from pathlib import Path
import subprocess

from zerino.ffmpeg.ffmpeg_utils import get_video_duration_seconds

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLIPS_DIR = BASE_DIR / "clips"
RECORDINGS_DIR = BASE_DIR / "recordings"


class ClipGeneratorProcess:
    def generate_clip(self, video_file, start, end):
        if not video_file:
            raise ValueError("video_file is missing")
        input_path = RECORDINGS_DIR / video_file

        print(f"[FFPROBE] PATH: {input_path}")
        print(f"[FFPROBE] EXISTS: {input_path.exists()}")

        if not input_path.exists():
            raise FileNotFoundError(f"Video file not found: {input_path}")

        duration = get_video_duration_seconds(input_path)
        if duration is None:
            raise Exception(f"Could not determine video duration for {video_file}")

        print(f"[FFPROBE] Duration: {duration}s")

        start = max(0, start)
        end = min(end, duration)

        if start >= end:
            raise ValueError(f"Invalid clip range: {start} >= {end}")

        base_name = Path(video_file).stem

        CLIPS_DIR.mkdir(parents=True, exist_ok=True)

        output_path = CLIPS_DIR / f"{base_name}_clip_{int(start)}_{int(end)}.mp4"

        if output_path.exists():
            output_path.unlink()

        print(f"[FFMPEG] Cutting clip: {start}s → {end}s")

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

        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"FFmpeg failed:\n{result.stderr}")

        print(f"[FFMPEG] SUCCESS → {output_path}")

        return str(output_path)