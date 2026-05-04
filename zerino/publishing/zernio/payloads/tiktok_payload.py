# app/payloads/tiktok.py

from __future__ import annotations
from datetime import datetime
from typing import Any


def build_tiktok_post_payload(
    *,
    account_id: str,
    caption: str,
    media_items: list[dict[str, Any]],  # [{"url": "...", "type": "video"}]
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    tiktok_settings: dict[str, Any] | None = None,
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not media_items:
        raise ValueError("TikTok requires at least one media item.")
    if not any((m or {}).get("type") == "video" for m in media_items):
        raise ValueError("TikTok posts require a video media item (type='video').")

    payload: dict[str, Any] = {
        "content": caption,
        "platforms": [{"platform": "tiktok", "accountId": account_id}],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "hashtags": hashtags,
        "mentions": mentions,
        "tiktok_settings": tiktok_settings,  # supported by your SDK
        "metadata": metadata,
    }

    return {k: v for k, v in payload.items() if v is not None}