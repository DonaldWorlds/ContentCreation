"""CP2 (RED): fuse merges multi-source events onto one timeline, sorted by t, stable."""
from zerino.detection.core.fuse import fuse


def test_fuse_merges_and_sorts_by_t(ev):
    out = fuse([ev(5.0, source="audio"), ev(1.0, source="ocr"), ev(3.0, source="audio")])
    assert [e.t for e in out] == [1.0, 3.0, 5.0]


def test_fuse_is_stable_for_equal_t(ev):
    a = ev(2.0, source="ocr")
    b = ev(2.0, source="audio")
    assert fuse([a, b]) == [a, b]
