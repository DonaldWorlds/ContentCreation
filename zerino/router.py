"""Router: given a clip and a list of platforms, dispatch each to the right
processor.

System 1 ("volume" pipeline) supports the four short-form platforms

    tiktok | youtube_shorts | facebook_reels | twitter

in three layouts:

    vertical (1080x1920 9:16)         →  VerticalProcessor
    square   (1080x1080 1:1)          →  SquareProcessor
    split    (1080x1920 face + game)  →  SplitProcessor

Layout selection is either per-clip (ClipJob.layout, set from the marker
kind — F8 talking_head → square, F9 gameplay → split) or per-account
(accounts.layout column) when the job doesn't pin one. Both pathways
flow through `route_clip_job(targets=...)`.
"""

from __future__ import annotations

from pathlib import Path

from zerino.config import RENDERS_DIR, get_logger
from zerino.models import ClipJob
from zerino.processors.base import ProcessorResult
from zerino.processors.split import SPLIT_PLATFORMS, SplitProcessor
from zerino.processors.square import SQUARE_PLATFORMS, SquareProcessor
from zerino.processors.vertical import VERTICAL_PLATFORMS, VerticalProcessor

PLATFORM_TO_TYPE: dict[str, str] = {p: "vertical" for p in VERTICAL_PLATFORMS}
# Valid (platform, layout) pairs. Today every short-form platform supports
# all three layouts; layout choice is the differentiator.
LAYOUT_TO_PLATFORMS: dict[str, tuple[str, ...]] = {
    "vertical": VERTICAL_PLATFORMS,
    "square":   SQUARE_PLATFORMS,
    "split":    SPLIT_PLATFORMS,
}


class Router:
    def __init__(self):
        self.log = get_logger("zerino.router")
        self._vertical: VerticalProcessor | None = None
        self._square: SquareProcessor | None = None
        self._split: SplitProcessor | None = None

    def _processor_for(self, platform: str, layout: str = "vertical"):
        """Return (processor, posting_type) for the given (platform, layout).

        `layout` defaults to 'vertical' to preserve the old single-arg
        signature for the legacy `route()` path. The new layout-aware
        callers pass it explicitly.
        """
        platform = platform.lower()
        allowed = LAYOUT_TO_PLATFORMS.get(layout)
        if allowed is None:
            raise ValueError(
                f"unknown layout: {layout!r}. known: {sorted(LAYOUT_TO_PLATFORMS)}"
            )
        if platform not in allowed:
            raise ValueError(
                f"platform {platform!r} does not support layout {layout!r}. "
                f"layout={layout!r} supports: {sorted(allowed)}"
            )

        if layout == "vertical":
            self._vertical = self._vertical or VerticalProcessor()
            return self._vertical, "vertical"
        if layout == "square":
            self._square = self._square or SquareProcessor()
            return self._square, "square"
        if layout == "split":
            self._split = self._split or SplitProcessor()
            return self._split, "split"
        raise AssertionError(f"unhandled layout: {layout!r}")

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

    def route_clip_job(
        self,
        job: ClipJob,
        targets: list[tuple[str, str]] | None = None,
    ) -> dict[tuple[str, str], ProcessorResult]:
        """One-pass render: render the clip's window from `job.source_path`
        for each (platform, layout) target.

        `targets` is a list of (platform, layout) pairs. If None, defaults
        to (platform, 'vertical') for every platform in `job.platforms`
        (backward-compatible behavior).

        Returns: {(platform, layout): ProcessorResult} for each target that
        succeeded. Per-target failures are logged and skipped — they do not
        abort the batch.
        """
        if targets is None:
            targets = [(p.lower(), "vertical") for p in job.platforms]

        # De-duplicate while preserving order — caller often passes (platform,
        # layout) pairs derived from many accounts that share renders.
        seen: set[tuple[str, str]] = set()
        unique_targets: list[tuple[str, str]] = []
        for platform, layout in targets:
            key = (platform.lower(), layout)
            if key in seen:
                continue
            seen.add(key)
            unique_targets.append(key)

        results: dict[tuple[str, str], ProcessorResult] = {}

        for platform, layout in unique_targets:
            try:
                processor, ptype = self._processor_for(platform, layout=layout)
                output_dir = RENDERS_DIR / platform
                self.log.info(
                    "route_clip_job: clip_id=%s src=%s [%.2fs-%.2fs] -> %s/%s (type=%s)",
                    job.clip_id, job.source_path.name, job.start, job.end,
                    platform, layout, ptype,
                )
                results[(platform, layout)] = processor.process_clip_job(
                    job, platform, output_dir,
                )
            except Exception as e:  # noqa: BLE001
                self.log.exception(
                    "route_clip_job failed for platform=%s layout=%s clip_id=%s: %s",
                    platform, layout, job.clip_id, e,
                )

        return results
