"""Shared dataclasses that flow between capture, render, and publishing.

These are the contracts between layers — keeping the data shape explicit
prevents the older pattern of each layer re-deriving info from filenames
or re-probing the source. If a piece of information is computed once
(e.g. transcript path, source duration), the dataclass carries it
downstream instead of repeating the work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ClipJob:
    """A logical clip (source + range) flowing through the render+post pipeline.

    Replaces the older tuple-passing between capture → publishing → render
    layers. Constructed by `ClipService` (F8-marker flow) or by the
    file-handoff CLI; consumed by `Router.route_clip_job`.

    Fields:
        clip_id        Database row id of the clip. Used for log correlation.
        source_path    The full source recording or handed-off file.
        start, end     Seconds into `source_path` for the clip window.
        platforms      Lowercased platform identifiers to render and post to.
                       Fan-out happens per platform.
        transcript_path  Optional path to a pre-computed .ass caption file.
                       If set, the processor skips transcription and uses
                       this file. If None, the processor extracts an audio
                       slice and runs Whisper itself. Used by a future
                       whole-source pre-transcribe step.
        caption        Text body for the post. Empty string means the
                       queue function picks one from the captions pool.
        mode           "manual" (post immediately) or "scheduled".
        scheduled_for  When to publish. None means immediate.
        layout         Override the per-account layout column. When set,
                       every account for each platform receives the render
                       in this layout (e.g. 'square' for talking-head clips,
                       'split' for gameplay clips with face + game stacked).
                       None means honor each account's `layout` column.
        metadata       Free-form bag for processor outputs (segment counts,
                       caption-style version, etc.). Not persisted.
    """
    clip_id: int | None
    source_path: Path
    start: float
    end: float
    platforms: list[str] = field(default_factory=list)
    transcript_path: Path | None = None
    caption: str = ""
    mode: str = "manual"
    scheduled_for: datetime | None = None
    layout: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start
