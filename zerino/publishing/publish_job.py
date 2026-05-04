# PublishJob is the data/model layer: it carries all information needed to publish.
# Workers and publisher modules consume this model and perform the actual Zernio actions.
# app/models/publish_job.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


Mode = Literal["manual", "batch", "scheduled"]
MediaType = Literal["image", "video"]
TargetPlatform = Literal[
    "twitter/x"
]

'''
  "instagram",
    "youtube",
    "tiktok",
    "twitter",
    "x",
    "pinterest",
    "facebook",
    "linkedin",
    "reddit",
    "bluesky",
    "threads",
    "google_business",
'''


@dataclass
class PlatformTarget:
    """
    One connected destination (one social account).
    account_id is the Zernio account id (string, usually 24 chars).
    """
    platform: str
    account_id: str
    metadata: dict[str, Any] | None = None



@dataclass
class PublishJob:
    content: str
    platform_targets: list[dict[str, Any]]
    # each target: {"platform": "instagram", "accountId": "...", "metadata": {...}?}

    media_paths: list[str] | None = None
    scheduled_for: datetime | str | None = None
    timezone: str = "UTC"

    # optional per-platform fields
    title: str | None = None
    tags: list[str] | None = None
    hashtags: list[str] | None = None
    mentions: list[str] | None = None

    # global metadata merged with per-platform metadata
    metadata: dict[str, Any] | None = None

    # platform-specific top-level supported by SDK
    tiktok_settings: dict[str, Any] | None = None

    # if you want to enqueue into Zernio profile queue instead of scheduled_for
    queued_from_profile: str | None = None
