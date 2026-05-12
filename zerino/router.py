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
from zerino.processors._captions import (
    has_subtitles_filter,
    transcribe_source_to_segments,
)
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
    ) -> dict[str, ProcessorResult]:
        """One-pass render: render the clip's window from `job.source_path`
        ONCE per unique layout, regardless of how many platforms share it.

        `targets` is still a list of (platform, layout) pairs (the call
        shape callers think in), but the actual render fans out by LAYOUT
        only — same layout = same render bytes, so producing it once and
        having every TikTok / YT Shorts / Reels / Twitter post for that
        layout reference the same file saves 3-4x encode time and disk on
        multi-platform fan-outs of identical content.

        `targets` defaults to (platform, 'vertical') for every platform in
        job.platforms (legacy behavior).

        Returns: {layout: ProcessorResult} for each unique layout that
        succeeded. Per-layout failures are logged and skipped — they do
        not abort the batch.
        """
        if targets is None:
            targets = [(p.lower(), "vertical") for p in job.platforms]

        # De-duplicate by LAYOUT. Each platform sharing that layout reads
        # the same render. We still remember one "representative platform"
        # per layout to pass to the processor's existing signature (it uses
        # it only for the supported-platforms guard, not for the encode).
        seen_layouts: set[str] = set()
        unique_targets: list[tuple[str, str]] = []
        for platform, layout in targets:
            if layout in seen_layouts:
                continue
            seen_layouts.add(layout)
            unique_targets.append((platform.lower(), layout))

        # Whisper-once cache: transcribe a single audio slice and stash the
        # karaoke segments on the job so each (platform, layout) target writes
        # its own .ass from cached segments instead of re-running Whisper. The
        # processors check job.metadata['karaoke_segments'] first and only fall
        # back to per-target transcription if it's missing.
        if (
            unique_targets
            and has_subtitles_filter()
            and "karaoke_segments" not in job.metadata
            and job.transcript_path is None
        ):
            try:
                segments = transcribe_source_to_segments(
                    job.source_path, job.start, job.end,
                )
                job.metadata["karaoke_segments"] = segments
                self.log.info(
                    "route_clip_job: transcribed once for %d target(s) — %d karaoke line(s) cached",
                    len(unique_targets), len(segments),
                )
            except Exception as e:  # noqa: BLE001
                # Don't fail the whole batch if one shared transcription
                # blows up — let each processor fall back to its own.
                self.log.warning(
                    "route_clip_job: shared transcription failed (%s); "
                    "processors will transcribe per-target",
                    e,
                )

        results: dict[str, ProcessorResult] = {}

        for platform, layout in unique_targets:
            try:
                processor, ptype = self._processor_for(platform, layout=layout)
                # Layout-keyed output dir — one render shared by every
                # platform on that layout. (Old per-platform dirs are
                # orphaned in place; cleanup CLI removes them by age.)
                output_dir = RENDERS_DIR / layout
                self.log.info(
                    "route_clip_job: clip_id=%s src=%s [%.2fs-%.2fs] -> layout=%s (type=%s, rep_platform=%s)",
                    job.clip_id, job.source_path.name, job.start, job.end,
                    layout, ptype, platform,
                )
                results[layout] = processor.process_clip_job(
                    job, platform, output_dir,
                )
            except Exception as e:  # noqa: BLE001
                self.log.exception(
                    "route_clip_job failed for layout=%s clip_id=%s: %s",
                    layout, job.clip_id, e,
                )

        return results
