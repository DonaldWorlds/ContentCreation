"""Global hotkey listener that triggers marker creation.

Uses pynput (macOS Accessibility permission, no sudo) instead of the
`keyboard` library which requires root on macOS.

Two hotkeys are registered, distinguished by clip kind:
    F8 = talking_head  → renders as a 1:1 square fill (face-only).
    F9 = gameplay      → renders as a 9:16 split (face on top + game on bottom).

The kind is stored on the marker row and read downstream in ClipService
to set ClipJob.layout, which drives Router → Processor selection.

Listener health caveat — macOS Accessibility permission can be silently
revoked when:
  - System Settings → Privacy & Security → Accessibility entry is toggled
    off (or auto-removed during an OS update / Terminal upgrade)
  - The Python interpreter binary moves (e.g. venv recreated under a new
    path) — pynput's permission applies to the exact binary, not Python
    generally
  - macOS upgrade or login keychain reset

In those cases pynput's GlobalHotKeys silently stops delivering events
without raising — the listener thread is "alive" (CPU happy, no exception)
but no callback ever fires. To surface this, the worker emits a heartbeat
log line every HEARTBEAT_INTERVAL_SEC with a press counter. If the count
is stuck across multiple heartbeats while the user is pressing F8/F9, the
permission was revoked and they need to re-grant it.
"""

from __future__ import annotations

import threading
import time

from pynput import keyboard

from zerino.config import get_logger

# Heartbeat cadence — long enough not to spam the log, short enough that the
# user notices within a stream segment if presses stop registering.
HEARTBEAT_INTERVAL_SEC = 60.0


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
        self._press_count = 0
        self._stop_heartbeat = threading.Event()

    def _heartbeat_loop(self) -> None:
        """Periodically log press count so a silent pynput failure is visible.

        Runs in its own daemon thread. Exits cleanly when `_stop_heartbeat`
        is set. The thread sleeps in small slices instead of one long sleep
        so process shutdown doesn't have to wait the full interval.
        """
        last_logged_count = -1
        slept = 0.0
        while not self._stop_heartbeat.is_set():
            time.sleep(1.0)
            slept += 1.0
            if slept < HEARTBEAT_INTERVAL_SEC:
                continue
            slept = 0.0
            if self._press_count == last_logged_count:
                # No new presses since last heartbeat. Could be normal (user
                # not pressing) or could be pynput silently dead. Hint at
                # the diagnostic without crying wolf.
                self.log.info(
                    "marker hotkey listener heartbeat: presses=%d (no change since last beat — "
                    "if you've been pressing F8/F9, check System Settings -> "
                    "Privacy & Security -> Accessibility for the python binary)",
                    self._press_count,
                )
            else:
                self.log.info(
                    "marker hotkey listener heartbeat: presses=%d", self._press_count,
                )
            last_logged_count = self._press_count

    def start(self) -> None:
        self.log.info(
            "marker hotkey listener ready (talking=%s, gameplay=%s)",
            self.talking_hotkey, self.gameplay_hotkey,
        )
        # Heartbeat thread runs alongside the blocking pynput join — its
        # only job is to make a silent listener-death visible in the log.
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="marker-heartbeat",
        )
        heartbeat.start()

        hotkeys = {
            self.talking_hotkey: lambda: self._on_hotkey("talking_head"),
            self.gameplay_hotkey: lambda: self._on_hotkey("gameplay"),
        }
        try:
            with keyboard.GlobalHotKeys(hotkeys) as listener:
                listener.join()
        finally:
            self._stop_heartbeat.set()

    def _on_hotkey(self, kind: str) -> None:
        self._press_count += 1
        self.log.info("marker hotkey pressed (kind=%s, press#=%d)", kind, self._press_count)
        try:
            self.marker_service.create_marker(kind=kind)
        except Exception:  # noqa: BLE001
            self.log.exception("marker creation failed (kind=%s)", kind)
