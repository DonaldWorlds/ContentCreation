from dataclasses import dataclass
from typing import Literal

# ManualPostService is the application service for manual posting.
# It validates input, builds a ManualPostJob, and prepares it for queueing.
# The dataclass holds the data; this service creates it.

@dataclass
class ManualPostJob:
    source: str
    content: str
    platform: str
    account_id: str
    media_type: Literal["image", "video"]
    mode: str = "manual"


class ManualPostService:
    def __init__(self, mode: str = "manual"):
        self.mode = mode

    def build_job(self, source: str, content: str, platform: str, account_id: int, media_type: str, ) -> ManualPostJob:
        """Build the Zernio payload for a manual post."""
        
        if not (isinstance(account_id, str) and len(account_id) == 24):
            raise ValueError("account_id must be a 24-char string from client.accounts.list()")

        return ManualPostJob(
            source=source,
            content=content,
            platform=platform,
            account_id=account_id,
            media_type=media_type,
            mode=self.mode,
        )