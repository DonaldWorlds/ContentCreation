"""The detection auto-post master kill-switch (ZERINO_DETECTION_AUTOPOST), default OFF.

This is the single gate that decides whether detection rides the existing
create_clips -> queue_clip_jobs_for_posting -> Zernio path F8/F9 uses. The routing itself
(render=True -> create_clips) is already covered by test_service_wiring; here we only pin
that the flag defaults OFF and only turns on for an explicit "1".
"""
import zerino.cli.detect as detect


def test_autopost_default_off(monkeypatch):
    monkeypatch.delenv(detect.AUTOPOST_ENV, raising=False)
    assert detect.autopost_enabled() is False


def test_autopost_off_for_zero_or_junk(monkeypatch):
    for val in ("0", "", "  ", "false", "no"):
        monkeypatch.setenv(detect.AUTOPOST_ENV, val)
        assert detect.autopost_enabled() is False


def test_autopost_on_only_for_one(monkeypatch):
    monkeypatch.setenv(detect.AUTOPOST_ENV, "1")
    assert detect.autopost_enabled() is True
    monkeypatch.setenv(detect.AUTOPOST_ENV, " 1 ")
    assert detect.autopost_enabled() is True
