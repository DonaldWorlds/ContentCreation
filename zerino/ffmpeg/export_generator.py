import os
import subprocess
import json
import logging
from pathlib import Path
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.composition.composition_rules import build_processing_config

_log = logging.getLogger("zerino.ffmpeg.export_generator")

# --- Watermark / brand overlay --------------------------------------------- #
# A single PNG composited on top of every render. Position varies by layout:
#   vertical / square  -> bottom-center with WATERMARK_BOTTOM_MARGIN px above
#                         the bottom edge
#   split              -> middle, with the watermark's BOTTOM touching the
#                         seam between face half and gameplay half. Sits in
#                         the face half just above the seam — doesn't cover
#                         the face content meaningfully (seam-adjacent) and
#                         doesn't collide with captions in the gameplay half.
#
# Drop the PNG at WATERMARK_PATH. If missing, the render still completes;
# a WARN is logged and the watermark step is skipped.
# Set env ZERINO_WATERMARK_ENABLED=0 to disable site-wide.
WATERMARK_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "overlays"
    / "kick_social_overlay.png"
)
WATERMARK_ENABLED = os.getenv("ZERINO_WATERMARK_ENABLED", "1") == "1"
# Bottom safe-margin for vertical / square placements. 80 px on a 1920-tall
# frame is ~4 % of canvas — clear of the platform UI bottom band.
WATERMARK_BOTTOM_MARGIN = 80
# Width of the watermark as a fraction of the output canvas width. 0.35 =
# ~35 % wide on a 1080-wide canvas (~378 px). Sized to read clearly at
# TikTok / IG feed thumbnail scale without dominating the frame. Height
# auto-scales to preserve aspect ratio (the source PNG's intrinsic aspect
# ratio is preserved — for the 678×63 banner that's 378×35 at this width).
WATERMARK_WIDTH_FRACTION = 0.35

# --- Audio policy (S6.1, S6.2, S6.3) ---------------------------------------- #
# Static speech leveler. Single-pass `loudnorm` was the prior chain and is
# famously a *dynamic* envelope follower — its I/TP/LRA values are TARGETS
# it chases, not values it achieves, so on real clips it pumped through
# silences and squashed plosives. The static chain below is rate-agnostic
# and deterministic:
#   highpass=f=80     — kills sub-bass rumble (mic stand thumps, AC, room hum)
#   dynaudnorm=f=200  — ~4 s window at 48 k. Long enough to read whole
#                       phrases as a single "loudness unit" so it doesn't
#                       breathe per-syllable.
#                       g=15 frames smoothing; p=0.95 pushes the average
#                       peak to 95 % of full scale. Empirically lands
#                       integrated loudness near -14 LUFS on talking-head
#                       speech (TikTok / IG target). Previous p=0.7 was
#                       3 dB too quiet on real clips.
#   alimiter=limit=0.95 — sample peak ceiling at -0.4 dB. Empirically
#                         tested: 0.95 lands integrated near -14.6 LUFS
#                         (close to TikTok's -14 target) with true peaks
#                         around 0 dBTP. Tightening to 0.85 paradoxically
#                         raised the integrated to -13.8 (dynaudnorm and
#                         the limiter don't share state — tighter limit
#                         meant dynaudnorm's peak target landed lower
#                         which the encoder compensated for). 0.95 is the
#                         empirical sweet spot. TikTok's own re-encoder
#                         pass will catch any residual inter-sample peaks.
SPEECH_LEVELER = "highpass=f=80,dynaudnorm=f=200:g=15:p=0.95,alimiter=limit=0.95"

# Fade in BEFORE the leveler so the leveler doesn't have to "warm up" on
# a hard-cut audio edge — its envelope state at sample 0 is then the
# steady-state of the fade-in's brief ramp instead of "I just hit a wall
# of audio." 50 ms is plenty to kill the click without being audible.
AUDIO_FADE_IN_SEC = 0.05
AUDIO_FADE_OUT_SEC = 0.20

# --- Encode quality settings (S6.16) ---------------------------------------- #
# CRF / CQ mode replaces the prior ABR target. ABR=15M was averaging far
# under target on real face-cam clips (probe of renders/tiktok showed
# ~3.85 Mbps actual against a 15M target — x264 spent the saved bits never
# because there were no subsequent high-motion shots to dump them into).
# Quality-target mode lets the encoder use bits where the content needs
# them and never overspend on easy frames. We still cap via -maxrate to
# avoid handing TikTok a 25 Mbps file it would just re-encode anyway.
#
# Caps pick: TikTok re-encodes above ~10-12 Mbps. 12M maxrate is the
# headroom upper bound; the actual average lands around 6-9 Mbps on
# typical talking-head content. bufsize 2x maxrate per ffmpeg guidance.
LIBX264_CRF = "18"          # "visually lossless" — common professional default
NVENC_CQ = "20"             # ~equivalent to libx264 CRF 18
MAX_BITRATE = "12M"
BUFFER_SIZE = "24M"

# Split layouts get slightly higher maxrate because the face half does
# a 1.78x upscale and benefits from the extra headroom. CRF/CQ themselves
# stay the same — quality target is per-content, not per-layout.
SPLIT_MAX_BITRATE = "14M"
SPLIT_BUFFER_SIZE = "28M"

# preset=slow gives better compression efficiency than medium at the same
# CRF (smaller artifacts for the same quality). 60s short-form clips
# re-encode in seconds on Apple Silicon; the speed cost is invisible.
ENCODE_PRESET = "slow"

# Force broadly-compatible 4:2:0 8-bit — required by most short-form
# platforms; explicit so a future encoder change can't silently switch
# to 4:2:2 / 10-bit and break upload.
PIX_FMT = "yuv420p"

# AAC bitrate target for native AAC fallback (Windows / Linux). Mac's
# aac_at uses VBR via -q:a instead.
AUDIO_BITRATE = "256k"

# --- Color metadata tags (S6.8) --------------------------------------------- #
# Write explicit BT.709 tags so Android / web / Roku players don't guess
# BT.601 and tint playback. Zero perf cost; metadata-only.
COLOR_TAG_ARGS = [
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-colorspace", "bt709",
    "-color_range", "tv",
]

# --- Split-layout filter constants (S6.5) ----------------------------------- #
# Softened denoise. Was 1.5/1.5/6/6, which over-smoothed skin across frames
# (Snapchat-skin look). 1.0/1.0/3/3 keeps webcam grain texture and only
# cleans the high-entropy random noise that wastes x264's bit budget.
SPLIT_FACE_DENOISE = "hqdn3d=1.0:1.0:3:3"

# (SPLIT_FACE_UNSHARP removed — was sharpening the lanczos upscale's edge
# ringing into visible halos. Bicubic upscale (S6.15) doesn't ring; no
# sharpener needed.)

# Split renders force libx264 — NVENC is competitive on most content but
# libx264 -preset slow is materially cleaner on the face quadrant's
# upscaled skin tones. Worth ~5x encode time vs NVENC for the quality.
# -tune film was removed (S6.5): it told x264 to preserve grain, but
# hqdn3d had just removed that grain — the two were fighting.
SPLIT_VIDEO_ENCODER = "libx264"
SPLIT_VIDEO_ENCODER_ARGS = [
    "-preset", "slow",
    "-profile:v", "high",
    "-level", "4.2",
    "-crf", LIBX264_CRF,
    # aq-mode 3 (auto-variance) + strength 1.0 distributes bits more evenly
    # across the face/game halves.
    "-x264-params", "aq-mode=3:aq-strength=1.0",
]


# --- Encoder detection (S6.3, S6.6) ----------------------------------------- #
# Probe ffmpeg's built-in encoder lists at import time and cache.
# Video encoder priority (after S6.6):
#   1. h264_nvenc (Windows / Linux w/ NVIDIA GPU) — genuinely faster than
#      libx264 -slow at comparable quality.
#   2. libx264 (always) — CPU fallback. On Apple Silicon this runs at
#      4-6x realtime for 60s 1080p clips; the speed-vs-quality trade for
#      VideoToolbox doesn't hold up (VideoToolbox at 12M maxrate produced
#      visible banding on dark gradients + smearing on skin, per testing).
# VideoToolbox is intentionally NOT in the priority chain — kept off entirely.
#
# Audio encoder priority (S6.3):
#   1. aac_at (macOS only — Apple AudioToolbox) — materially cleaner on
#      plosives and sibilants than ffmpeg's native AAC at the same bitrate.
#   2. aac (always) — native ffmpeg encoder. On Windows / Linux we add
#      `-aac_coder twoloop` which is slower but better than the default
#      `fast` coder.

def _probe_available_encoders() -> set[str]:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    found: set[str] = set()
    for line in (out.stdout or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        # Encoder lines start with a 6-char flag column; first char is the
        # type (V=video, A=audio, S=subtitle). We collect both V and A.
        flag = parts[0]
        if flag.startswith("V") or flag.startswith("A"):
            found.add(parts[1])
    return found


_AVAILABLE_ENCODERS = _probe_available_encoders()


def _pick_video_encoder() -> tuple[str, list[str]]:
    """Return (encoder_name, args). NVENC first (Windows / Linux NVIDIA),
    libx264 otherwise. VideoToolbox intentionally skipped — see S6.6.
    """
    if "h264_nvenc" in _AVAILABLE_ENCODERS:
        # NVENC CQ mode: -rc vbr enables variable bitrate WITH a quality
        # target (-cq), then -maxrate / -bufsize cap the upper bound.
        # multipass=fullres gives an extra ~1 dB of quality at minor speed
        # cost; spatial-aq distributes bits within frames for skin/eyes.
        return ("h264_nvenc", [
            "-preset", "p5",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", NVENC_CQ,
            "-multipass", "fullres",
            "-spatial-aq", "1",
        ])
    # CPU fallback — libx264 ships with every reasonable ffmpeg build.
    return ("libx264", [
        "-preset", ENCODE_PRESET,
        "-crf", LIBX264_CRF,
    ])


def _pick_audio_encoder() -> tuple[str, list[str]]:
    """Return (encoder_name, args). aac_at on Mac, native AAC + twoloop
    elsewhere. The twoloop coder is ~3x slower than default `fast` but
    gives noticeably cleaner output at the same bitrate — short-form
    clips re-encode quickly enough that the cost is invisible.
    """
    if "aac_at" in _AVAILABLE_ENCODERS:
        # aac_at in bitrate-target mode lands at a predictable rate. The
        # initial attempt at VBR `-q:a 9` produced ~73 kbps on real talking-
        # head speech (aac_at was conservative on low-entropy content) — way
        # under the comfortable speech band of 160-256 kbps. Bitrate mode
        # at 192k is enough headroom for clean speech + game audio.
        return ("aac_at", ["-b:a", "192k"])
    return ("aac", [
        "-b:a", AUDIO_BITRATE,
        "-aac_coder", "twoloop",
    ])


VIDEO_ENCODER, VIDEO_ENCODER_ARGS = _pick_video_encoder()
AUDIO_ENCODER, AUDIO_ENCODER_ARGS = _pick_audio_encoder()
_log.info("video encoder selected: %s", VIDEO_ENCODER)
_log.info("audio encoder selected: %s", AUDIO_ENCODER)


# --- Source normalization (S0.2) --------------------------------------------
# The creative chain (eq, crop, scale, leveler) assumes 8-bit BT.709 yuv420p
# input. Sources we accept can deviate:
#   - OBS HEVC recordings can land 10-bit (`yuv420p10le`); without an explicit
#     downconvert the encoder quantizes to 8-bit somewhere in the middle of
#     the chain with no dither, banding the gradient frames.
#   - Some capture cards default to BT.601; eq's contrast curve then operates
#     on the wrong gamma and the final output (which we tag BT.709 in S6.8)
#     reads as slightly green-shifted on the viewer's device.
#
# `_video_normalize_prefix` returns an empty string or a comma-terminated
# chunk to be prepended at the START of the filter chain (before any
# creative filter). Audio sample-rate normalization is NOT a filter-graph
# concern — `loudnorm` resets the rate to its input, so any `aresample`
# upstream is silently undone. The audio output rate is set at the ffmpeg
# command level via `-ar AUDIO_SAMPLE_RATE` instead, which sits outside the
# filter graph and is honored unconditionally.


def _video_normalize_prefix(metadata: dict) -> str:
    """Return the conditional `format=yuv420p,colorspace=...,` prefix for the
    video chain. Empty string when source is already 8-bit BT.709 yuv420p (the
    common case). Only conditions on tags we are SURE about — None / unknown
    falls through untouched because tag-less modern OBS recordings are almost
    always BT.709, and "convert just in case" would alter correct sources.
    """
    parts: list[str] = []
    pix_fmt = metadata.get("pix_fmt")
    if pix_fmt and pix_fmt != "yuv420p":
        parts.append("format=yuv420p")
    primaries = metadata.get("color_primaries")
    space = metadata.get("color_space")
    if (primaries and primaries != "bt709") or (space and space != "bt709"):
        parts.append(
            "colorspace=all=bt709:iall=bt601:ispace=bt601:itrc=bt601:iprimaries=bt601"
        )
    if not parts:
        return ""
    return ",".join(parts) + ","


# Output audio rate. Set unconditionally via `-ar` on every ffmpeg command —
# resampling is a no-op when the source is already 48 kHz and a one-pass
# conversion otherwise. TikTok / IG / Shorts all normalize to 48 kHz on
# upload anyway; matching here saves the platform-side encoder a step.
AUDIO_SAMPLE_RATE = "48000"


def _pick_scaler(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """S6.15: pick scaler kernel based on direction.

    Lanczos is excellent for DOWNSCALE (preserves detail) but introduces
    ringing halos on edges during UPSCALE — those rings get amplified by
    any downstream contrast / sharpen pass and read as "over-processed."
    Bicubic at the same speed is softer on upscale, no ringing.

    Decision is by total pixel area (handles aspect ratio changes correctly).
    """
    return "bicubic" if (dst_w * dst_h) > (src_w * src_h) else "lanczos"


def _target_fps(metadata: dict) -> float:
    """S6.7: match source fps instead of forcing 60. Forcing 60 on a 30 fps
    source duplicates every frame, halving the effective bit budget on
    actual motion. Cap at 60 to avoid emitting 120 fps from oddball sources.
    """
    src_fps = float(metadata.get("fps") or 30.0)
    return min(60.0, src_fps)


def _fps_filter(metadata: dict, target_fps: float) -> str:
    """Return either `,fps=NN` or empty string. We only emit the fps filter
    when the source actually differs from the target (avoids a no-op pass).
    """
    src_fps = float(metadata.get("fps") or 30.0)
    if abs(src_fps - target_fps) <= 0.1:
        return ""
    return f",fps={target_fps:g}"


def _escape_ffmpeg_movie_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg `movie='...'` source filter.
    Same special-char list as the subtitles= filter (see _captions.py).
    Windows backslashes already get converted to forward slashes upstream.
    """
    path_str = str(path).replace("\\", "/")
    for ch in (":", ",", "'", "[", "]", ";"):
        path_str = path_str.replace(ch, "\\" + ch)
    return path_str


def _watermark_overlay_position(layout: str) -> str:
    """Return the `overlay=X:Y` x:y expression for the given layout.
    ffmpeg overlay variables: W = main width, H = main height, w = overlay
    width, h = overlay height.
    """
    if layout == "split":
        # Watermark BOTTOM at H/2 (the seam between face and gameplay
        # halves). Sits in the lower edge of the face half, just above
        # the captions which start at MarginV=1020.
        return "(W-w)/2:H/2-h"
    # vertical / square — bottom-center with safe margin.
    return f"(W-w)/2:H-h-{WATERMARK_BOTTOM_MARGIN}"


def _watermark_graph_pieces(layout: str, canvas_width: int) -> tuple[str, str] | None:
    """Return (movie_node, overlay_position) for splicing watermark into a
    filter graph, or None if watermark is disabled or the file is missing.

    `movie_node` is a filter-graph node that loads + scales the PNG, e.g.:
        movie='/path/wm.png',scale=WIDTH:-1[wm]
    Caller plugs it into their graph and references the `[wm]` label.

    `overlay_position` is the `X:Y` expression for the `overlay` filter.
    """
    if not WATERMARK_ENABLED:
        return None
    if not WATERMARK_PATH.exists():
        _log.warning(
            "watermark missing at %s — render will skip the watermark. "
            "Drop the PNG there (or set ZERINO_WATERMARK_ENABLED=0 to silence).",
            WATERMARK_PATH,
        )
        return None

    path_escaped = _escape_ffmpeg_movie_path(WATERMARK_PATH)
    wm_width = int(canvas_width * WATERMARK_WIDTH_FRACTION)
    # `scale=W:-1` preserves aspect ratio (height auto-computed).
    movie_node = f"movie='{path_escaped}',scale={wm_width}:-1[wm]"
    position = _watermark_overlay_position(layout)
    return (movie_node, position)


class ExportGenerator:

    def build_filter(self, metadata, config):
        """Build the -vf chain for the standard (non-split) renders.

        Chain order: [source normalize] -> [crop / scale / setsar / fps].
        S6.4: the eq=contrast/saturation/gamma filter was removed. The lift
        was being applied on top of an already-saturated webcam source and
        produced orange skin + crushed shadows. Source-preservation-first.
        S6.7: fps target derived from source (no more forced 60).
        S6.15: scaler chosen per direction (bicubic on upscale, lanczos
        on downscale). S6.18: golden_zone crop anchor now actually wires
        through composition_rules.
        """
        target_width = config["canvas_width"]
        target_height = config["canvas_height"]
        mode = config["mode"]

        input_width = metadata["width"]
        input_height = metadata["height"]

        normalize = _video_normalize_prefix(metadata)
        target_fps = _target_fps(metadata)
        fps_clause = _fps_filter(metadata, target_fps)

        if mode == "crop":
            crop_mode = config.get("crop_mode", "center")
            center_bias = float(config.get("center_bias", 0.5))
            target_ratio = target_width / target_height
            source_ratio = input_width / input_height

            if source_ratio > target_ratio:
                crop_w = int(input_height * target_ratio)
                crop_h = input_height
            else:
                crop_w = input_width
                crop_h = int(input_width / target_ratio)

            if crop_mode == "golden_zone":
                # S6.18: the rule-of-thirds-ish bias (typically 0.42)
                # pulls the crop window toward the top-left of the source
                # frame. On a centered-face webcam shot this lands the
                # face higher in the output canvas — closer to the rule
                # of thirds anchor than a dead-center crop. Was dead code
                # before S6.18 because composition_rules wrote `mode`/
                # `crop_anchor` keys instead of `crop_mode`.
                x = max(0, int((input_width - crop_w) * center_bias))
                y = max(0, int((input_height - crop_h) * center_bias))
            else:
                x = max(0, (input_width - crop_w) // 2)
                y = max(0, (input_height - crop_h) // 2)

            scaler = _pick_scaler(crop_w, crop_h, target_width, target_height)
            return (
                f"{normalize}"
                f"crop={crop_w}:{crop_h}:{x}:{y},"
                f"scale={target_width}:{target_height}:flags={scaler},"
                f"setsar=1{fps_clause}"
            )

        if mode == "pad":
            scaler = _pick_scaler(input_width, input_height, target_width, target_height)
            return (
                f"{normalize}"
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease:flags={scaler},"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1{fps_clause}"
            )

        scaler = _pick_scaler(input_width, input_height, target_width, target_height)
        return (
            f"{normalize}"
            f"scale={target_width}:{target_height}:flags={scaler},"
            f"setsar=1{fps_clause}"
        )

    def build_audio_filter(self, duration: float | None) -> str:
        """Audio chain (S6.1 / S6.2):
            afade=t=in (BEFORE leveler — see S6.2 comment in SPEECH_LEVELER)
                -> highpass -> dynaudnorm -> alimiter
                -> afade=t=out

        Sample-rate normalization is NOT a concern of this chain — any
        `aresample` in the filter graph gets reset by downstream filters
        anyway. The output rate is fixed by `-ar AUDIO_SAMPLE_RATE` at
        the ffmpeg command level.
        """
        parts = [
            f"afade=t=in:st=0:d={AUDIO_FADE_IN_SEC}",
            SPEECH_LEVELER,
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

        # Probe the source for w/h/fps + color/audio metadata (S0.1). Override
        # `duration` with the slice duration so audio fade-out timing matches
        # the OUTPUT clip, not the whole source.
        metadata = probe_metadata(source_path)
        metadata["duration"] = duration

        config = build_processing_config(metadata, platform=platform, style=style, layout=layout)
        vf = self.build_filter(metadata, config)

        # Layer order: watermark FIRST, captions LAST. The captions get
        # z-order priority because they're the load-bearing content; the
        # watermark is positioned to avoid the caption region anyway, but
        # if a caption ever drifts into the watermark zone we want the
        # word visible, not the logo.

        # Watermark overlay — wrap the simple comma-chain `vf` into a
        # labeled filter graph that composites the main video with the
        # PNG loaded by the `movie=` source filter.
        wm_pieces = _watermark_graph_pieces(layout, config["canvas_width"])
        if wm_pieces is not None:
            movie_node, position = wm_pieces
            vf = (
                f"{movie_node};"
                f"[0:v]{vf}[base];"
                f"[base][wm]overlay={position}"
            )

        if subtitles_path is not None:
            from zerino.processors._captions import subtitles_filter
            # Captions burned LAST (after watermark) so they sit on top.
            # libass's original_size matches the .ass PlayResX/Y so
            # captions render at the right pixel size.
            vf = (
                f"{vf},"
                f"{subtitles_filter(Path(subtitles_path), play_res_x=config['canvas_width'], play_res_y=config['canvas_height'])}"
            )

        af = self.build_audio_filter(duration)

        command = [
            "ffmpeg",
            "-y", "-nostdin",
            "-ss", f"{start - pre_roll:.3f}",
            "-i", str(source_path),
            "-ss", f"{pre_roll:.3f}",
            "-t", f"{duration:.3f}",
            "-vf", vf,
            "-af", af,
            # S6.6/S6.16 video encoder + rate control. CRF / CQ in
            # VIDEO_ENCODER_ARGS; we cap via -maxrate / -bufsize here.
            "-c:v", VIDEO_ENCODER,
            *VIDEO_ENCODER_ARGS,
            "-maxrate", MAX_BITRATE,
            "-bufsize", BUFFER_SIZE,
            "-pix_fmt", PIX_FMT,
            # S6.8 color metadata — explicit BT.709 tags so non-Apple
            # players don't guess BT.601 and tint playback green.
            *COLOR_TAG_ARGS,
            # S6.3 audio encoder + args (aac_at on Mac, native aac+twoloop
            # elsewhere). Output sample rate is locked to 48 kHz outside
            # the filter graph because filters like loudnorm reset to the
            # input rate.
            "-c:a", AUDIO_ENCODER,
            *AUDIO_ENCODER_ARGS,
            "-ar", AUDIO_SAMPLE_RATE,
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

        # Probe source: S0.2 normalize prefix + S6.7 source-fps match.
        metadata = probe_metadata(source_path)
        normalize = _video_normalize_prefix(metadata)
        target_fps = _target_fps(metadata)
        fps_clause = _fps_filter(metadata, target_fps)

        # S6.15: scaler by direction, per-half. Face is typically an upscale
        # (small webcam quadrant -> 1080xhalf_h), so bicubic. Game is
        # typically a downscale (large game region -> 1080xhalf_h), so
        # lanczos. Computed from actual box dimensions so it adapts to
        # whatever FACE_BOX / GAME_BOX the operator configures.
        face_scaler = _pick_scaler(fw, fh, canvas_width, half_h)
        game_scaler = _pick_scaler(gw, gh, canvas_width, half_h)

        # Face chain (S6.4 eq removed, S6.5 unsharp removed, softened
        # hqdn3d):
        #   [normalize] -> crop -> hqdn3d (softened) -> bicubic upscale
        #   -> final crop -> setsar (+ optional fps).
        # The prior chain stacked eq + unsharp on top of the lanczos
        # upscale, which produced "plastic skin" + edge halos. Bicubic
        # upscale produces no ringing in the first place, so neither
        # post-scale filter is needed.
        face_chain = (
            f"{normalize}crop={fw}:{fh}:{fx}:{fy},"
            f"{SPLIT_FACE_DENOISE},"
            f"scale={canvas_width}:{half_h}:flags={face_scaler}:force_original_aspect_ratio=increase,"
            f"crop={canvas_width}:{half_h},"
            f"setsar=1{fps_clause}"
        )
        # Game chain (downscale): no denoise (would smear textures), no eq.
        game_chain = (
            f"{normalize}crop={gw}:{gh}:{gx}:{gy},"
            f"scale={canvas_width}:{half_h}:flags={game_scaler}:force_original_aspect_ratio=increase,"
            f"crop={canvas_width}:{half_h},"
            f"setsar=1{fps_clause}"
        )

        graph_parts = [
            f"[0:v]split=2[fa][ga]",
            f"[fa]{face_chain}[face]",
            f"[ga]{game_chain}[game]",
            "[face][game]vstack=inputs=2[stacked]",
        ]
        current_label = "stacked"

        # Layer order: watermark FIRST, then subtitles on top. Captions win
        # z-order so a stray caption never gets obscured by the logo.

        # Watermark overlay (split layout: middle of canvas, bottom edge at
        # the seam between face and gameplay halves).
        wm_pieces = _watermark_graph_pieces("split", canvas_width)
        if wm_pieces is not None:
            movie_node, position = wm_pieces
            graph_parts.append(movie_node)
            graph_parts.append(
                f"[{current_label}][wm]overlay={position}[v_wm]"
            )
            current_label = "v_wm"

        if subtitles_path is not None:
            from zerino.processors._captions import subtitles_filter
            sub = subtitles_filter(
                Path(subtitles_path),
                play_res_x=canvas_width, play_res_y=canvas_height,
            )
            graph_parts.append(f"[{current_label}]{sub}[v_subs]")
            current_label = "v_subs"

        video_map = f"[{current_label}]"
        filter_complex = ";".join(graph_parts)
        af = self.build_audio_filter(duration)

        command = [
            "ffmpeg",
            "-y", "-nostdin",
            "-ss", f"{start - pre_roll:.3f}",
            "-i", str(source_path),
            "-ss", f"{pre_roll:.3f}",
            "-t", f"{duration:.3f}",
            "-filter_complex", filter_complex,
            "-map", video_map,
            "-map", "0:a",
            "-af", af,
            # SPLIT forces libx264 with the AQ tuning — face quadrant
            # upscale + skin tones are where encoder efficiency matters
            # most. NVENC is competitive on most content but libx264
            # -slow is materially cleaner here. -tune film was removed
            # (S6.5) because it told x264 to preserve grain that hqdn3d
            # had just removed. CRF lives in SPLIT_VIDEO_ENCODER_ARGS.
            "-c:v", SPLIT_VIDEO_ENCODER,
            *SPLIT_VIDEO_ENCODER_ARGS,
            "-maxrate", SPLIT_MAX_BITRATE,
            "-bufsize", SPLIT_BUFFER_SIZE,
            "-pix_fmt", PIX_FMT,
            *COLOR_TAG_ARGS,
            "-c:a", AUDIO_ENCODER,
            *AUDIO_ENCODER_ARGS,
            "-ar", AUDIO_SAMPLE_RATE,
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

        # Layer order: watermark FIRST, captions LAST so captions win z-order.

        # Watermark overlay (legacy path — treats every render as vertical
        # layout since this function doesn't take a layout arg).
        wm_pieces = _watermark_graph_pieces("vertical", config["canvas_width"])
        if wm_pieces is not None:
            movie_node, position = wm_pieces
            vf = (
                f"{movie_node};"
                f"[0:v]{vf}[base];"
                f"[base][wm]overlay={position}"
            )

        if subtitles_path is not None:
            from zerino.processors._captions import subtitles_filter
            vf = f"{vf},{subtitles_filter(Path(subtitles_path))}"

        af = self.build_audio_filter(metadata.get("duration"))

        command = [
            "ffmpeg",
            "-y", "-nostdin",
            "-i", str(input_path),
            "-vf", vf,
            "-af", af,
            "-c:v", VIDEO_ENCODER,
            *VIDEO_ENCODER_ARGS,
            "-maxrate", MAX_BITRATE,
            "-bufsize", BUFFER_SIZE,
            "-pix_fmt", PIX_FMT,
            *COLOR_TAG_ARGS,
            "-c:a", AUDIO_ENCODER,
            *AUDIO_ENCODER_ARGS,
            "-ar", AUDIO_SAMPLE_RATE,
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