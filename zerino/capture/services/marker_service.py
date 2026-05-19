import time
from zerino.db.repositories.marker_repository import MarkerRepository


class MarkerService:
    """Handles marker creation logic (validation, timestamp, DB writes).

    `kind` is the hotkey-derived clip type: 'talking_head' (F8, square render)
    or 'gameplay' (F9, split render). Drives layout selection downstream in
    ClipService → ClipJob → Router.
    """

    def __init__(self, state, lock=None):
        self.state = state
        self.marker_repo = MarkerRepository()
        self.lock = lock or state.get("lock")

    def create_marker(self, kind: str = "talking_head"):
        with self.lock:
            recording = self.state.get("current_recording")
            streamer_id = self.state.get("current_streamer_id")
            start_time = self.state.get("start_time")

            if not recording:
                print("[warn] Cannot create marker: no active recording")
                return

            recording_id = recording.get("id")

            if not streamer_id:
                print("[warn] Cannot create marker: no streamer set")
                return

            if not start_time:
                print("[warn] Cannot create marker: recording start time not set")
                return

            # Float, not int — F8 at 12.7s into a recording must produce
            # marker.timestamp=12.7, not 12. Truncating to integer seconds
            # shifted clip windows by up to a second, so the streamer's
            # actual reaction moment landed off-frame in the rendered clip.
            # The DB columns are SQLite REAL (or INTEGER-affinity, which also
            # accepts float — type affinity is permissive) so float values
            # round-trip cleanly.
            timestamp = time.time() - start_time

            marker_id = self.marker_repo.insert_marker(
                recording_id=recording_id,
                streamer_id=streamer_id,
                timestamp=timestamp,
                kind=kind,
                note=None,
            )

        print(f"[ok] Marker created @ {timestamp:.3f}s (ID: {marker_id}, kind: {kind})")