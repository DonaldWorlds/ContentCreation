import keyboard

class MarkerIngestWorker:
    """Listens for hotkeys (F8) and delegates marker creation."""

    def __init__(self, state, marker_service):
        self.state = state
        self.marker_service = marker_service

    def start(self):
        print("Marker listener ready F8")
        keyboard.add_hotkey("F8", self.handle_marker_hotkey)
        keyboard.wait()

    def handle_marker_hotkey(self):
        """Triggered when F8 is pressed."""
        self.marker_service.create_marker()