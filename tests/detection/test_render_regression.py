"""NON-REGRESSION guard (characterization — green by design).

Pins the EXISTING render *recipe* (build_filter / build_audio_filter) for canonical
square + vertical jobs so the additive detection work can't silently change F8/F9
output. We pin the recipe, not raw bytes: libx264 default multithreading is
non-deterministic, so byte-identical output run-to-run is not achievable. This guard
only CALLS the quality-critical code; it never modifies it.
"""
from zerino.ffmpeg.export_generator import ExportGenerator
from zerino.composition.composition_rules import build_processing_config

CANON_MD = {
    "width": 1920, "height": 1080, "fps": 30.0, "duration": 30.0,
    "pix_fmt": None, "color_space": None, "color_primaries": None,
    "color_transfer": None, "color_range": None, "video_bit_rate": None,
    "audio_codec": None, "audio_sample_rate": None, "audio_channels": None,
    "audio_channel_layout": None, "audio_bit_rate": None,
}


def _vf(layout):
    cfg = build_processing_config(CANON_MD, platform="tiktok",
                                  style="talking_head", layout=layout)
    return ExportGenerator().build_filter(CANON_MD, cfg)


def test_square_filter_recipe_unchanged():
    assert _vf("square") == "crop=1080:1080:352:0,scale=1080:1080:flags=lanczos,setsar=1"


def test_vertical_filter_recipe_unchanged():
    assert _vf("vertical") == "crop=607:1080:551:0,scale=1080:1920:flags=bicubic,setsar=1"


def test_audio_filter_recipe_unchanged():
    assert ExportGenerator().build_audio_filter(30.0) == (
        "afade=t=in:st=0:d=0.05,highpass=f=80,dynaudnorm=f=200:g=15:p=0.95,"
        "alimiter=limit=0.95,afade=t=out:st=29.800:d=0.2"
    )
