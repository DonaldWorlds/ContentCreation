from zerino.db.repositories.clip_repository import ClipRepository
from zerino.db.repositories.export_repository import ExportRepository
from zerino.validators.export_validator import ExportValidator
from zerino.ffmpeg.export_generator import ExportGenerator
from zerino.capture.services.queue_service import PipelineQueueService

from pathlib import Path
import os


EXPORTS_ROOT = "exports"
PLATFORMS = ["tiktok", "instagram", "youtube"]


class ExportService:
    def __init__(self, pipeline_queue_service=None):
        self.clip_repo = ClipRepository()
        self.export_repo = ExportRepository()
        self.validator = ExportValidator()
        self.processor = ExportGenerator()
        self.ensure_platforms()
        self.pipeline_queue_service = pipeline_queue_service or PipelineQueueService()
    # ensures the folders exist before any export is created
    def ensure_platforms(self):
        for platform in PLATFORMS:
            folder_path = os.path.join(EXPORTS_ROOT, platform)
            os.makedirs(folder_path, exist_ok=True)
        print(f"[ExportService] Platform folders ensured: {PLATFORMS}")

    # -------------------------
    # 1. ENTRY POINT
    # -------------------------
    def process_export(self, export_id):
        try:
            print(f"[EXPORT] Processing {export_id}")

            export, clip = self.get_export_with_clip(export_id)

            if clip["status"] != "completed":
                raise Exception(f"Clip not ready: {clip['id']}")

            platform = export["platform"]

            if platform not in PLATFORMS:
                raise ValueError(f"Invalid platform: {platform}")

            video_file = clip["output_path"] or clip["video_file"]  

            if not video_file:
                raise ValueError(f"Missing video file for clip {clip['id']}")

            video_path = Path(video_file)

            print(f"[DEBUG] video_path: {video_path}")
            print(f"[DEBUG] exists: {video_path.exists()}")

            if not video_path.exists():
                raise Exception(f"File not found: {video_path}")

            metadata = self.validator.validate_clip_input(video_path)

            self.validator.enforce_duration_rules(
                metadata["duration"],
                platform
            )

            self.mark_export_processing(export_id)

            output_path = self.generate_output_path(export, platform)

            self.processor.run_export(
                str(video_path),
                str(output_path),
                platform=platform
            )
            self.mark_export_completed(export_id, output_path)

            print(f"[DB] Export {export_id} marked completed -> {output_path}")

        except Exception as e:
            self.mark_export_failed(export_id, str(e))
            print(f"[EXPORT FAILED] Export ID {export_id}: {e}")

    # -------------------------
    # 2. DATA FETCHING
    # -------------------------
    def get_export_with_clip(self, export_id):
        export = self.export_repo.get_export_by_id(export_id)
        if not export:
            raise Exception("Export not found")

        clip_id = export["clip_id"]
        print(clip_id)
        clip = self.clip_repo.get_clip_by_id(clip_id)

        if not clip:
            raise Exception("Clip not found")

        return export, clip
    
    def process_exports_for_clip(self, clip_id):
        try:
            print(f"[EXPORT] Processing exports for clip {clip_id}")

            clip = self.clip_repo.get_clip_by_id(clip_id)
            if not clip:
                raise Exception(f"Clip not found: {clip_id}")

            self.create_pending_exports_for_clip(clip_id)
            exports = self.export_repo.get_exports_by_clip(clip_id)

            if not exports:
                print(f"[EXPORT] No pending exports for clip {clip_id}")
                return

            for export in exports:
                self.pipeline_queue_service.enqueue_export_ready(export["id"])
             
        except Exception as e:
            print(f"[EXPORT FAILED] Clip {clip_id}: {e}")
    
   
    def build_processing_config(self, platform):
        return {
            "platform": platform,
            "encoding": {
                "video_codec": "libx264",
                "audio_codec": "aac",
                "fps": 60,
                "crf": 23,
                "preset": "fast"
            }
        }

    # -------------------------
    # 5. OUTPUT
    # -------------------------
    def generate_output_path(self, export, platform):
        base_dir = Path(EXPORTS_ROOT) / platform
        base_dir.mkdir(parents=True, exist_ok=True)

        clip_id = export["clip_id"]
        filename = f"Clip_{clip_id}_{platform}.mp4"
        output_path = base_dir / filename

        return str(output_path)

    # -------------------------
    # 6. STATUS MANAGEMENT
    # -------------------------
    def mark_export_processing(self, export_id):
        self.export_repo.mark_processing(export_id)

    def mark_export_completed(self, export_id, file_path):
        self.export_repo.mark_completed(export_id, file_path)

    def mark_export_failed(self, export_id, error_message):
        self.export_repo.mark_failed(export_id, str(error_message))

    # -------------------------
    # 7. ERROR HANDLING
    # -------------------------
    def handle_export_failure(self, export_id, error):
        error_message = str(error)
        print(f"[EXPORT FAILED] Export ID {export_id}: {error_message}")
        self.mark_export_failed(export_id, error_message)

    def create_pending_exports_for_clip(self, clip_id: int):
        for platform in PLATFORMS:
            # Only create if it doesn't exist
            if not self.export_repo.export_exists(clip_id, platform):
                self.export_repo.create_export(clip_id, platform)

   

