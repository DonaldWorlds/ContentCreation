"""Render an mp4 into an inspectable report.

Produces a directory of artifacts that a reviewer (human or LLM) can read
without watching the video or hearing the audio. The render path is
unaffected; this is a thin ffmpeg/ffprobe wrapper that runs after a clip
exists on disk.

Outputs land in <out_dir>/<clip_stem>/:

    frame_10pct.png  frame_40pct.png  frame_70pct.png  frame_95pct.png
        - keyframes at 10/40/70/95 % of the clip's duration
    caption_sample.png  (only when an .ass sidecar is found)
        - extra keyframe at a dialogue-active timestamp so the burned
          captions are visible — used to verify the active karaoke style
          (font, color, position) at a glance
    spectrogram.png
        - full-clip audio spectrogram (loudness pumping, compression
          artifacts, silence handling visible at a glance)
    loudness.json
        - ebur128 integrated loudness, true peak, LRA
    ffprobe.json
        - raw ffprobe -show_streams -show_format dump
    captions.json  (only when an .ass sidecar is found)
        - parsed ASS style + first N dialogue events + Latin-script check
    summary.md
        - one-page human/LLM-readable summary distilled from the above

Caption-correctness verification (S4.x): if a `.ass` sidecar lives next to
the input mp4 (same stem, `.ass` extension), it gets parsed and reported.
Latin-script check flags any non-Latin chars in dialogue text — catches
the pre-S4 foreign-language Whisper failure where captions came back in
Korean / Welsh / Maori / Cyrillic.

Optional --reference <path> adds a `compare/frame_*pct.png` subdir of
side-by-side frames (hstack) for regression checks.

Usage:
    python -m zerino.cli.quality_verify path/to/render.mp4
    python -m zerino.cli.quality_verify path/to/new.mp4 --reference path/to/old.mp4
    python -m zerino.cli.quality_verify path/to/render.mp4 --out-dir custom/

Default --out-dir is `quality_report/`. Per-clip subdir is the input stem.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from zerino.config import get_logger
from zerino.ffmpeg.ffmpeg_utils import _run_ffprobe

log = get_logger("zerino.cli.quality_verify")

FRAME_PERCENTS = (0.10, 0.40, 0.70, 0.95)
SPECTROGRAM_SIZE = "1920x540"

# Latin-script Unicode ranges considered acceptable in caption dialogue text.
# Everything else (Cyrillic, Greek beyond ASCII coverage, Hebrew, Arabic,
# Devanagari, Thai, CJK, Hangul, etc.) trips the language guard. Includes
# common typographic punctuation produced by Whisper's punctuation pass
# (curly quotes, em / en dashes, ellipsis) so they don't false-positive.
_LATIN_ALLOWED_CODEPOINTS = frozenset(
    {0x2018, 0x2019, 0x201C, 0x201D, 0x2013, 0x2014, 0x2026}
)
_LATIN_MAX_BASIC = 0x024F  # Basic Latin + Latin-1 Supplement + Latin Extended-A/B
# Number of dialogue lines to include in the captions JSON / summary.
_CAPTION_SAMPLE_LINES = 8


def _ffprobe_dump(input_path: Path) -> dict:
    raw = _run_ffprobe([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(input_path),
    ])
    return json.loads(raw.decode("utf-8"))


def _duration(probe: dict) -> float:
    fmt = probe.get("format", {})
    if "duration" in fmt:
        return float(fmt["duration"])
    for stream in probe.get("streams", []):
        if "duration" in stream:
            return float(stream["duration"])
    raise RuntimeError("ffprobe reported no duration")


def _first_stream(probe: dict, kind: str) -> dict | None:
    return next((s for s in probe.get("streams", []) if s.get("codec_type") == kind), None)


def _extract_frame(input_path: Path, ts: float, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-ss", f"{ts:.3f}",
        "-i", str(input_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"frame extraction failed at t={ts:.3f} for {input_path.name}: "
            f"{result.stderr.strip()[:400]}"
        )


def _hstack_frames(left: Path, right: Path, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", str(left), "-i", str(right),
        "-filter_complex", "hstack=inputs=2",
        "-frames:v", "1",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"hstack failed for {left.name} + {right.name}: "
            f"{result.stderr.strip()[:400]}"
        )


def _spectrogram(input_path: Path, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", str(input_path),
        "-lavfi", f"showspectrumpic=s={SPECTROGRAM_SIZE}:legend=1",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"spectrogram failed for {input_path.name}: "
            f"{result.stderr.strip()[:400]}"
        )


_EBUR128_KEYS = (
    ("integrated_lufs", r"I:\s*(-?\d+\.\d+)\s*LUFS"),
    ("loudness_range_lu", r"LRA:\s*(-?\d+\.\d+)\s*LU"),
    ("true_peak_dbtp", r"Peak:\s*(-?\d+\.\d+)\s*dBFS"),
    ("threshold_lufs", r"Threshold:\s*(-?\d+\.\d+)\s*LUFS"),
)


def _loudness(input_path: Path) -> dict:
    """Run ffmpeg's ebur128 filter and parse the summary block out of stderr.

    ebur128 doesn't have a JSON output mode; the summary lands in the stderr
    text block at the end. Parsing is regex-based and tolerant of missing
    fields (ffmpeg versions vary slightly in the section header).
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin",
        "-i", str(input_path),
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr or ""

    summary_start = stderr.rfind("Summary:")
    block = stderr[summary_start:] if summary_start != -1 else stderr

    parsed: dict[str, float | None] = {}
    for name, pattern in _EBUR128_KEYS:
        m = re.search(pattern, block)
        parsed[name] = float(m.group(1)) if m else None

    parsed["ffmpeg_returncode"] = result.returncode
    parsed["raw_summary"] = block.strip()[:2000] if block.strip() else None
    return parsed


def _parse_ass_timestamp(s: str) -> float:
    """ASS time format H:MM:SS.cs → seconds. Single-digit hour is allowed."""
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _strip_ass_overrides(text: str) -> str:
    """Remove ASS inline override codes like `{\\c&Hxxxxxx&}` from dialogue
    text — those are color/style hints, not letters. Leaves the visible
    word characters for the Latin-script integrity check.
    """
    return re.sub(r"\{[^}]*\}", "", text)


def _is_latin_script(text: str) -> bool:
    """True if every character is in the Latin scripts we ship in. False if
    any char is Cyrillic / CJK / Hangul / Arabic / Hebrew / etc.

    The pre-S4 Whisper auto-detect would land on Korean / Welsh / Maori on
    noisy clips; this check is the run-after-the-render guard that catches
    a regression if the language-force ever slips.
    """
    for c in text:
        cp = ord(c)
        if cp <= _LATIN_MAX_BASIC:
            continue
        if cp in _LATIN_ALLOWED_CODEPOINTS:
            continue
        return False
    return True


def _parse_ass(ass_path: Path) -> dict:
    """Parse an .ass file into a dict of {play_res, style, dialogues}.

    Best-effort, format-tolerant — uses the field positions from libass's
    default V4+ Style: header (the same header _captions.py emits). If
    upstream ever changes column order, the style fields just come back
    as `None` instead of the parser crashing.
    """
    text = ass_path.read_text(encoding="utf-8", errors="replace")
    play_res: dict[str, int | None] = {"x": None, "y": None}
    style: dict[str, str | None] = {}
    dialogues: list[dict] = []
    section: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]").strip()
            continue
        if section == "Script Info":
            if line.lower().startswith("playresx:"):
                try:
                    play_res["x"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.lower().startswith("playresy:"):
                try:
                    play_res["y"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        elif section == "V4+ Styles" and line.startswith("Style:"):
            parts = line.split(":", 1)[1].strip().split(",")
            # libass V4+ Style: column order (23 fields)
            # 0 Name, 1 Fontname, 2 Fontsize, 3 PrimaryColour,
            # 4 SecondaryColour, 5 OutlineColour, 6 BackColour,
            # 7 Bold, 8 Italic, 9 Underline, 10 StrikeOut,
            # 11 ScaleX, 12 ScaleY, 13 Spacing, 14 Angle,
            # 15 BorderStyle, 16 Outline, 17 Shadow,
            # 18 Alignment, 19 MarginL, 20 MarginR, 21 MarginV, 22 Encoding
            if len(parts) >= 22:
                style = {
                    "name": parts[0],
                    "fontname": parts[1],
                    "fontsize": parts[2],
                    "primary_colour": parts[3],
                    "secondary_colour": parts[4],
                    "outline_colour": parts[5],
                    "back_colour": parts[6],
                    "bold": parts[7],
                    "border_style": parts[15],
                    "outline": parts[16],
                    "alignment": parts[18],
                    "margin_l": parts[19],
                    "margin_r": parts[20],
                    "margin_v": parts[21],
                }
        elif section == "Events" and line.startswith("Dialogue:"):
            # Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
            # Text is the final field — split with maxsplit so commas inside
            # the text are not treated as field separators.
            body = line.split(":", 1)[1].strip()
            parts = body.split(",", 9)
            if len(parts) < 10:
                continue
            try:
                start_ts = _parse_ass_timestamp(parts[1].strip())
                end_ts = _parse_ass_timestamp(parts[2].strip())
            except (ValueError, IndexError):
                continue
            text_raw = parts[9]
            dialogues.append({
                "start": start_ts,
                "end": end_ts,
                "text_raw": text_raw,
                "text_plain": _strip_ass_overrides(text_raw).strip(),
            })

    # Latin-script integrity check: every dialogue's plain text must be
    # entirely in Latin scripts. One foreign-script slip flips this.
    latin_ok = all(_is_latin_script(d["text_plain"]) for d in dialogues)
    offending = [d for d in dialogues if not _is_latin_script(d["text_plain"])]

    result = {
        "play_res": play_res,
        "style": style,
        "dialogues": dialogues,
        "dialogue_count": len(dialogues),
        "latin_script_ok": latin_ok,
        "non_latin_examples": [d["text_plain"][:80] for d in offending[:3]],
    }
    # Geometry / professional-look checks (size, centering, safe-zone,
    # face-clearance heuristic). Attached AFTER the parsed result so
    # downstream callers see a fully-populated `captions` dict.
    result["geometry"] = _check_caption_geometry(result)
    return result


# --- Caption geometry checks (the "professional captions" question) -------- #
# Heuristics + constants used to grade caption layout without a face detector.
# A face-detection upgrade (MediaPipe) would replace `FACE_TOP_FRACTION` with
# a per-frame measured face bbox.

# Typical webcam talking-head: the top of the speaker's head lands at roughly
# 30 % down the canvas. Caption box bottom must clear this line, or captions
# overlap the speaker's hair / forehead. Wrong for corner-cam OBS scenes
# (where the face is in a quadrant, not centered) — those need MediaPipe.
FACE_TOP_FRACTION_VERTICAL = 0.30
FACE_TOP_FRACTION_SQUARE = 0.20  # face is closer to top in 1:1 framing
FACE_TOP_FRACTION_SPLIT = 0.45   # face quadrant in upper half of split layout

# Platform top safe area (pixels). Caption must sit BELOW this, or the
# platform UI (clock, notch, profile button) obscures it.
TOP_SAFE_AREA_VERTICAL = 120
TOP_SAFE_AREA_SQUARE = 90

# Maximum acceptable line height as a fraction of canvas height. The user's
# prior feedback flagged "letters take up the whole clip" as sloppy; 8 % is
# the line above which a single line of caption dominates the frame.
MAX_LINE_HEIGHT_FRACTION = 0.08

# Rough leading factor — ASS FontSize is the cap-height pixel size; actual
# rendered line height (including descenders + line gap) is ~1.4× that.
LINE_HEIGHT_LEADING = 1.4


def _check_caption_geometry(captions: dict) -> dict:
    """Compute caption layout metrics from the parsed .ass + report pass/fail
    for each of the "professional captions" invariants. Pure geometry; no CV.

    Returned dict structure:
        {
          "metrics": { ... measured numbers ... },
          "checks":  { check_name: bool, ... },
          "verdict": "PASS" | "FAIL",
          "fail_reasons": [str, ...],
        }
    """
    style = captions.get("style") or {}
    play_res = captions.get("play_res") or {}
    play_y = play_res.get("y") or 1920
    play_x = play_res.get("x") or 1080

    def _int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _float(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    fontsize = _float(style.get("fontsize"))
    margin_v = _int(style.get("margin_v"))
    margin_l = _int(style.get("margin_l"))
    margin_r = _int(style.get("margin_r"))
    alignment = _int(style.get("alignment"))

    line_height_px = fontsize * LINE_HEIGHT_LEADING
    line_height_frac = line_height_px / play_y if play_y else 0.0
    caption_bottom_px = margin_v + line_height_px

    # Layout-aware safe-area + face-top heuristic.
    if play_x == 1080 and play_y == 1080:
        face_top_frac = FACE_TOP_FRACTION_SQUARE
        top_safe = TOP_SAFE_AREA_SQUARE
        layout_hint = "square"
    elif play_x == 1080 and play_y == 1920 and margin_v >= 900:
        # Heuristic: the split layout pushes captions below the seam
        # (MarginV ~1020), so high MarginV on a 1920 canvas == split.
        face_top_frac = FACE_TOP_FRACTION_SPLIT
        top_safe = TOP_SAFE_AREA_VERTICAL
        layout_hint = "split"
    else:
        face_top_frac = FACE_TOP_FRACTION_VERTICAL
        top_safe = TOP_SAFE_AREA_VERTICAL
        layout_hint = "vertical"

    face_top_px = play_y * face_top_frac

    # Check 1 — line height fits within 8% of canvas.
    size_ok = line_height_frac < MAX_LINE_HEIGHT_FRACTION

    # Check 2 — caption centered (MarginL == MarginR for top-center
    # alignments). Note: zero on both is the karaoke-chunk default (we
    # rely on WrapStyle=2 and short chunks instead of a width box) — also
    # passes because the values are symmetric.
    centering_ok = (margin_l == margin_r)

    # Check 3 — caption sits BELOW the platform top safe area.
    above_safe_top = (margin_v >= top_safe)

    # Check 4 — caption's bottom edge clears the heuristic face top.
    # For talking_head clips, this means captions hover above the head.
    face_clear = (caption_bottom_px < face_top_px)

    # Check 5 — alignment is top-center (8). Anything else and the rest of
    # the math is wrong anyway.
    alignment_top_center = (alignment == 8)

    checks = {
        "size_ok": size_ok,
        "centering_ok": centering_ok,
        "above_safe_top": above_safe_top,
        "face_clear (heuristic)": face_clear,
        "alignment_top_center": alignment_top_center,
    }

    fail_reasons: list[str] = []
    if not size_ok:
        fail_reasons.append(
            f"FontSize {fontsize}pt produces a line ~{line_height_px:.0f}px tall "
            f"({line_height_frac * 100:.1f}% of canvas) — exceeds the {MAX_LINE_HEIGHT_FRACTION * 100:.0f}% "
            "max. Captions will dominate the frame ('letters take up whole clip')."
        )
    if not centering_ok:
        fail_reasons.append(
            f"MarginL={margin_l}, MarginR={margin_r} — asymmetric. For "
            "Alignment=8 (top-center) these must be equal or the caption "
            "drifts off-center ('slop on the left/right')."
        )
    if not above_safe_top:
        fail_reasons.append(
            f"MarginV={margin_v}px is INSIDE the top safe area (< {top_safe}px). "
            "The platform's UI (clock, notch, profile button) will overlay the caption."
        )
    if not face_clear:
        fail_reasons.append(
            f"Caption bottom edge at y={caption_bottom_px:.0f}px crosses the "
            f"heuristic face-top line at y={face_top_px:.0f}px ({face_top_frac * 100:.0f}% down "
            f"the {layout_hint} canvas). Captions will overlap the speaker's "
            "face. (If the streamer's webcam is in a non-typical position, "
            "this heuristic can false-positive — add face detection to disambiguate.)"
        )
    if not alignment_top_center:
        fail_reasons.append(
            f"Alignment={alignment} is not 8 (top-center). The other geometry "
            "checks assume top-center; they may be wrong for your alignment."
        )

    return {
        "metrics": {
            "play_res": {"x": play_x, "y": play_y},
            "layout_inferred": layout_hint,
            "fontsize_pt": fontsize,
            "line_height_px": round(line_height_px, 1),
            "line_height_pct_of_canvas": round(line_height_frac * 100, 2),
            "margin_v_px": margin_v,
            "margin_l_px": margin_l,
            "margin_r_px": margin_r,
            "alignment": alignment,
            "caption_bottom_px": round(caption_bottom_px, 1),
            "face_top_heuristic_px": round(face_top_px, 1),
            "top_safe_area_px": top_safe,
        },
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "fail_reasons": fail_reasons,
    }


def _pick_caption_sample_time(captions: dict, duration: float) -> float | None:
    """Choose a timestamp for caption_sample.png — pick the dialogue closest
    to the clip midpoint, then add 0.15s so the karaoke highlight has
    settled on a stable word rather than a transition edge.
    """
    dialogues = captions.get("dialogues") or []
    if not dialogues:
        return None
    midpoint = duration / 2.0
    best = min(dialogues, key=lambda d: abs((d["start"] + d["end"]) / 2.0 - midpoint))
    sample = best["start"] + 0.15
    # Clamp to the clip; if past the end, fall back to the dialogue's start.
    if sample >= duration:
        sample = max(0.0, best["start"])
    return sample


def _summary_markdown(
    input_path: Path,
    probe: dict,
    loudness: dict,
    frame_files: list[Path],
    spectrogram_file: Path,
    compare_dir: Path | None,
    captions: dict | None = None,
    caption_sample_file: Path | None = None,
) -> str:
    video = _first_stream(probe, "video") or {}
    audio = _first_stream(probe, "audio") or {}
    fmt = probe.get("format", {})

    def _maybe(d: dict, *keys: str) -> str:
        for k in keys:
            v = d.get(k)
            if v is not None and v != "":
                return str(v)
        return "—"

    duration = float(fmt.get("duration", 0.0) or 0.0)
    size_mb = float(fmt.get("size", 0) or 0) / (1024 * 1024)
    overall_bitrate = int(fmt.get("bit_rate", 0) or 0) / 1000  # kbps

    lines = [
        f"# Quality report — {input_path.name}",
        "",
        f"- **Path:** `{input_path}`",
        f"- **Duration:** {duration:.2f} s",
        f"- **File size:** {size_mb:.2f} MB",
        f"- **Overall bitrate:** {overall_bitrate:.0f} kbps",
        "",
        "## Video",
        "",
        f"- **Codec:** {_maybe(video, 'codec_name')} ({_maybe(video, 'profile')})",
        f"- **Resolution:** {_maybe(video, 'width')}x{_maybe(video, 'height')}",
        f"- **Frame rate:** {_maybe(video, 'avg_frame_rate', 'r_frame_rate')}",
        f"- **Pixel format:** {_maybe(video, 'pix_fmt')}",
        f"- **Color primaries:** {_maybe(video, 'color_primaries')}",
        f"- **Color transfer:** {_maybe(video, 'color_transfer')}",
        f"- **Color space:** {_maybe(video, 'color_space')}",
        f"- **Color range:** {_maybe(video, 'color_range')}",
        f"- **Bit rate:** {_maybe(video, 'bit_rate')} bps",
        "",
        "## Audio",
        "",
        f"- **Codec:** {_maybe(audio, 'codec_name')}",
        f"- **Sample rate:** {_maybe(audio, 'sample_rate')} Hz",
        f"- **Channels:** {_maybe(audio, 'channels')} ({_maybe(audio, 'channel_layout')})",
        f"- **Bit rate:** {_maybe(audio, 'bit_rate')} bps",
        "",
        "## Loudness (ebur128)",
        "",
        f"- **Integrated:** {loudness.get('integrated_lufs')} LUFS  (target: -14 for TikTok/IG)",
        f"- **Loudness range:** {loudness.get('loudness_range_lu')} LU  (target: ~7 LU for short-form)",
        f"- **True peak:** {loudness.get('true_peak_dbtp')} dBTP  (must be < -1.0 dBTP to survive platform re-encode)",
        "",
        "## Inspection artifacts",
        "",
    ]
    for f in frame_files:
        lines.append(f"- ![{f.name}]({f.name})")
    if caption_sample_file is not None:
        lines.append(f"- ![caption sample]({caption_sample_file.name})  (dialogue-active frame — captions visible)")
    lines.append(f"- ![spectrogram]({spectrogram_file.name})")

    # Captions section — only when an .ass sidecar was found
    if captions is not None:
        style = captions.get("style") or {}
        play_res = captions.get("play_res") or {}
        dialogues = captions.get("dialogues") or []

        lines.append("")
        lines.append("## Captions (.ass sidecar)")
        lines.append("")
        lines.append(
            f"- **Canvas:** PlayResX={play_res.get('x')}, PlayResY={play_res.get('y')}"
        )
        lines.append(
            f"- **Font:** {style.get('fontname')} {style.get('fontsize')}pt, "
            f"bold={style.get('bold')}, alignment={style.get('alignment')}, "
            f"MarginV={style.get('margin_v')}"
        )
        lines.append(
            f"- **Colors (mint preset expected):** primary={style.get('primary_colour')}, "
            f"secondary={style.get('secondary_colour')}, "
            f"outline={style.get('outline_colour')}, back={style.get('back_colour')}"
        )
        lines.append(
            f"  - Reference: TEXT=`&H000000FF` (red), HIGHLIGHT=`&H0000FFFF` (yellow)"
        )
        lines.append(
            f"- **Border / outline:** style={style.get('border_style')}, "
            f"outline={style.get('outline')}px"
        )
        lines.append(
            f"- **Dialogue events:** {captions.get('dialogue_count', 0)}"
        )
        latin_ok = captions.get("latin_script_ok")
        if latin_ok:
            lines.append("- **Latin-script check:** PASS (every dialogue line is Latin script)")
        else:
            lines.append("- **Latin-script check:** **FAIL** — non-Latin chars detected:")
            for example in captions.get("non_latin_examples", []):
                lines.append(f"  - `{example}`")
            lines.append(
                "  - This means Whisper landed on a non-English language despite the force. "
                "Check logs for the language-guard warning."
            )

        # Geometry / professional-look section
        geometry = captions.get("geometry") or {}
        if geometry:
            m = geometry.get("metrics", {})
            checks = geometry.get("checks", {})
            verdict = geometry.get("verdict", "?")
            fail_reasons = geometry.get("fail_reasons", [])

            lines.append("")
            lines.append("### Caption geometry")
            lines.append("")
            lines.append(f"- **Inferred layout:** {m.get('layout_inferred')}  "
                         f"(canvas {m.get('play_res', {}).get('x')}x{m.get('play_res', {}).get('y')})")
            lines.append(
                f"- **Line height:** {m.get('line_height_px')} px = "
                f"{m.get('line_height_pct_of_canvas')}% of canvas  "
                f"(< {MAX_LINE_HEIGHT_FRACTION*100:.0f}% required)"
            )
            lines.append(
                f"- **Caption baseline (MarginV):** {m.get('margin_v_px')} px from top"
            )
            lines.append(
                f"- **Caption bottom edge (estimated):** y = {m.get('caption_bottom_px')} px"
            )
            lines.append(
                f"- **Top safe area:** y < {m.get('top_safe_area_px')} px (platform UI zone)"
            )
            lines.append(
                f"- **Face-top heuristic:** y = {m.get('face_top_heuristic_px')} px  "
                "(captions must finish above this)"
            )
            lines.append(
                f"- **Horizontal margins:** L={m.get('margin_l_px')}, R={m.get('margin_r_px')}  "
                "(must be equal for top-center alignment)"
            )
            lines.append("")
            lines.append("**Professional-quality checks:**")
            for name, ok in checks.items():
                lines.append(f"- {'PASS' if ok else 'FAIL'} `{name}`")
            lines.append("")
            lines.append(f"**Overall verdict: {verdict}**")
            if fail_reasons:
                lines.append("")
                lines.append("**Why it failed:**")
                for r in fail_reasons:
                    lines.append(f"- {r}")

        if dialogues:
            lines.append("")
            lines.append(f"### Sample dialogue (first {_CAPTION_SAMPLE_LINES} lines)")
            lines.append("")
            for d in dialogues[:_CAPTION_SAMPLE_LINES]:
                lines.append(
                    f"- `{d['start']:.2f}s` -> `{d['end']:.2f}s`: {d['text_plain']}"
                )

    if compare_dir is not None and compare_dir.exists():
        lines.append("")
        lines.append("## Side-by-side comparison (new | reference)")
        lines.append("")
        for f in sorted(compare_dir.glob("frame_*pct.png")):
            lines.append(f"- ![{f.name}](compare/{f.name})")

    lines.append("")
    return "\n".join(lines)


def verify(
    input_path: Path,
    out_root: Path,
    reference_path: Path | None = None,
) -> Path:
    """Run the full verification pipeline. Returns the per-clip output dir."""
    input_path = input_path.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input not found: {input_path}")

    out_dir = (out_root / input_path.stem).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("verifying %s -> %s", input_path.name, out_dir)

    probe = _ffprobe_dump(input_path)
    (out_dir / "ffprobe.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")

    duration = _duration(probe)

    frame_files: list[Path] = []
    for pct in FRAME_PERCENTS:
        ts = duration * pct
        out_path = out_dir / f"frame_{int(pct * 100):02d}pct.png"
        _extract_frame(input_path, ts, out_path)
        frame_files.append(out_path)
        log.info("frame extracted: %s @ %.2fs", out_path.name, ts)

    spectrogram_path = out_dir / "spectrogram.png"
    _spectrogram(input_path, spectrogram_path)
    log.info("spectrogram: %s", spectrogram_path.name)

    loudness = _loudness(input_path)
    (out_dir / "loudness.json").write_text(json.dumps(loudness, indent=2), encoding="utf-8")
    log.info(
        "loudness: integrated=%s LUFS  LRA=%s LU  peak=%s dBTP",
        loudness.get("integrated_lufs"),
        loudness.get("loudness_range_lu"),
        loudness.get("true_peak_dbtp"),
    )

    # Caption sidecar (.ass next to the input mp4) — added by S4.x. When
    # present, parse it, save a captions.json summary, and pull one extra
    # frame at a dialogue-active timestamp so the burnt-in captions are
    # actually visible somewhere in the report. Missing sidecar is not an
    # error — older renders won't have one.
    captions: dict | None = None
    caption_sample_path: Path | None = None
    ass_sidecar = input_path.with_suffix(".ass")
    if ass_sidecar.exists():
        captions = _parse_ass(ass_sidecar)
        (out_dir / "captions.json").write_text(
            json.dumps(captions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(
            "captions: %d dialogue events, latin_ok=%s",
            captions.get("dialogue_count", 0), captions.get("latin_script_ok"),
        )
        sample_ts = _pick_caption_sample_time(captions, duration)
        if sample_ts is not None:
            caption_sample_path = out_dir / "caption_sample.png"
            _extract_frame(input_path, sample_ts, caption_sample_path)
            log.info("caption sample frame: %s @ %.2fs", caption_sample_path.name, sample_ts)
    else:
        log.info("no .ass sidecar at %s - skipping caption checks", ass_sidecar)

    compare_dir: Path | None = None
    if reference_path is not None:
        reference_path = reference_path.resolve()
        if not reference_path.exists():
            raise FileNotFoundError(f"reference not found: {reference_path}")
        compare_dir = out_dir / "compare"
        compare_dir.mkdir(exist_ok=True)
        ref_probe = _ffprobe_dump(reference_path)
        ref_duration = _duration(ref_probe)
        for pct, new_frame in zip(FRAME_PERCENTS, frame_files):
            ref_ts = ref_duration * pct
            ref_tmp = compare_dir / f".__ref_{int(pct * 100):02d}.png"
            _extract_frame(reference_path, ref_ts, ref_tmp)
            stacked = compare_dir / f"frame_{int(pct * 100):02d}pct.png"
            _hstack_frames(new_frame, ref_tmp, stacked)
            ref_tmp.unlink(missing_ok=True)
            log.info("compare: %s", stacked.name)

    summary = _summary_markdown(
        input_path, probe, loudness, frame_files, spectrogram_path, compare_dir,
        captions=captions, caption_sample_file=caption_sample_path,
    )
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")

    log.info("done: %s", out_dir)
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render an mp4 into an inspectable quality report.",
    )
    parser.add_argument("input", help="Path to the mp4 to inspect.")
    parser.add_argument(
        "--reference", default=None,
        help="Optional reference mp4 for side-by-side comparison frames.",
    )
    parser.add_argument(
        "--out-dir", default="quality_report",
        help="Root output directory. Default: quality_report/",
    )
    args = parser.parse_args()

    out_dir = verify(
        Path(args.input),
        Path(args.out_dir),
        Path(args.reference) if args.reference else None,
    )
    print(f"\nReport: {out_dir}")
    print(f"Open:   {out_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
