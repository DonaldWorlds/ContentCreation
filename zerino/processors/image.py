"""Image processor: Pinterest pins.

Pulls one or more frames from a clip and resizes them to Pinterest pin
spec (1000x1500). A "carousel" is N frames extracted at evenly-spaced
timestamps across the clip; each is center-cropped + Lanczos-resized.

Instagram feed (1080x1080) was dropped per v1 scope: Instagram in this
project = Reels (vertical video), not images.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from zerino.composition.composition_rules import get_platform_preset
from zerino.config import get_logger
from zerino.ffmpeg.ffmpeg_utils import probe_metadata
from zerino.processors.base import Processor, ProcessorResult

IMAGE_PLATFORMS = ("pinterest",)
DEFAULT_CAROUSEL_COUNT = 3


class ImageProcessor(Processor):
    posting_type = "image"

    def __init__(self):
        self.log = get_logger("zerino.processors.image")

    def _extract_frame(self, input_path: Path, timestamp: float, output_path: Path) -> None:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp),
            "-i", str(input_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_path),
        ]
        self.log.debug("extracting frame at %.2fs -> %s", timestamp, output_path)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg frame extract failed:\n{result.stderr}")

    def _fit_to_canvas(self, image_path: Path, target_w: int, target_h: int) -> None:
        """Center-crop + resize the image in-place to the target canvas."""
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            src_w, src_h = img.size
            target_ratio = target_w / target_h
            src_ratio = src_w / src_h

            if src_ratio > target_ratio:
                # source wider — crop width
                new_w = int(src_h * target_ratio)
                left = (src_w - new_w) // 2
                box = (left, 0, left + new_w, src_h)
            else:
                # source taller — crop height
                new_h = int(src_w / target_ratio)
                top = (src_h - new_h) // 2
                box = (0, top, src_w, top + new_h)

            cropped = img.crop(box)
            resized = cropped.resize((target_w, target_h), Image.LANCZOS)
            resized.save(image_path, format="JPEG", quality=92, optimize=True)

    def process(self, input_path: Path | str, platform: str, output_dir: Path | str) -> ProcessorResult:
        return self.process_carousel(input_path, platform, output_dir, count=1)

    def process_carousel(
        self,
        input_path: Path | str,
        platform: str,
        output_dir: Path | str,
        count: int = DEFAULT_CAROUSEL_COUNT,
    ) -> ProcessorResult:
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        platform = platform.lower()
        if platform not in IMAGE_PLATFORMS:
            raise ValueError(
                f"ImageProcessor does not support platform={platform!r}. "
                f"Supported: {IMAGE_PLATFORMS}"
            )
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")

        preset = get_platform_preset(platform)
        target_w = preset["canvas_width"]
        target_h = preset["canvas_height"]

        meta = probe_metadata(input_path)
        duration = meta.get("duration") or 0.0
        if duration <= 0:
            raise RuntimeError(f"could not determine duration for {input_path}")

        self.log.info(
            "image render start: %s platform=%s count=%d canvas=%dx%d",
            input_path.name, platform, count, target_w, target_h,
        )

        # Pick timestamps evenly spaced through the middle 80% of the clip
        # (skip the first and last 10% to avoid black/in-out frames).
        margin = duration * 0.10
        usable = duration - 2 * margin
        if count == 1:
            timestamps = [duration / 2]
        else:
            step = usable / (count - 1)
            timestamps = [margin + step * i for i in range(count)]

        outputs: list[Path] = []
        for i, ts in enumerate(timestamps):
            out = output_dir / f"{input_path.stem}__{platform}_{i+1:02d}.jpg"
            self._extract_frame(input_path, ts, out)
            self._fit_to_canvas(out, target_w, target_h)
            outputs.append(out)

        # Primary output = first image; rest are sidecars (carousel slides)
        primary = outputs[0]
        sidecars = {f"slide_{i+1}": p for i, p in enumerate(outputs[1:], start=1)}

        self.log.info("image render done: %d frames -> %s", len(outputs), output_dir)
        return ProcessorResult(
            output_path=primary,
            sidecars=sidecars,
            metadata={"platform": platform, "frame_count": len(outputs), "canvas": (target_w, target_h)},
        )
