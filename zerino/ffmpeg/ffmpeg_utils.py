import subprocess
import json
from pathlib import Path

def get_video_duration_seconds(video_file: str | Path) -> float | None:

    video_file = Path(video_file)

    #DEBUG (temporary but VERY useful)
    print("FFPROBE PATH:", video_file)
    print("EXISTS:", video_file.exists())

    if not video_file.exists():
        print("ERROR: Video file does not exist")
        return None

    try:
        result = subprocess.check_output([
            "ffprobe",
            "-v", "quiet",
            "-show_format",
            "-show_streams",
            "-print_format", "json",
            str(video_file)
        ])

        data = json.loads(result.decode("utf-8"))

        if "format" not in data or "duration" not in data["format"]:
            print("ERROR: Duration missing from ffprobe output")
            return None
        
        return float(data["format"]["duration"])

    except subprocess.CalledProcessError as e:
        print("FFPROBE FAILED:", e)
        return None

    except Exception as e:
        print("UNKNOWN ERROR:", e)
        return None
    


def probe_metadata(input_path):
    result = subprocess.check_output([
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(input_path)
    ])

    data = json.loads(result.decode("utf-8"))
    video_stream = next(
        (s for s in data["streams"] if s.get("codec_type") == "video"),
        None
    )

    if not video_stream:
        raise Exception(f"No video stream found in {input_path}")

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