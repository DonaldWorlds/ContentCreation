"""Elim-feed + banner OCR and the "my events only" identity filter (Decision 1).

pytesseract/cv2 imported INSIDE functions (lazy/optional, DETECTION_DECISIONS.md §0).
Default Tesseract-CPU. Evidence (calibration): raw OCR mangles the stylized feed font,
so the identity filter prefers the orange highlight-color cue + fuzzy/alias match over
exact string equality, and read_region preprocesses (upscale + bright-text threshold).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Feed verbs, longest-first so "knocked out" matches before "knocked".
_VERBS = ["knocked out", "shotgunned", "headshotted", "headshot", "eliminated",
          "knocked", "blew up", "finished", "downed"]


def tesseract_cmd() -> str:
    """Resolve the Tesseract binary: PATH first, then known install dirs (the winget
    UB-Mannheim install lands in Program Files and is NOT on PATH)."""
    import os
    import shutil

    found = shutil.which("tesseract")
    if found:
        return found
    for cand in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ):
        if os.path.exists(cand):
            return cand
    raise RuntimeError("Tesseract binary not found (install: winget UB-Mannheim.TesseractOCR)")


def _preprocess(arr):
    """Upscale + isolate bright HUD text (white/orange on darker bg) -> binary image that
    Tesseract reads far better than the raw stylized crop."""
    import numpy as np
    from PIL import Image

    rgb = np.asarray(arr)[:, :, :3].astype(np.float32)
    # luma; HUD text is high-luma. Threshold relative to the crop's bright tail.
    luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    thr = max(150.0, float(np.percentile(luma, 92)))
    mask = (luma >= thr)
    out = np.where(mask, 0, 255).astype(np.uint8)   # dark text on white for Tesseract
    img = Image.fromarray(out)
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    return img


def read_region(image, *, preprocess: bool = True, psm: int = 6) -> str:
    """OCR a (cropped) HUD region. Lazy-imports pytesseract; preprocess isolates bright
    text so the stylized feed + colored banner become legible."""
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()
    img = _preprocess(image) if preprocess else Image.fromarray(__import__("numpy").asarray(image))
    return pytesseract.image_to_string(img, config=f"--psm {psm}")


def parse_feed_lines(text: str) -> list[dict]:
    """Parse kill-feed text into [{eliminator, verb, victim}]."""
    rows = []
    for line in text.splitlines():
        low = line.lower()
        verb = next((v for v in _VERBS if v in low), None)
        if not verb:
            continue
        idx = low.index(verb)
        eliminator = line[:idx].strip(" -|:.—")
        rest = line[idx + len(verb):]
        victim = rest
        for marker in (" with a", " with ", " using "):
            mi = victim.lower().find(marker)
            if mi != -1:
                victim = victim[:mi]
                break
        if not eliminator:
            continue
        rows.append({"eliminator": eliminator, "verb": verb, "victim": victim.strip(" -|:.")})
    return rows


def banner_kind(text: str) -> tuple[str, int] | None:
    """Classify a center-banner OCR string. Returns (kind, multi_count) or None.
    kind in {ELIM, KNOCK}; multi_count from 'ELIMINATION xN' (1 if absent)."""
    up = text.upper()
    if "ELIMINAT" not in up and "KNOCKED" not in up:
        return None
    m = re.search(r"X\s*([2-9])", up)
    count = int(m.group(1)) if m else 1
    kind = "KNOCK" if ("KNOCKED" in up and "ELIMINAT" not in up) else "ELIM"
    return kind, count


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _eliminator_candidates(eliminator: str) -> list[str]:
    """The raw OCR eliminator plus cleaned variants: the part before any '(count)'/'[..]'
    and the leading whitespace token. OCR appends the kill-count ('kkthedon_ (117)') and
    leading garbage, which wrecks a whole-string match — these give the matcher a clean
    shot at just the name."""
    cands = {eliminator}
    before_paren = re.split(r"[(\[]", eliminator)[0].strip()
    cands.add(before_paren)
    toks = before_paren.split() or eliminator.split()
    if toks:
        cands.add(toks[0])      # leading token
        cands.add(toks[-1])     # trailing token (leading OCR garbage case)
    return [c for c in cands if c]


def is_own_event(eliminator: str, *, highlighted: bool, identity: dict) -> bool:
    """Decision 1 filter (adapter-side, never core): True iff this elim is the operator's.
    Primary signal = `highlighted` (own feed lines are color-highlighted); secondary =
    fuzzy match of the eliminator (and its cleaned variants) vs identity['gamertag'] +
    identity.get('aliases', []). OCR mangles the stylized font, so this is fuzzy by design."""
    if highlighted:
        return True
    names = [identity.get("gamertag", "")] + list(identity.get("aliases", []))
    norm_names = [_norm(n) for n in names if n]
    for cand in _eliminator_candidates(eliminator):
        nc = _norm(cand)
        if len(nc) < 4:
            continue
        for nn in norm_names:
            if not nn:
                continue
            if nn in nc or nc in nn:
                return True
            if SequenceMatcher(None, nn, nc).ratio() >= 0.7:
                return True
    return False
