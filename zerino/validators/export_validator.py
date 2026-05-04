from pathlib import Path
import os
import subprocess

class ExportValidator:

    # -------------------------
    # 🔷 1. VALIDATE CLIP INPUT
    # -------------------------
    def validate_clip_input(self, file_path):
        """
        Ensures:
        - file exists
        - readable
        - valid video
        - has duration
        - not corrupted

        Returns:
        metadata dict: {"duration": seconds, "width": int, "height": int}

        Raises:
        Exception if invalid
        """

        path = Path(file_path)

        if not path.exists() or not path.is_file():
            raise Exception(f"File not found: {file_path}")

        # Optionally, use ffprobe to check duration & resolution
        try:
            print(f"[VALIDATOR] File OK: {file_path}")
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(file_path)
                ],
                capture_output=True,
                text=True,
                check=True
            )

            lines = result.stdout.strip().split("\n")
            width, height, duration = map(float, lines)
            metadata = {
                "duration": duration,
                "width": int(width),
                "height": int(height)
            }

        except Exception as e:
            raise Exception(f"Invalid or corrupted video: {file_path}") from e

        return metadata
    
    def enforce_duration_rules(self, duration, platform):
        """
        Ensures clip meets platform duration limits.

        TikTok / Instagram → minimum 25s
        YouTube → minimum 25s, maximum 180s

        Returns:
        {"valid": True, "duration": duration}

        Raises:
        Exception if invalid
        """
        limits = {
            "tiktok": 25,
            "instagram": 25,
            "youtube": 180
        }

        max_duration = limits.get(platform.lower(), 25)

        duration = round(float(duration), 2)

        if duration < 25:
            raise Exception(f"Clip too short: {duration:.2f}s")

        if platform.lower() == "youtube" and duration > max_duration:
            raise Exception(f"Clip too long for {platform}: {duration:.2f}s")

        return {"valid": True, "duration": duration}
