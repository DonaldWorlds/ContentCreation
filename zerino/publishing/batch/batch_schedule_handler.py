from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[3]
source_root = BASE_DIR / "clip_engine" / "exports"


logger = logging.getLogger(__name__)

MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class BatchItem:
    """
    Normalized unit of work produced by the handler.

    - platform: inferred from folder path (instagram/tiktok/youtube/etc.)
    - path: local filesystem path after we move it into destination_root
    - metadata: optional extra data for later layers (service/publisher)
    """
    platform: str
    path: Path
    metadata: dict[str, Any] | None = None


class BatchScheduleHandler:
    """
    Discovers exported media files, dedupes them, and moves them into a stable destination folder.

    Responsibilities:
    - Scan source_root for media exports
    - Ignore non-media files
    - Skip files we've already processed (via processed_history_file)
    - Move new files into destination_root/<platform>/
    - Return a list[BatchItem] for planner/service to schedule + convert into PublishJobs
    """

    def __init__(
        self,
        source_root: str = source_root,
        destination_root: str = "content_exports",
        processed_history_file: str = "content_exports/processed_history.json",
        allowed_platform_folders: Iterable[str] | None = ("instagram", "tiktok", "youtube"),
    ) -> None:
        self.source_root = Path(source_root)
        self.destination_root = Path(destination_root)
        self.processed_history_file = Path(processed_history_file)
        self.allowed_platform_folders = tuple(p.lower() for p in allowed_platform_folders) if allowed_platform_folders else None

        self.processed_history: set[str] = self._load_processed_history()

    # -----------------------
    # History / dedupe
    # -----------------------
    def _load_processed_history(self) -> set[str]:
        if not self.processed_history_file.exists():
            logger.info("No processed history file found. Starting fresh: %s", self.processed_history_file)
            return set()

        try:
            with self.processed_history_file.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                history = set(map(str, data))
            elif isinstance(data, dict):
                history = set(map(str, data.get("processed", [])))
            else:
                history = set()

            logger.info("Loaded %d processed items.", len(history))
            return history
        except Exception:
            logger.exception("Failed to load processed history: %s", self.processed_history_file)
            return set()

    def _save_processed_history(self) -> None:
        self.processed_history_file.parent.mkdir(parents=True, exist_ok=True)
        with self.processed_history_file.open("w", encoding="utf-8") as f:
            json.dump(sorted(self.processed_history), f, indent=2)
        logger.info("Saved %d processed items -> %s", len(self.processed_history), self.processed_history_file)

    def _fingerprint(self, path: Path) -> str:
        """
        Lightweight fingerprint so re-renders at the same path count as "new".

        Uses: absolute path + size + mtime
        """
        st = path.stat()
        return f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"

    # -----------------------
    # Scanning
    # -----------------------
    def _is_media_file(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in MEDIA_EXTS

    def scan_export_folder(self) -> list[Path]:
        if not self.source_root.exists():
            logger.warning("Source root does not exist: %s", self.source_root)
            return []

        # If allowed_platform_folders is set, only scan those; otherwise scan everything.
        scan_roots: list[Path] = []
        if self.allowed_platform_folders:
            for name in self.allowed_platform_folders:
                folder = self.source_root / name
                if folder.exists():
                    scan_roots.append(folder)
                else:
                    logger.warning("Missing export folder: %s", folder)
        else:
            scan_roots = [self.source_root]

        files: list[Path] = []
        for folder in scan_roots:
            logger.info("Scanning folder: %s", folder)
            for path in folder.rglob("*"):
                if self._is_media_file(path):
                    files.append(path)

        logger.info("Scan complete. Found %d media file(s).", len(files))
        return files

    def is_new_clip(self, path: Path) -> bool:
        fp = self._fingerprint(path)
        is_new = fp not in self.processed_history
        logger.debug("%s media: %s", "New" if is_new else "Old", path)
        return is_new

    def get_new_export_files(self) -> list[Path]:
        all_files = self.scan_export_folder()
        new_files = [p for p in all_files if self.is_new_clip(p)]
        logger.info("Found %d new media file(s).", len(new_files))
        return new_files

    # -----------------------
    # Platform + moving
    # -----------------------
    def detect_platform(self, path: Path) -> str:
        parts = {part.lower() for part in path.parts}
        for p in (self.allowed_platform_folders or ()):
            if p in parts:
                return p
        # fallback heuristics
        if "instagram" in parts:
            return "instagram"
        if "tiktok" in parts:
            return "tiktok"
        if "youtube" in parts:
            return "youtube"
        return "unknown"

    def ensure_platform_folder(self, platform: str) -> Path:
        target_folder = self.destination_root / platform
        target_folder.mkdir(parents=True, exist_ok=True)
        return target_folder

    def _avoid_overwrite_path(self, target_path: Path) -> Path:
        """
        If a file already exists, append _1, _2, ... to avoid overwriting.
        """
        if not target_path.exists():
            return target_path

        stem = target_path.stem
        suffix = target_path.suffix
        parent = target_path.parent

        i = 1
        while True:
            candidate = parent / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    def move_clip(self, path: Path) -> Path:
        platform = self.detect_platform(path)
        target_folder = self.ensure_platform_folder(platform)

        target_path = self._avoid_overwrite_path(target_folder / path.name)
        shutil.move(str(path), str(target_path))
        logger.info("Moved media %s -> %s", path, target_path)
        return target_path

    # -----------------------
    # Public API
    # -----------------------
    def process_exports(self) -> list[BatchItem]:
        """
        Main entrypoint: find new exported media, move it, persist history, return BatchItems.
        """
        items: list[BatchItem] = []
        new_files = self.get_new_export_files()

        for src_path in new_files:
            fp = None
            try:
                fp = self._fingerprint(src_path)
                moved_path = self.move_clip(src_path)

                # mark processed using fingerprint of the *source* at time of discovery
                self.processed_history.add(fp)

                items.append(BatchItem(platform=self.detect_platform(moved_path), path=moved_path, metadata=None))
            except Exception as e:
                logger.exception("Failed processing media %s (fp=%s): %s", src_path, fp, e)

        self._save_processed_history()
        logger.info("Processed %d new item(s).", len(items))
        return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    handler = BatchScheduleHandler()
    items = handler.process_exports()
    print(items)