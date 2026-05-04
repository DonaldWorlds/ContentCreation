# app/handlers/recording_handler.py
from pathlib import Path
import time
from watchdog.events import FileSystemEventHandler
from zerino.capture.services.recording_service import RecordingService


RECORDINGS_PATH = Path("recordings")

class RecordingHandler(FileSystemEventHandler):
    """Watches the recording folder and forwards new files to RecordingService. 
    Responsibilities
        - Detect new files 
        - filter invalid files 
        - Pass valid files to RecordingService
        
        Does NOT:
            - Handle recording logic
            - Talk to database
            - Manage markers

    """
    def __init__(self, state, recording_service):
        super().__init__() # Initialize watchdog base class 
        self.state = state 
        self.recording_service = recording_service# correct dependency
    
    def on_created(self,event):
        """Triggered when a new file is created"""
        # Ignore directries 
        if event.is_directory:
            return 
        
        file_path = Path(self.extract_file_path(event))

        # Filter unwanted files 
        if self.should_ignore_file(file_path):
            return 
        
        print(f"New file detected: {file_path}")

        # delegate to service
        self.recording_service.handle_new_recording(file_path)

    def should_ignore_file(self, file_path):
        file_path = Path(file_path)
        allowed_extensions = {".mp4", ".mkv", ".mov"}

        if file_path.suffix.lower() not in allowed_extensions:
            return True

        return False


    def extract_file_path(self, event):
        """Extract file path from watchdog event"""
        return event.src_path









