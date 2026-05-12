import subprocess
import json
from pathlib import Path
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.composition.composition_rules import build_processing_config

# --- Quality tuning (Phase 2.5) ---------------------------------------------
# Subtle color/contrast bump — makes flat stream captures pop without looking
# filtered. Applied at the very start of the video filter chain.
COLOR_FILTER = "eq=contrast=1.05:saturation=1.1:gamma=0.97"

# TikTok / Instagram target loudness. Raw stream audio is typically ~-20 LUFS,
# so without normalization clips sound quieter than the feed and get scrolled.
LOUDNORM_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"

AUDIO_FADE_IN_SEC = 0.15   # 150 ms — kills the hard-cut audio pop on entry
AUDIO_FADE_OUT_SEC = 0.20  # 200 ms — gentle tail-out

# --- Encode quality settings -------------------------------------------------
# Bitrate-target VBR (replaces the prior CRF 20 mode that let x264 starve
# gameplay/dark scenes down to ~4 Mbps and look muddy on 1080p60 short-form).
#
# Targets pick: TikTok recommends 8-12 Mbps for 1080p60 and re-encodes
# anything above ~15 Mbps, so 15M is the sweet spot — max quality without
# wasted upload bandwidth. YouTube Shorts wants >=8 Mbps. maxrate 18M gives
# 20% headroom for high-motion bursts; bufsize 30M ~= 2x maxrate per Apple
# guidance for stable rate control.
TARGET_BITRATE = "15M"
MAX_BITRATE = "18M"
BUFFER_SIZE = "30M"

# preset=slow gives better compression efficiency than medium at the same
# bitrate (smaller artifacts for the same data budget). 30s short-form
# clips re-encode in seconds; the speed cost is invisible.
ENCODE_PRESET = "slow"

# Force broadly-compatible 4:2:0 8-bit — required by some Zernio backends
# and most short-form platforms; explicit so a future encoder change can't
# silently switch to 4:2:2/10-bit and break upload.
PIX_FMT = "yuv420p"

# AAC at 256k is well under most platform limits and is the practical max
# before perceptual gains plateau. Bumped from 192k.
AUDIO_BITRATE = "256k"

# Sharpening pass for the split layout's face half: the facecam is upscaled
# from a small source crop (e.g. 480x270 -> 1080x960, ~3.5x zoom per
# dimension), and lanczos upscales look soft because interpolation can't
# manufacture detail. A light luma-only unsharp restores perceived
# sharpness without producing chroma ringing on faces.
SPLIT_FACE_UNSHARP = "unsharp=5:5:0.8:5:5:0.0"


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
                f"{COLOR_FILTER},"
                f"crop={crop_w}:{crop_h}:{x}:{y},"
                f"scale={target_width}:{target_height}:flags={scaler},"
                f"setsar=1,fps={fps}"
            )

        if mode == "pad":
            return (
                f"{COLOR_FILTER},"
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags={scaler},"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,fps={fps}"
            )

        return (
            f"{COLOR_FILTER},"
            f"scale={target_width}:{target_height}:flags={scaler},"
            f"setsar=1,fps={fps}"
        )

    def build_audio_filter(self, duration: float | None) -> str:
        """Audio chain: loudness normalize to TikTok/IG target, then fade
        in 150 ms and fade out 200 ms before EOF.
        """
        parts = [
            LOUDNORM_FILTER,
            f"afade=t=in:st=0:d={AUDIO_FADE_IN_SEC}",
        ]
        if duration is not None and duration > AUDIO_FADE_OUT_SEC + 0.1:
            fade_out_st = max(0.0, duration - AUDIO_FADE_OUT_SEC)
            parts.append(f"afade=t=out:st={fade_out_st:.3f}:d={AUDIO_FADE_OUT_SEC}")
        return ",".join(parts)

    def run_export_from_source(
        self,
        source_path,
        output_path,
        start: float,
        end: float,
        platform: str = "tiktok",
        style: str = "talking_head",
        subtitles_path=None,
        layout: str = "vertical",
    ):
        """One-pass accurate-seek re-encode from a long source recording.

        Replaces the older two-stage (stream-copy cut → re-encode export)
        flow which produced wonky-start motion on Windows. The intermediate
        cut had timestamps the Windows decoder handled poorly; baking
        cut+crop+caption+encode into one ffmpeg invocation from the source
        eliminates that surface entirely.

        Seek pattern: `-ss before -i` for fast keyframe jump, `-ss after -i`
        for accurate decode-to-frame. A short pre-roll keeps the encoder's
        rate control warm before the actual content begins.
        """
        source_path = Path(source_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        if end <= start:
            raise ValueError(f"Invalid range: start={start} end={end}")

        duration = end - start
        pre_roll = min(2.0, start)

        # Probe the source for w/h/fps. Override duration with the slice's
        # duration so audio fade-out timing is correct for the OUTPUT clip.
        metadata = probe_metadata(source_path)
        metadata["duration"] = duration

        config = build_processing_config(metadata, platform=platform, style=style, layout=layout)
        vf = self.build_filter(metadata, config)

        if subtitles_path is not None:
            from zerino.processors._captions import subtitles_filter
            # Burn captions LAST so they aren't tinted by the eq color bump.
            # Pass the canvas size so libass's original_size matches the .ass
            # file's PlayResX/PlayResY (otherwise captions render the wrong size).
            vf = (
                f"{vf},"
                f"{subtitles_filter(Path(subtitles_path), play_res_x=config['canvas_width'], play_res_y=config['canvas_height'])}"
            )

        af = self.build_audio_filter(duration)

        command = [
            "ffmpeg",
            "-y",
            "-ss", f"{start - pre_roll:.3f}",
            "-i", str(source_path),
            "-ss", f"{pre_roll:.3f}",
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264",
            "-preset", ENCODE_PRESET,
            "-b:v", TARGET_BITRATE,
            "-maxrate", MAX_BITRATE,
            "-bufsize", BUFFER_SIZE,
            "-pix_fmt", PIX_FMT,
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise Exception(result.stderr)
        return str(output_path)

    def run_split_export_from_source(
        self,
        source_path,
        output_path,
        start: float,
        end: float,
        face_box: tuple[int, int, int, int],
        game_box: tuple[int, int, int, int],
        canvas_width: int = 1080,
        canvas_height: int = 1920,
        platform: str = "tiktok",
        subtitles_path=None,
        margin_v_for_subs: int | None = None,
    ):
        """One-pass face+gameplay split (vstack) render from a long source.

        `face_box` and `game_box` are (x, y, w, h) crops on the SOURCE frame.
        Each is scaled+center-cropped to fill canvas_width × (canvas_height / 2)
        so the two halves stack flush. Captions (if any) are burned onto the
        composed canvas via filter_complex's final node.

        Audio uses the same loudnorm + fades chain as the standard export.
        """
        source_path = Path(source_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        if end <= start:
            raise ValueError(f"Invalid range: start={start} end={end}")
        if canvas_height % 2 != 0:
            raise ValueError(f"canvas_height must be even for vstack, got {canvas_height}")

        duration = end - start
        pre_roll = min(2.0, start)
        half_h = canvas_height // 2

        fx, fy, fw, fh = face_box
        gx, gy, gw, gh = game_box

        scaler = "lanczos"
        fps = 60

        # Each half: source crop → eq color bump → scale (increase) → center crop → fps.
        # eq is applied BEFORE the final crop so libass-burned subs (added later) aren't tinted.
        # Face chain ends with `unsharp` because the small facecam region gets
        # upscaled ~3x; lanczos interpolation looks soft and unsharp restores
        # perceived edge detail. Game chain skips unsharp — it's a downscale
        # and sharpening would just amplify noise.
        face_chain = (
            f"crop={fw}:{fh}:{fx}:{fy},"
            f"{COLOR_FILTER},"
            f"scale={canvas_width}:{half_h}:flags={scaler}:force_original_aspect_ratio=increase,"
            f"crop={canvas_width}:{half_h},"
            f"{SPLIT_FACE_UNSHARP},"
            f"setsar=1,fps={fps}"
        )
        game_chain = (
            f"crop={gw}:{gh}:{gx}:{gy},"
            f"{COLOR_FILTER},"
            f"scale={canvas_width}:{half_h}:flags={scaler}:force_original_aspect_ratio=increase,"
            f"crop={canvas_width}:{half_h},"
            f"setsar=1,fps={fps}"
        )

        graph_parts = [
            f"[0:v]split=2[fa][ga]",
            f"[fa]{face_chain}[face]",
            f"[ga]{game_chain}[game]",
            "[face][game]vstack=inputs=2[stacked]",
        ]

        if subtitles_path is not None:
            from zerino.processors._captions import subtitles_filter
            sub = subtitles_filter(
                Path(subtitles_path),
                play_res_x=canvas_width, play_res_y=canvas_height,
            )
            graph_parts.append(f"[stacked]{sub}[v]")
            video_map = "[v]"
        else:
            video_map = "[stacked]"

        filter_complex = ";".join(graph_parts)
        af = self.build_audio_filter(duration)

        command = [
            "ffmpeg",
            "-y",
            "-ss", f"{start - pre_roll:.3f}",
            "-i", str(source_path),
            "-ss", f"{pre_roll:.3f}",
            "-t", f"{duration:.3f}",
            "-filter_complex", filter_complex,
            "-map", video_map,
            "-map", "0:a",
            "-af", af,
            "-c:v", "libx264",
            "-preset", ENCODE_PRESET,
            "-b:v", TARGET_BITRATE,
            "-maxrate", MAX_BITRATE,
            "-bufsize", BUFFER_SIZE,
            "-pix_fmt", PIX_FMT,
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise Exception(result.stderr)
        return str(output_path)

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
            # Burn captions LAST so they aren't tinted by the eq color bump.
            vf = f"{vf},{subtitles_filter(Path(subtitles_path))}"

        af = self.build_audio_filter(metadata.get("duration"))

        command = [
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264",
            "-preset", ENCODE_PRESET,
            "-b:v", TARGET_BITRATE,
            "-maxrate", MAX_BITRATE,
            "-bufsize", BUFFER_SIZE,
            "-pix_fmt", PIX_FMT,
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
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