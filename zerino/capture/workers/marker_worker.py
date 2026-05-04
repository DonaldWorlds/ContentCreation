"""Global hotkey listener that triggers marker creation.

Uses pynput (macOS Accessibility permission, no sudo) instead of the
`keyboard` library which requires root on macOS.

Default hotkey: F8. Override by passing hotkey="<ctrl>+<shift>+m" etc.
See https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys
"""

from __future__ import annotations

from pynput import keyboard

from zerino.config import get_logger


class MarkerIngestWorker:
    def __init__(self, state, marker_service, hotkey: str = "<f8>"):
        self.state = state
        self.marker_service = marker_service
        self.hotkey = hotkey
        self.log = get_logger("zerino.capture.marker_worker")

    def start(self) -> None:
        self.log.info("marker hotkey listener ready (hotkey=%s)", self.hotkey)
        with keyboard.GlobalHotKeys({self.hotkey: self._on_hotkey}) as listener:
            listener.join()

    def _on_hotkey(self) -> None:
        self.log.info("marker hotkey pressed")
        try:
            self.marker_service.create_marker()
        except Exception:  # noqa: BLE001
            self.log.exception("marker creation failed")
