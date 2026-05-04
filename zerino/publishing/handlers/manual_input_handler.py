from pathlib import Path 
from PIL import Image
import mimetypes

class ManualInputHandler:
    """
    Handle manual-mode input for the posting system.

    This class accepts local files, pasted URLs, or staging-folder clips,
    validates them, normalizes them into one format, detects media type,
    builds a Zernio-ready post payload, and sends the post request.
    """

    def get_manual_input_source(self):
        """Get the source of manual input from the user or calling code."""
        return input("Enter image or video file path: ").strip()
    

    def validate_input_path(self, file_path):
        """Check that a local file path exists and is usable for posting."""
        path = Path(file_path)
        return path.exists() and path.is_file()
    

    def validate_input_image(self, file_path):
        """Check that the file exists and is a valid image."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return False
        try:
            with Image.open(path) as img:
                img.verify()
            return True
        except Exception:
            return False
        
    def validate_input_video(self, file_path):
        """Check that the file exists and looks like a valid video."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return False
        mime_type, _ = mimetypes.guess_type(str(path))
        return bool(mime_type and mime_type.startswith("video/"))
    

    def normalize_manual_input(self, input_value):
        """Convert the input into one standard internal format."""
        return str(Path(input_value).expanduser().resolve())
    

    def detect_media_type(self, file_path_or_url):
        """Detect whether the input is an image or video."""
        mime_type, _ = mimetypes.guess_type(str(file_path_or_url))
        if not mime_type:
            return None
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"
        return None

   

