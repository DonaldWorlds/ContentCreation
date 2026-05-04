# app/payloads/twitter.py

from __future__ import annotations
from datetime import datetime
from typing import Any


def build_twitter_post_payload(
    *,
    account_id: str,
    text: str,
    media_items: list[dict[str, Any]] | None = None,
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
    platform: str = "twitter",  # set to "x" if your connected account uses that
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": text,
        "platforms": [{"platform": platform, "accountId": account_id}],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "hashtags": hashtags,
        "mentions": mentions,
        "metadata": metadata,
    }
    return {k: v for k, v in payload.items() if v is not None}