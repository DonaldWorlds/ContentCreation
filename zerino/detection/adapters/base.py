"""DetectorAdapter contract (Phase 1 interface; real adapters land in Phase 2/3)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from zerino.detection.events import Event
from zerino.detection.profile import GameProfile


class DetectorAdapter(ABC):
    game_id: str
    detector_version: str

    @abstractmethod
    def detect(self, media, profile: GameProfile) -> list[Event]:
        """Return events ALREADY identity-filtered to the operator (Decision 1).
        The core never sees enemy elims / opponent baskets."""
        ...
