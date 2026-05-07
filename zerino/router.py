"""Router: given a clip and a list of platforms, dispatch each to the right
processor.

System 1 ("volume" pipeline) supports four short-form vertical platforms
that all share the same 1080x1920 render:

    tiktok | youtube_shorts | facebook_reels | twitter   →   vertical

Higher-quality / image / long-form processors are deliberately out of
scope for this system. They're slated for the next system.
"""

from __future__ import annotations

from pathlib import Path

from zerino.config import RENDERS_DIR, get_logger
from zerino.processors.base import ProcessorResult
from zerino.processors.vertical import VERTICAL_PLATFORMS, VerticalProcessor

PLATFORM_TO_TYPE: dict[str, str] = {p: "vertical" for p in VERTICAL_PLATFORMS}


class Router:
    def __init__(self):
        self.log = get_logger("zerino.router")
        self._vertical: VerticalProcessor | None = None

    def _processor_for(self, platform: str):
        ptype = PLATFORM_TO_TYPE.get(platform.lower())
        if ptype is None:
            raise ValueError(
                f"unknown platform: {platform!r}. known: {sorted(PLATFORM_TO_TYPE)}"
            )
        if ptype == "vertical":
            self._vertical = self._vertical or VerticalProcessor()
            return self._vertical, ptype
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
                self.log.info(
                    "route: clip=%s -> %s (type=%s)",
                    input_path.name, platform, ptype,
                )
                results[platform] = processor.process(input_path, platform, output_dir)
            except Exception as e:  # noqa: BLE001
                self.log.exception("route failed for platform=%s: %s", platform, e)

        return results
