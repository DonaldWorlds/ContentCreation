import subprocess
import json
import logging
from pathlib import Path
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.composition.composition_rules import build_processing_config

_log = logging.getLogger("zerino.ffmpeg.export_generator")

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

# Light denoise BEFORE the face upscale — webcam sensor noise is high-
# entropy and tricks x264's adaptive quantization into spending bits on
# random grain instead of face features. hqdn3d cleans the noise cheaply
# (it's the fastest ffmpeg denoiser; nlmeans/atadenoise are higher quality
# but 10-50x slower). Tuning: luma_spatial=1.5 chroma_spatial=1.5
# luma_temporal=6 chroma_temporal=6 = gentle, won't oversmooth skin texture.
SPLIT_FACE_DENOISE = "hqdn3d=1.5:1.5:6:6"

# Sharpening pass for the split layout's face half, AFTER the denoise+scale
# chain so we sharpen real detail instead of amplified noise. luma_amount
# dropped 0.8 -> 0.5 because the upstream denoise removes the noise floor
# that the earlier 0.8 was compensating for; 0.5 produces less ringing on
# skin / hair edges.
SPLIT_FACE_UNSHARP = "unsharp=5:5:0.5:5:5:0.0"

# Split renders override the auto-detected hardware encoder and use libx264
# with film-tuned adaptive quantization. The face upscale + skin tones +
# the hard split-stack architecture all benefit from libx264's superior
# per-bit efficiency over VideoToolbox/NVENC at the same bitrate. We trade
# ~5x encode speed for materially cleaner skin/eyes. -tune film treats
# webcam grain as film grain (don't waste bits flattening it); aq-mode=3
# + aq-strength=1.0 distributes bits more uniformly across the frame so
# the face half doesn't get starved by the gameplay half's complexity.
SPLIT_VIDEO_ENCODER = "libx264"
SPLIT_VIDEO_ENCODER_ARGS = [
    "-preset", "slow",
    "-tune", "film",          # psy-rd defaults are already tuned for film grain
    "-profile:v", "high",
    "-level", "4.2",
    # aq-mode 3 (auto-variance) + strength 1.0 distributes bits more evenly
    # across the face/game halves. Colon is the x264-params separator; the
    # values themselves must not contain colons.
    "-x264-params", "aq-mode=3:aq-strength=1.0",
]

# Split layouts get more bitrate headroom than the standard 15M target,
# because the face half is doing a brutal upscale and the encoder needs
# room to preserve skin/eyes without starving gameplay. 20M peak is still
# below TikTok's ~25M re-encode threshold.
SPLIT_TARGET_BITRATE = "20M"
SPLIT_MAX_BITRATE = "25M"
SPLIT_BUFFER_SIZE = "40M"


# --- Hardware encoder detection ---------------------------------------------
# Probe ffmpeg's built-in encoder list at import time and cache the result.
# Order of preference: NVENC (NVIDIA, fastest by far) > VideoToolbox (Mac
# GPU, fast + clean) > libx264 (CPU fallback, slowest but most consistent).
# At 15 Mbps target bitrate the quality delta between any of these is
# imperceptible on 1080p short-form; the throughput delta is 5-20x.

def _probe_available_encoders() -> set[str]:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    # Each encoder line looks like " V..... libx264 ..." — we only need the name.
    found: set[str] = set()
    for line in (out.stdout or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            found.add(parts[1])
    return found


def _pick_video_encoder() -> tuple[str, list[str]]:
    """Return (encoder_name, extra_args) for the fastest h264 encoder available.

    extra_args are encoder-specific flags appended AFTER the universal
    bitrate flags (-b:v / -maxrate / -bufsize) — used to pin preset/quality
    knobs that don't translate across encoders.
    """
    available = _probe_available_encoders()

    if "h264_nvenc" in available:
        # NVENC: preset p5 (slow-ish) gives near-x264-slow quality with a
        # massive speed win. rc=vbr_hq + multipass=fullres lock in quality.
        return ("h264_nvenc", [
            "-preset", "p5",
            "-tune", "hq",
            "-rc", "vbr",
            "-multipass", "fullres",
            "-spatial-aq", "1",
        ])

    if "h264_videotoolbox" in available:
        # VideoToolbox: -q:v 60 (range 0-100) maps to ~CRF 20-ish but we're
        # in bitrate mode anyway; -allow_sw 1 lets it fall back to software
        # if the GPU is busy. No preset concept — speed is fixed.
        return ("h264_videotoolbox", [
            "-allow_sw", "1",
            "-realtime", "0",  # 0 = quality > speed (default on Apple Silicon)
        ])

    # CPU fallback — libx264 is always present in any reasonable ffmpeg build.
    return ("libx264", [
        "-preset", ENCODE_PRESET,
    ])


VIDEO_ENCODER, VIDEO_ENCODER_ARGS = _pick_video_encoder()
_log.info("video encoder selected: %s", VIDEO_ENCODER)


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
            "-c:v", VIDEO_ENCODER,
            *VIDEO_ENCODER_ARGS,
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

        # Face chain (forensic-tuned filter order):
        #   crop → DENOISE before upscale (so we don't amplify noise) →
        #   scale (lanczos, aspect-fit) → final crop → EQ AT OUTPUT RES
        #   (banding from contrast/gamma bump doesn't get magnified by the
        #   3.5x upscale anymore) → unsharp (mild, 0.5) → setsar/fps.
        #
        # Game chain (no denoise — it's a downscale, denoise would oversmooth
        # textures): same order, eq moved to AFTER scale for consistency.
        face_chain = (
            f"crop={fw}:{fh}:{fx}:{fy},"
            f"{SPLIT_FACE_DENOISE},"
            f"scale={canvas_width}:{half_h}:flags={scaler}:force_original_aspect_ratio=increase,"
            f"crop={canvas_width}:{half_h},"
            f"{COLOR_FILTER},"
            f"{SPLIT_FACE_UNSHARP},"
            f"setsar=1,fps={fps}"
        )
        game_chain = (
            f"crop={gw}:{gh}:{gx}:{gy},"
            f"scale={canvas_width}:{half_h}:flags={scaler}:force_original_aspect_ratio=increase,"
            f"crop={canvas_width}:{half_h},"
            f"{COLOR_FILTER},"
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
            # SPLIT-SPECIFIC ENCODER: libx264 + tune film + AQ tuning gives
            # materially better skin-tone preservation than VideoToolbox /
            # NVENC at the same bitrate. We force it here (overriding the
            # auto-detected HW encoder) because face upscale + skin = where
            # encoder efficiency matters most.
            "-c:v", SPLIT_VIDEO_ENCODER,
            *SPLIT_VIDEO_ENCODER_ARGS,
            "-b:v", SPLIT_TARGET_BITRATE,
            "-maxrate", SPLIT_MAX_BITRATE,
            "-bufsize", SPLIT_BUFFER_SIZE,
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
            "-c:v", VIDEO_ENCODER,
            *VIDEO_ENCODER_ARGS,
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