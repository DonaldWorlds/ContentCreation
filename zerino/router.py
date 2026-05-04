"""Router: given a clip and a list of (posting_type, platform) targets,
dispatch each target to the right processor.

Posting type → platforms:
  vertical   → tiktok, youtube_shorts, instagram_reels
  horizontal → youtube, twitter
  image      → pinterest, instagram_feed
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zerino.config import RENDERS_DIR, get_logger
from zerino.processors.base import ProcessorResult
from zerino.processors.horizontal import HorizontalProcessor, HORIZONTAL_PLATFORMS
from zerino.processors.image import ImageProcessor, IMAGE_PLATFORMS
from zerino.processors.vertical import VerticalProcessor, VERTICAL_PLATFORMS

PLATFORM_TO_TYPE: dict[str, str] = {
    **{p: "vertical" for p in VERTICAL_PLATFORMS},
    **{p: "horizontal" for p in HORIZONTAL_PLATFORMS},
    **{p: "image" for p in IMAGE_PLATFORMS},
}


@dataclass
class Target:
    platform: str  # e.g. "tiktok", "youtube", "pinterest"


class Router:
    def __init__(self):
        self.log = get_logger("zerino.router")
        self._vertical: VerticalProcessor | None = None
        self._horizontal: HorizontalProcessor | None = None
        self._image: ImageProcessor | None = None

    def _processor_for(self, platform: str):
        ptype = PLATFORM_TO_TYPE.get(platform.lower())
        if ptype is None:
            raise ValueError(f"unknown platform: {platform!r}. known: {sorted(PLATFORM_TO_TYPE)}")

        if ptype == "vertical":
            self._vertical = self._vertical or VerticalProcessor()
            return self._vertical, ptype
        if ptype == "horizontal":
            self._horizontal = self._horizontal or HorizontalProcessor()
            return self._horizontal, ptype
        if ptype == "image":
            self._image = self._image or ImageProcessor()
            return self._image, ptype
        raise AssertionError(f"unhandled posting type: {ptype}")

    def route(self, input_path: Path | str, platforms: list[str]) -> dict[str, ProcessorResult]:
        """Run the input clip through every requested platform.

        Output dir per render is RENDERS_DIR / <platform> / .
        Returns: {platform: ProcessorResult} for each platform that succeeded.
        Failures are logged and skipped — they do not abort the batch.
        """
        input_path = Path(input_path)
        results: dict[str, ProcessorResult] = {}

        for platform in platforms:
            platform = platform.lower()
            try:
                processor, ptype = self._processor_for(platform)
                output_dir = RENDERS_DIR / platform
                self.log.info("route: clip=%s -> %s (type=%s)", input_path.name, platform, ptype)
                results[platform] = processor.process(input_path, platform, output_dir)
            except Exception as e:  # noqa: BLE001
                self.log.exception("route failed for platform=%s: %s", platform, e)

        return results
