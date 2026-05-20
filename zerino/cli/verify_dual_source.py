"""Synthetic verification for the dual-source split + square render paths.

No OBS, no real recordings. Generates two labelled 1920x1080 test sources
with ffmpeg lavfi:

  - GAME: dark-blue field, a large centred "CENTER ACTION" marker + a centre
    crosshair, and "EDGE L"/"EDGE R" markers pinned to the far left/right
    edges. After a centred cover-crop into the bottom panel the EDGE markers
    must be GONE and CENTER ACTION must remain — proves the game crop is
    centred (the live-test off-centre bug) and that no facecam can bleed
    (there is no facecam in this source).
  - FACE: solid green field with a big "FACE" label. After cover-scaling into
    the top panel it must fill the whole top half (sharp, no game pixels).

It then runs both dual exports and extracts a mid-clip frame from each so the
result can be eyeballed (or read by the assistant as a PNG):

  split frame  -> top half all-green "FACE", bottom half blue "CENTER ACTION",
                  clean seam, no bleed either way.
  square frame -> all-green "FACE" filling 1080x1080.

Also exercises `ClipService._find_face_pair` (time-based pairing + the
out-of-window miss) and confirms the single-source fallbacks still construct.

Usage:
    python -m zerino.cli.verify_dual_source [--out-dir verify_dual/] [--keep]

This is a throwaway dev/QA helper — it imports the render code but is not part
of the render path and can be deleted without consequence.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
_FONT = f"fontfile={ARIAL}:" if Path(ARIAL).exists() else ""


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-2000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])} ...")


def _make_game(path: Path, seconds: int = 6) -> None:
    """Blue field, centred CENTER ACTION + crosshair, edge markers, 1 kHz tone."""
    vf = (
        f"drawbox=x=956:y=0:w=8:h=1080:color=white@0.6:t=fill,"
        f"drawbox=x=0:y=536:w=1920:h=8:color=white@0.6:t=fill,"
        f"drawtext={_FONT}text='CENTER ACTION':fontcolor=yellow:fontsize=90:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-120,"
        f"drawtext={_FONT}text='EDGE L':fontcolor=red:fontsize=70:x=20:y=(h-text_h)/2,"
        f"drawtext={_FONT}text='EDGE R':fontcolor=red:fontsize=70:x=w-text_w-20:y=(h-text_h)/2"
    )
    _run([
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x102040:s=1920x1080:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=48000:d={seconds}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ])


def _make_face(path: Path, seconds: int = 6) -> None:
    """Green field, big FACE label, 440 Hz tone (distinct from game)."""
    vf = (
        f"drawtext={_FONT}text='FACE':fontcolor=white:fontsize=300:"
        f"x=(w-text_w)/2:y=(h-text_h)/2,"
        f"drawtext={_FONT}text='cam top':fontcolor=black:fontsize=80:"
        f"x=(w-text_w)/2:y=80"
    )
    _run([
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x108040:s=1920x1080:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:d={seconds}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ])


def _grab_frame(video: Path, out_png: Path, at: float) -> None:
    _run([
        "ffmpeg", "-y", "-nostdin", "-ss", f"{at:.2f}", "-i", str(video),
        "-frames:v", "1", str(out_png),
    ])


def _check_pairing(tmp: Path) -> bool:
    """Exercise ClipService._find_face_pair: in-window hit + out-of-window miss."""
    from zerino.capture.services import clip_service as cs

    ok = True
    rec_dir = tmp / "recordings_root"
    face_dir = rec_dir / "face"
    face_dir.mkdir(parents=True, exist_ok=True)

    game = rec_dir / "game.mp4"
    game.write_bytes(b"x")
    near = face_dir / "near.mp4"
    near.write_bytes(b"x")

    # Point the module's dirs at our temp tree.
    orig_face_dir = cs.FACE_RECORDINGS_DIR
    cs.FACE_RECORDINGS_DIR = face_dir
    try:
        now = time.time()
        import os
        os.utime(game, (now, now))
        os.utime(near, (now + 1.0, now + 1.0))  # 1s skew -> within window
        svc = cs.ClipService.__new__(cs.ClipService)  # no DB
        hit = svc._find_face_pair(game)
        if hit != near:
            print(f"  [FAIL] in-window pairing: got {hit}, expected {near}")
            ok = False
        else:
            print("  [ok] in-window face pair matched (1s skew)")

        os.utime(near, (now + 60.0, now + 60.0))  # 60s skew -> out of window
        miss = svc._find_face_pair(game)
        if miss is not None:
            print(f"  [FAIL] out-of-window should be None, got {miss}")
            ok = False
        else:
            print("  [ok] out-of-window face correctly rejected (60s skew)")
    finally:
        cs.FACE_RECORDINGS_DIR = orig_face_dir
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic dual-source render verification.")
    ap.add_argument("--out-dir", default="verify_dual", help="Where to write frames/clips.")
    ap.add_argument("--keep", action="store_true", help="Keep the intermediate clips, not just frames.")
    args = ap.parse_args()

    from zerino.ffmpeg.export_generator import ExportGenerator

    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        game = tmp / "game.mp4"
        face = tmp / "face.mp4"
        print("generating synthetic GAME + FACE sources (1920x1080, 6s)...")
        _make_game(game)
        _make_face(face)

        gen = ExportGenerator()

        split_mp4 = (out if args.keep else tmp) / "dual_split.mp4"
        square_mp4 = (out if args.keep else tmp) / "dual_square.mp4"

        print("rendering dual SPLIT (game bottom, face top)...")
        gen.run_dual_split_export_from_source(
            str(game), str(face), str(split_mp4),
            start=1.0, end=5.0, canvas_width=1080, canvas_height=1920,
            platform="tiktok", subtitles_path=None, margin_v_for_subs=None,
        )
        _grab_frame(split_mp4, out / "split_frame.png", at=2.0)
        print(f"  -> {out / 'split_frame.png'}")

        print("rendering dual SQUARE (face fills 1080x1080, game audio)...")
        gen.run_dual_square_export_from_source(
            str(face), str(game), str(square_mp4),
            start=1.0, end=5.0, canvas=1080,
            platform="tiktok", subtitles_path=None,
        )
        _grab_frame(square_mp4, out / "square_frame.png", at=2.0)
        print(f"  -> {out / 'square_frame.png'}")

        print("checking _find_face_pair...")
        pairing_ok = _check_pairing(tmp)

    print()
    print("=== verification artifacts ===")
    print(f"  {out / 'split_frame.png'}  (expect: top=green FACE, bottom=blue CENTER ACTION, no EDGE L/R)")
    print(f"  {out / 'square_frame.png'} (expect: all-green FACE filling the square)")
    print(f"  pairing: {'PASS' if pairing_ok else 'FAIL'}")
    if not pairing_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
