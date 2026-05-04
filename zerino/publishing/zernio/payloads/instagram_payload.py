# app/payloads/instagram.py

from __future__ import annotations
from datetime import datetime
from typing import Any


def build_instagram_post_payload(
    *,
    caption: str,
    media_items: list[dict[str, Any]],  # [{"url": "...", "type": "image"|"video"}]
    account_id: str,
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not media_items:
        raise ValueError("Instagram requires at least one media item.")
    if any("url" not in m or "type" not in m for m in media_items):
        raise ValueError("Each media item must include 'url' and 'type'.")
    if any(m["type"] not in ("image", "video") for m in media_items):
        raise ValueError("Instagram media item type must be 'image' or 'video'.")

    post_type = "reel" if any(m["type"] == "video" for m in media_items) else "feed"

    md: dict[str, Any] = {}
    if metadata:
        md.update(metadata)
    md.setdefault("instagram", {})
    md["instagram"].setdefault("postType", post_type)

    payload: dict[str, Any] = {
        "content": caption,
        "platforms": [{"platform": "instagram", "accountId": account_id}],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "hashtags": hashtags,
        "mentions": mentions,
        "metadata": md,
    }

    return {k: v for k, v in payload.items() if v is not None}