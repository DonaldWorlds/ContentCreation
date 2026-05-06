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

_log = logging.getLogger("zerino.processors.captions")
_whisper_model = None  # process-wide cache


# --------------------------------------------------------------------------- #
# Style — every value lands in the ASS Style: line below.                     #
#                                                                             #
# ASS colour format: &HAABBGGRR (alpha, blue, green, red).                    #
# Alignment numpad layout: 7=top-left   8=top-center   9=top-right            #
#                          4=mid-left   5=mid-center   6=mid-right            #
#                          1=bot-left   2=bot-center   3=bot-right            #
# --------------------------------------------------------------------------- #
PLAY_RES_X = 1080
PLAY_RES_Y = 1920
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
MARGIN_V = 780   # ~43 % down the 1920 frame — upper-middle, close to center

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


def _ass_header() -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PLAY_RES_X}\n"
        f"PlayResY: {PLAY_RES_Y}\n"
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
        f"{ALIGNMENT},{MARGIN_L},{MARGIN_R},{MARGIN_V},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _segments_to_ass(segments: list[Segment]) -> str:
    out = [_ass_header()]
    for seg in segments:
        start = _format_ass_timestamp(seg.start)
        end = _format_ass_timestamp(seg.end)
        # Text is the final field in a Dialogue line, so commas inside it
        # are NOT field separators and must not be escaped. Hard-wrap any
        # newlines (libass would treat \N as a line break otherwise).
        text = seg.text.strip().replace("\n", " ")
        out.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    return "".join(out)


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _log.info("loading faster-whisper model size=%s (first run downloads ~500MB)", WHISPER_MODEL_SIZE)
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model


def _build_karaoke_segments(words: list) -> list[Segment]:
    """Group `words` into chunks of WORDS_PER_CHUNK and emit one Segment per
    word — each Segment displays the full chunk with the current word
    coloured HIGHLIGHT_COLOUR and the rest TEXT_COLOUR.

    Result: TikTok-signature karaoke — the line stays on screen, one word at
    a time lights up in yellow (or whatever HIGHLIGHT_COLOUR is set to).
    """
    out: list[Segment] = []
    for i in range(0, len(words), WORDS_PER_CHUNK):
        chunk = words[i:i + WORDS_PER_CHUNK]
        if not chunk:
            continue
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
            out.append(Segment(start=current.start, end=current.end, text=text))
    return out


def transcribe_to_ass(input_path: Path, ass_path: Path) -> int:
    """Transcribe `input_path` with word-level timing and write an .ass file
    pre-styled for vertical 1080x1920 burn-in. Returns dialogue-line count.
    """
    model = _get_whisper()
    _log.info("transcribing %s", input_path.name)
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

    ass_path.write_text(_segments_to_ass(karaoke), encoding="utf-8")
    _log.info(
        "wrote ASS (%d karaoke lines, %d words/chunk, lang=%s) -> %s",
        len(karaoke), WORDS_PER_CHUNK, info.language, ass_path,
    )
    return len(karaoke)


def has_subtitles_filter() -> bool:
    """True if ffmpeg has the libass-backed `subtitles` filter."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "filter=subtitles"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "Unknown filter" not in (result.stdout + result.stderr)


def subtitles_filter(subtitle_path: Path) -> str:
    """Return the ffmpeg `-vf`-compatible filter snippet for an .ass (or .srt)
    subtitle file. The .ass file already contains all styling, so no
    `force_style` is needed.

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
    return f"subtitles='{escaped}':original_size={PLAY_RES_X}x{PLAY_RES_Y}"
