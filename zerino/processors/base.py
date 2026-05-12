"""Processor interface for the three v1 posting types.

Each posting type (vertical, horizontal, image) has its own processor with
distinct processing logic. This is intentional — they are not parameterized
variants of one renderer. See memory: zerino_posting_types.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProcessorResult:
    """What a processor returns: the primary output plus any sidecar files."""
    output_path: Path
    sidecars: dict[str, Path] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class Processor:
    """Base class for render processors.

    Subclasses MUST set `posting_type` and implement `process_clip_job`
    (the active one-pass-from-source render path). `process` is the legacy
    cut-file-in entry point — kept as a stub on the base so subclasses that
    only support the modern path (SquareProcessor, SplitProcessor) can still
    instantiate. Override it if you need cut-file-in support.
    """

    posting_type: str = ""

    def process(self, input_path: Path | str, platform: str, output_dir: Path | str) -> ProcessorResult:
        """Legacy entry point. Raise unless the subclass overrides."""
        raise NotImplementedError(
            f"{type(self).__name__} only supports process_clip_job (the active path). "
            f"Use Router.route_clip_job instead of the legacy Router.route."
        )
