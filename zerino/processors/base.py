"""Processor interface for the three v1 posting types.

Each posting type (vertical, horizontal, image) has its own processor with
distinct processing logic. This is intentional — they are not parameterized
variants of one renderer. See memory: zerino_posting_types.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProcessorResult:
    """What a processor returns: the primary output plus any sidecar files."""
    output_path: Path
    sidecars: dict[str, Path] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class Processor(ABC):
    """Base class. Subclasses MUST set `posting_type` and implement `process`."""

    posting_type: str = ""

    @abstractmethod
    def process(self, input_path: Path | str, platform: str, output_dir: Path | str) -> ProcessorResult:
        """Render the input clip for the given platform; return ProcessorResult."""
        raise NotImplementedError
