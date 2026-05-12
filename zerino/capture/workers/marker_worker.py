"""Global hotkey listener that triggers marker creation.

Uses pynput (macOS Accessibility permission, no sudo) instead of the
`keyboard` library which requires root on macOS.

Two hotkeys are registered, distinguished by clip kind:
    F8 = talking_head  → renders as a 1:1 square fill (face-only).
    F9 = gameplay      → renders as a 9:16 split (face on top + game on bottom).

The kind is stored on the marker row and read downstream in ClipService
to set ClipJob.layout, which drives Router → Processor selection.
"""

from __future__ import annotations

from pynput import keyboard

from zerino.config import get_logger


class MarkerIngestWorker:
    def __init__(
        self,
        state,
        marker_service,
        talking_hotkey: str = "<f8>",
        gameplay_hotkey: str = "<f9>",
    ):
        self.state = state
        self.marker_service = marker_service
        self.talking_hotkey = talking_hotkey
        self.gameplay_hotkey = gameplay_hotkey
        self.log = get_logger("zerino.capture.marker_worker")

    def start(self) -> None:
        self.log.info(
            "marker hotkey listener ready (talking=%s, gameplay=%s)",
            self.talking_hotkey, self.gameplay_hotkey,
        )
        hotkeys = {
            self.talking_hotkey: lambda: self._on_hotkey("talking_head"),
            self.gameplay_hotkey: lambda: self._on_hotkey("gameplay"),
        }
        with keyboard.GlobalHotKeys(hotkeys) as listener:
            listener.join()

    def _on_hotkey(self, kind: str) -> None:
        self.log.info("marker hotkey pressed (kind=%s)", kind)
        try:
            self.marker_service.create_marker(kind=kind)
        except Exception:  # noqa: BLE001
            self.log.exception("marker creation failed (kind=%s)", kind)
