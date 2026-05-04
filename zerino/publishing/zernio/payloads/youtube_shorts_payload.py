# app/payloads/youtube_shorts.py

from __future__ import annotations
from datetime import datetime
from typing import Any


def build_youtube_shorts_payload(
    *,
    title: str,
    description: str,
    video_url: str,
    account_id: str,
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    tags: list[str] | None = None,
    privacy: str = "public",  # public | unlisted | private
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not title:
        raise ValueError("YouTube requires a title.")
    if not video_url:
        raise ValueError("YouTube Shorts requires a video_url.")

    # Optional convention: include #Shorts
    if "#shorts" not in (description or "").lower():
        description = (description or "").strip() + "\n\n#Shorts"

    md: dict[str, Any] = {}
    if metadata:
        md.update(metadata)
    md.setdefault("youtube", {})
    md["youtube"].setdefault("isShort", True)
    md["youtube"].setdefault("privacyStatus", privacy)

    payload: dict[str, Any] = {
        "title": title,
        "content": description,
        "platforms": [{"platform": "youtube", "accountId": account_id}],
        "media_items": [{"url": video_url, "type": "video"}],
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "tags": tags,
        "metadata": md,
    }

    return {k: v for k, v in payload.items() if v is not None}