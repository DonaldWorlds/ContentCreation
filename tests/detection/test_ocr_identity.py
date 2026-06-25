"""CP2 (RED): elim-feed parsing + the "my events only" identity filter (Decision 1).

No media — operates on text the OCR layer produces. The identity filter must (a) accept
the operator's own (color-highlighted) elims, (b) accept a fuzzy/mangled OCR of the
gamertag, (c) accept known aliases, and (d) REJECT a squadmate/enemy eliminator.
Reds on NotImplementedError until CP3.
"""
from zerino.detection.ocr import parse_feed_lines, is_own_event

IDENTITY = {"gamertag": "kkthedon_", "aliases": ["for3v3ronyt"]}


def test_parse_feed_extracts_eliminator_verb_victim():
    rows = parse_feed_lines("kkthedon_ eliminated AngelEye30 with a rifle")
    assert rows and rows[0]["eliminator"].lower().startswith("kkthedon")
    assert rows[0]["verb"] == "eliminated"
    assert "angeleye30" in rows[0]["victim"].lower()


def test_own_event_accepts_highlighted_self():
    assert is_own_event("kkthedon_", highlighted=True, identity=IDENTITY) is True


def test_own_event_accepts_fuzzy_ocr_of_gamertag():
    # raw Tesseract mangled kkthedon_ -> "Kothedon" (calibration evidence)
    assert is_own_event("Kothedon", highlighted=True, identity=IDENTITY) is True


def test_own_event_accepts_known_alias():
    assert is_own_event("for3v3ronyt", highlighted=True, identity=IDENTITY) is True


def test_own_event_rejects_squadmate():
    assert is_own_event("DTN_HADES", highlighted=False, identity=IDENTITY) is False
