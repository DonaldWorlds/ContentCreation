import subprocess
import json
from pathlib import Path
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.composition.composition_rules import build_processing_config


class ExportGenerator:

    def build_filter(self, metadata, config):
        target_width = config["canvas_width"]
        target_height = config["canvas_height"]
        mode = config["mode"]
        fps = config.get("fps", 60)
        scaler = config.get("scaler", "lanczos")

        input_width = metadata["width"]
        input_height = metadata["height"]

        if mode == "crop":
            crop_mode = config.get("crop_mode", "center")
            target_ratio = target_width / target_height
            source_ratio = input_width / input_height

            if source_ratio > target_ratio:
                crop_w = int(input_height * target_ratio)
                crop_h = input_height
            else:
                crop_w = input_width
                crop_h = int(input_width / target_ratio)

            if crop_mode == "golden_zone":
                x = max(0, int((input_width - crop_w) * 0.42))
                y = max(0, int((input_height - crop_h) * 0.42))
            else:
                x = max(0, (input_width - crop_w) // 2)
                y = max(0, (input_height - crop_h) // 2)

            return (
                f"crop={crop_w}:{crop_h}:{x}:{y},"
                f"scale={target_width}:{target_height}:flags={scaler},"
                f"setsar=1,fps={fps}"
            )

        if mode == "pad":
            return (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags={scaler},"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,fps={fps}"
            )

        return f"scale={target_width}:{target_height}:flags={scaler},setsar=1,fps={fps}"
    
    def run_export(self, input_path, output_path, platform="tiktok", style="talking_head", subtitles_path=None):
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        metadata = probe_metadata(input_path)

        config = build_processing_config(metadata, platform=platform, style=style)
        vf = self.build_filter(metadata, config)

        if subtitles_path is not None:
            from zerino.processors._captions import subtitles_filter
            vf = f"{vf},{subtitles_filter(Path(subtitles_path))}"

        command = [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", config.get("preset", "slow"),
            "-crf", str(config.get("crf", 20)),
            "-c:a", "aac",
            "-b:a", config.get("audio_bitrate", "192k"),
            "-movflags", "+faststart",
            str(output_path)
        ]

        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(result.stderr)
        return str(output_path)
    
if __name__ == '__main__':
    from zerino.config import CLIPS_DIR, RENDERS_DIR

    generator = ExportGenerator()
    input_path = CLIPS_DIR / "sample_clip.mp4"
    output_path = RENDERS_DIR / "tiktok" / "sample_clip_export.mp4"
    result = generator.run_export(input_path, output_path, platform="tiktok")
    print("RESULT:", result)