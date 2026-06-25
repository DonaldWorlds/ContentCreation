"""§4 NON-REGRESSION (GREEN by design) — extends the Phase-1 guard to the F9 / split path.

test_render_regression.py pins the square (F8) + vertical recipes via build_filter. The
split (F9) layout that detection emits (kind='gameplay' -> split) builds its own vstack
graph in run_split_export_from_source, so here we pin its STABLE, pure building blocks —
the split canvas preset + the split watermark position. If additive detection work ever
perturbs the F9 render recipe, this turns red. It only CALLS the render code; never edits it.
"""
from zerino.composition.composition_rules import get_platform_preset
from zerino.ffmpeg.export_generator import _watermark_overlay_position


def test_split_canvas_preset_unchanged():
    assert get_platform_preset("tiktok", "split") == {
        "canvas_width": 1080,
        "canvas_height": 1920,
        "aspect_ratio": "9:16",
        "safe_area": {"top": 120, "bottom": 260},
    }


def test_square_canvas_preset_unchanged():
    assert get_platform_preset("tiktok", "square") == {
        "canvas_width": 1080,
        "canvas_height": 1080,
        "aspect_ratio": "1:1",
        "safe_area": {"top": 90, "bottom": 150},
    }


def test_split_watermark_position_unchanged():
    # split watermark: horizontally centered, bottom edge at the vstack seam (H/2)
    assert _watermark_overlay_position("split") == "(W-w)/2:H/2-h"
