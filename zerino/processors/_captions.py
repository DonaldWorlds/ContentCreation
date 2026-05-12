"""Shared captioning helpers used by every video processor.

Strategy:
  1. Whisper transcribes with word-level timing.
  2. Words are grouped into 2-word chunks (TikTok / CapCut style — never wraps).
  3. The chunks are written directly to an .ass file with PlayResX/Y =
     1080x1920 and an explicit Style block. No `force_style` — every
     position, size, and colour is hard-coded into the file the way libass
     reads it. This avoids the SRT-→-ASS conversion + force_style override
     quirks that caused captions to drift left and to scale 6.67× larger
     than intended.
  4. The .ass file is passed to the ffmpeg `subtitles=` filter for burn-in.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

WHISPER_MODEL_SIZE = "small"
WORDS_PER_CHUNK = 3   # how many words sit on screen at once during karaoke

# Caption timing shift. POSITIVE values delay captions (appear later);
# NEGATIVE values advance them. Default 0 = use Whisper's word.start times
# unchanged. Bump to ~0.10 if captions visibly lead your voice after the
# smooth-karaoke rebuild below; lower to negative if they lag.
CAPTION_TIME_OFFSET_SECONDS = 0.0

_log = logging.getLogger("zerino.processors.captions")
_whisper_model = None  # process-wide cache
_libass_available: bool | None = None  # None=untested, True/False=cached probe result


# --------------------------------------------------------------------------- #
# Style — every value lands in the ASS Style: line below.                     #
#                                                                             #
# ASS colour format: &HAABBGGRR (alpha, blue, green, red).                    #
# Alignment numpad layout: 7=top-left   8=top-center   9=top-right            #
#                          4=mid-left   5=mid-center   6=mid-right            #
#                          1=bot-left   2=bot-center   3=bot-right            #
# --------------------------------------------------------------------------- #
PLAY_RES_X = 1080
PLAY_RES_Y = 1920           # default (vertical 9:16). SquareProcessor overrides.
FONT_NAME = "Impact"
FONT_SIZE = 72
BOLD = 1
# Karaoke colours: TEXT_COLOUR is the default (un-highlighted) word color,
# HIGHLIGHT_COLOUR is the current word being spoken. ASS colour format
# &HAABBGGRR — &H0000FFFF = full red + full green = bright YELLOW.
TEXT_COLOUR = "&H00FFFFFF"        # white — un-highlighted words
HIGHLIGHT_COLOUR = "&H0000FFFF"   # bright yellow — current word
OUTLINE_COLOUR = "&H00000000"
BACK_COLOUR = "&H00000000"
BORDER_STYLE = 1
OUTLINE = 5
SHADOW = 0
ALIGNMENT = 8
MARGIN_L = 0
MARGIN_R = 0
MARGIN_V = 780              # ~43 % down a 1920 frame. SquareProcessor passes ~440.

# Per-layout margin-V table. Same proportional placement (~43% down the
# canvas) so the caption sits in the upper third regardless of aspect.
LAYOUT_MARGIN_V = {
    1920: 780,   # vertical 9:16
    1080: 440,   # square 1:1
}

@dataclass
class Segment:
    start: float
    end: float
    text: str


def _format_ass_timestamp(seconds: float) -> str:
    """ASS time format: H:MM:SS.cs (centiseconds, single-digit hour)."""
    cs = max(0, int(round(seconds * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_header(play_res_x: int = PLAY_RES_X, play_res_y: int = PLAY_RES_Y, margin_v: int | None = None) -> str:
    if margin_v is None:
        margin_v = LAYOUT_MARGIN_V.get(play_res_y, MARGIN_V)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n"   # 2 = no wrap; each chunk is a single line
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{FONT_NAME},{FONT_SIZE},"
        f"{TEXT_COLOUR},{TEXT_COLOUR},{OUTLINE_COLOUR},{BACK_COLOUR},"
        f"{BOLD},0,0,0,100,100,0,0,"
        f"{BORDER_STYLE},{OUTLINE},{SHADOW},"
        f"{ALIGNMENT},{MARGIN_L},{MARGIN_R},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _segments_to_ass(
    segments: list[Segment],
    play_res_x: int = PLAY_RES_X,
    play_res_y: int = PLAY_RES_Y,
    margin_v: int | None = None,
) -> str:
    out = [_ass_header(play_res_x=play_res_x, play_res_y=play_res_y, margin_v=margin_v)]
    for seg in segments:
        start = _format_ass_timestamp(seg.start)
        end = _format_ass_timestamp(seg.end)
        # Text is the final field in a Dialogue line, so commas inside it
        # are NOT field separators and must not be escaped. Hard-wrap any
        # newlines (libass would treat \N as a line break otherwise).
        text = seg.text.strip().replace("\n", " ")
        out.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    return "".join(out)


def _detect_whisper_device() -> tuple[str, str]:
    """Pick the fastest faster-whisper backend available on this machine.

    Order:
      1. CUDA (NVIDIA GPU) — float16, ~10x CPU
      2. CPU int8           — fallback, real-time on Apple Silicon

    Apple Silicon MPS isn't supported by faster-whisper / CTranslate2 yet
    (as of late 2025); CoreML support exists in upstream whisper.cpp but
    not faster-whisper. So Mac users still land on CPU int8 for now —
    which is fine: M-series CPUs do small/int8 at near-realtime.
    """
    try:
        # ctranslate2 ships with faster-whisper; the IDE may flag this on
        # machines without the venv activated, but it's always present at
        # runtime when faster-whisper itself is installed.
        from ctranslate2 import get_cuda_device_count
        if get_cuda_device_count() > 0:
            return ("cuda", "float16")
    except Exception:  # noqa: BLE001
        pass
    return ("cpu", "int8")


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        device, compute_type = _detect_whisper_device()
        _log.info(
            "loading faster-whisper size=%s device=%s compute=%s "
            "(first run downloads ~500MB)",
            WHISPER_MODEL_SIZE, device, compute_type,
        )
        try:
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE, device=device, compute_type=compute_type,
            )
        except Exception as e:  # noqa: BLE001
            # CUDA toolkit / cuDNN missing or version-mismatched — fall back
            # rather than dying. Logging the reason helps the user fix it
            # later if they expected GPU.
            _log.warning(
                "whisper init failed on device=%s (%s) — falling back to CPU int8",
                device, e,
            )
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE, device="cpu", compute_type="int8",
            )
    return _whisper_model


def _build_karaoke_segments(words: list) -> list[Segment]:
    """Group `words` into chunks of WORDS_PER_CHUNK and emit one Segment per
    word — each Segment displays the full chunk with the current word
    coloured HIGHLIGHT_COLOUR and the rest TEXT_COLOUR.

    Result: TikTok-signature karaoke — the chunk stays on screen continuously,
    with the highlight moving word-by-word across the chunk.

    CONTINUOUS-SPAN BEHAVIOR (critical for sync):
      Each segment runs from `current.start` to `next_word.start` (or to
      the chunk's last-word `.end` for the final word in the chunk). This
      eliminates the inter-word gaps in the old "current.start..current.end"
      version, where the caption would VANISH between words and pop back in
      at the next word's start time — visually it read as "caption appears
      before I said the word" because the eye sees the pop-in slightly
      before the ear processes the phoneme.
    """
    out: list[Segment] = []
    for i in range(0, len(words), WORDS_PER_CHUNK):
        chunk = words[i:i + WORDS_PER_CHUNK]
        if not chunk:
            continue
        chunk_tail = chunk[-1].end
        for j, current in enumerate(chunk):
            parts: list[str] = []
            for k, w in enumerate(chunk):
                word_text = w.word.strip()
                if not word_text:
                    continue
                if k == j:
                    parts.append(
                        f"{{\\c{HIGHLIGHT_COLOUR}&}}{word_text}{{\\c{TEXT_COLOUR}&}}"
                    )
                else:
                    parts.append(word_text)
            text = " ".join(parts).strip()
            if not text:
                continue
            # Each segment ends where the NEXT segment begins, so the chunk
            # stays painted on screen continuously across the gaps Whisper
            # leaves between words.
            seg_start = current.start + CAPTION_TIME_OFFSET_SECONDS
            if j + 1 < len(chunk):
                seg_end = chunk[j + 1].start + CAPTION_TIME_OFFSET_SECONDS
            else:
                seg_end = chunk_tail + CAPTION_TIME_OFFSET_SECONDS
            # Defensive: Whisper occasionally returns zero/negative-duration
            # words on very short utterances. Ensure end > start so libass
            # actually renders the line.
            if seg_end <= seg_start:
                seg_end = seg_start + 0.05
            out.append(Segment(start=seg_start, end=seg_end, text=text))
    return out


def extract_audio_slice(source_path: Path, slice_path: Path, start: float, end: float) -> None:
    """Extract a short audio-only slice from a long source for transcription.

    Output is WAV PCM 16-bit 16 kHz mono — Whisper's native internal format,
    so faster-whisper skips its decode/resample pass entirely. Lossless from
    the source (no AAC re-encode loss before Whisper sees it), faster than
    AAC encoding, and accuracy on noisy gameplay backgrounds improves
    slightly because the borderline-quiet phonemes aren't smeared by lossy
    pre-compression.

    Two-stage seek: `-ss before -i` jumps to the nearest keyframe (fast),
    `-ss after -i` decodes accurately from there. The resulting file starts
    at the exact requested second of the source.
    """
    if end <= start:
        raise ValueError(f"Invalid range: start={start} end={end}")

    duration = end - start
    pre_roll = min(2.0, start)  # decode-accurate seek context; trimmed below

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start - pre_roll:.3f}",
        "-i", str(source_path),
        "-ss", f"{pre_roll:.3f}",
        "-t", f"{duration:.3f}",
        "-vn",
        "-ac", "1",          # mono
        "-ar", "16000",      # 16 kHz — Whisper's native rate
        "-c:a", "pcm_s16le", # 16-bit PCM — lossless, smaller than 24-bit
        str(slice_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "ffmpeg is not on PATH. Install it and try again."
        ) from e

    if result.returncode != 0:
        slice_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"audio slice extraction failed (rc={result.returncode}): "
            f"{result.stderr.strip()[:500]}"
        )


def transcribe_source_to_segments(
    source_path: Path,
    start: float,
    end: float,
    language: str | None = None,
) -> list[Segment]:
    """Whisper-once entry point: extract audio, transcribe, return karaoke
    segments WITHOUT writing any .ass file.

    Used by Router to run the expensive Whisper pass exactly once per
    ClipJob, even when the job fans out to many (platform, layout) targets.
    Each target then calls `write_ass_from_segments` with its own canvas
    + MarginV — that's a few KB of disk I/O, not another transcription.

    `language=None` (default) lets Whisper auto-detect per segment. We
    previously hard-coded "en" as a ~3s speed optimization, but that
    forced English transcription on borderline / non-speech segments and
    occasionally produced hallucinated words. Auto-detect is the safer
    default; pass an explicit code to override.
    """
    slice_path = source_path.parent / f".__transcribe_slice_{int(start)}_{int(end)}.wav"
    try:
        extract_audio_slice(source_path, slice_path, start, end)
        return _transcribe_audio_to_segments(slice_path, language=language)
    finally:
        slice_path.unlink(missing_ok=True)


def _transcribe_audio_to_segments(
    audio_path: Path,
    language: str | None = None,
) -> list[Segment]:
    """Run Whisper on a pre-extracted audio file; return karaoke segments."""
    model = _get_whisper()
    _log.info("transcribing %s (lang=%s)", audio_path.name, language or "auto")
    segments_iter, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        word_timestamps=True,
        language=language,
    )
    karaoke: list[Segment] = []
    for segment in segments_iter:
        words = list(getattr(segment, "words", None) or [])
        if not words:
            karaoke.append(Segment(start=segment.start, end=segment.end, text=segment.text.strip()))
            continue
        karaoke.extend(_build_karaoke_segments(words))
    _log.info(
        "transcribed %d karaoke line(s), %d words/chunk, lang=%s",
        len(karaoke), WORDS_PER_CHUNK, info.language,
    )
    return karaoke


def write_ass_from_segments(
    segments: list[Segment],
    ass_path: Path,
    play_res_x: int = PLAY_RES_X,
    play_res_y: int = PLAY_RES_Y,
    margin_v: int | None = None,
) -> int:
    """Write a styled .ass file from already-transcribed karaoke segments.

    Cheap (a few KB of disk I/O) — every (platform, layout) target calls
    this once with its own canvas + MarginV after the shared Whisper pass.
    Returns dialogue-line count.
    """
    ass_path.write_text(
        _segments_to_ass(
            segments,
            play_res_x=play_res_x, play_res_y=play_res_y,
            margin_v=margin_v,
        ),
        encoding="utf-8",
    )
    return len(segments)


def transcribe_source_slice(
    source_path: Path,
    ass_path: Path,
    start: float,
    end: float,
    play_res_x: int = PLAY_RES_X,
    play_res_y: int = PLAY_RES_Y,
    margin_v: int | None = None,
) -> int:
    """Extract an audio slice from `source_path` covering [start, end],
    transcribe it, and write the .ass file. Returns dialogue-line count.

    `play_res_x`/`play_res_y` control the ASS canvas — pass the OUTPUT
    aspect (1080x1920 for vertical, 1080x1080 for square) so the burned
    captions match the rendered frame size. `margin_v` overrides the
    auto-selected MarginV (used by SplitProcessor to place captions at the
    seam between face and gameplay halves). Caller owns `ass_path` lifecycle.
    """
    slice_path = ass_path.with_suffix(".__slice.wav")
    try:
        extract_audio_slice(source_path, slice_path, start, end)
        return transcribe_to_ass(
            slice_path, ass_path,
            play_res_x=play_res_x, play_res_y=play_res_y,
            margin_v=margin_v,
        )
    finally:
        slice_path.unlink(missing_ok=True)


def transcribe_to_ass(
    input_path: Path,
    ass_path: Path,
    play_res_x: int = PLAY_RES_X,
    play_res_y: int = PLAY_RES_Y,
    margin_v: int | None = None,
) -> int:
    """Transcribe `input_path` with word-level timing and write an .ass file
    pre-styled for the given canvas size. Returns dialogue-line count.

    Defaults are vertical 1080x1920. Pass `play_res_y=1080` for square; the
    Style block's MarginV is auto-selected from LAYOUT_MARGIN_V to keep the
    caption proportionally placed (upper third of frame) regardless of aspect.
    Pass `margin_v` explicitly to override the table (split layout uses this
    to land captions just below the seam in the gameplay half).
    """
    model = _get_whisper()
    _log.info("transcribing %s (canvas=%dx%d)", input_path.name, play_res_x, play_res_y)
    segments_iter, info = model.transcribe(
        str(input_path),
        beam_size=5,
        word_timestamps=True,
    )

    karaoke: list[Segment] = []
    for segment in segments_iter:
        words = list(getattr(segment, "words", None) or [])
        if not words:
            # No word timing — fall back to flashing the whole segment
            karaoke.append(Segment(start=segment.start, end=segment.end, text=segment.text.strip()))
            continue
        karaoke.extend(_build_karaoke_segments(words))

    ass_path.write_text(
        _segments_to_ass(
            karaoke,
            play_res_x=play_res_x, play_res_y=play_res_y,
            margin_v=margin_v,
        ),
        encoding="utf-8",
    )
    _log.info(
        "wrote ASS (%d karaoke lines, %d words/chunk, lang=%s) -> %s",
        len(karaoke), WORDS_PER_CHUNK, info.language, ass_path,
    )
    return len(karaoke)


def prewarm_subtitles_filter() -> bool:
    """Probe ffmpeg for the libass `subtitles` filter and cache the result.

    Call this from the daemon's startup healthcheck so the per-clip path
    never pays the cold-launch cost. Windows Defender scans ffmpeg.exe on
    first invocation per session — a 10s timeout in that window silently
    disabled caption burn for the first clip of every session. Probing
    upfront with a generous timeout + caching the result fixes that.
    """
    global _libass_available
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "filter=subtitles"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _libass_available = False
        return False
    _libass_available = "Unknown filter" not in (result.stdout + result.stderr)
    return _libass_available


def has_subtitles_filter() -> bool:
    """True if ffmpeg has the libass-backed `subtitles` filter.

    Returns the cached probe result if available; otherwise probes once and
    caches. The capture / scheduler daemons call `prewarm_subtitles_filter`
    during startup healthcheck so this is already warm by the time clips
    are processed.
    """
    if _libass_available is None:
        return prewarm_subtitles_filter()
    return _libass_available


def subtitles_filter(
    subtitle_path: Path,
    play_res_x: int = PLAY_RES_X,
    play_res_y: int = PLAY_RES_Y,
) -> str:
    """Return the ffmpeg `-vf`-compatible filter snippet for an .ass (or .srt)
    subtitle file. The .ass file already contains all styling, so no
    `force_style` is needed.

    Pass `play_res_x`/`play_res_y` that MATCH the values used when generating
    the .ass file — otherwise libass scales the caption wrong. Vertical
    1080x1920 and square 1080x1080 are the two values used today.

    Windows note: ffmpeg's filter parser interprets backslashes in the path
    as escape sequences and silently drops them — e.g. `C:\\Users\\...`
    becomes `C:Users...` and ffmpeg "cannot find the file." The fix is to
    normalize to forward slashes; Windows ffmpeg accepts those just fine
    and the parser leaves them alone.
    """
    # 1. Backslashes → forward slashes (Windows path safety)
    path_str = str(subtitle_path).replace("\\", "/")
    # 2. Escape colons (drive letter on Windows, etc.) and the punctuation
    #    that has meaning inside the single-quoted filter argument
    escaped = path_str.replace(":", r"\:").replace(",", r"\,").replace("'", r"\'")
    return f"subtitles='{escaped}':original_size={play_res_x}x{play_res_y}"
