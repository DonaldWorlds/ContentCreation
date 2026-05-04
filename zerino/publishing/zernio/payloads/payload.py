# payload_builders.py
# Works with your SDK signature:
# client.posts.create(content=..., title=..., platforms=[...], media_items=[...],
#                    scheduled_for=..., timezone=..., tags=..., hashtags=..., mentions=...,
#                    metadata=..., tiktok_settings=...)

from __future__ import annotations
from datetime import datetime
from typing import Any


def _platform_entry(platform: str, account_id: str) -> dict[str, Any]:
    return {"platform": platform, "accountId": account_id}


def instagram_payload(
    *,
    account_id: str,
    caption: str,
    media_items: list[dict[str, Any]],  # [{"url":..., "type":"image|video"}]
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    post_type: str | None = None,  # "feed" | "reel" (optional hint)
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
) -> dict[str, Any]:
    md = {}
    if post_type:
        md = {"instagram": {"postType": post_type}}

    return {
        "content": caption,
        "platforms": [_platform_entry("instagram", account_id)],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "hashtags": hashtags,
        "mentions": mentions,
        "metadata": md or None,
    }


def youtube_shorts_payload(
    *,
    account_id: str,
    title: str,
    description: str,
    media_items: list[dict[str, Any]],  # must include a video item
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    privacy: str = "public",  # public|unlisted|private
    tags: list[str] | None = None,
) -> dict[str, Any]:
    md = {
        "youtube": {
            "isShort": True,
            "privacyStatus": privacy,
        }
    }
    return {
        "title": title,
        "content": description,
        "platforms": [_platform_entry("youtube", account_id)],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "tags": tags,
        "metadata": md,
    }


def tiktok_payload(
    *,
    account_id: str,
    caption: str,
    media_items: list[dict[str, Any]],  # must include a video item
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    tiktok_settings: dict[str, Any] | None = None,
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "content": caption,
        "platforms": [_platform_entry("tiktok", account_id)],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "hashtags": hashtags,
        "mentions": mentions,
        "tiktok_settings": tiktok_settings,  # SDK-supported
    }


def twitter_payload(
    *,
    account_id: str,
    text: str,
    media_items: list[dict[str, Any]] | None = None,
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "content": text,
        "platforms": [_platform_entry("twitter", account_id)],  # or "x" depending on your account.platform
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "hashtags": hashtags,
        "mentions": mentions,
    }


def pinterest_payload(
    *,
    account_id: str,
    title: str,
    description: str,
    media_items: list[dict[str, Any]],  # typically image (or video if supported)
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    link: str | None = None,
    board_id: str | None = None,
) -> dict[str, Any]:
    # Pinterest often needs extra fields (board, destination link).
    # Put them in metadata unless your Zernio docs specify dedicated fields.
    md: dict[str, Any] = {"pinterest": {}}
    if link:
        md["pinterest"]["link"] = link
    if board_id:
        md["pinterest"]["boardId"] = board_id
    if not md["pinterest"]:
        md = {}

    return {
        "title": title,
        "content": description,
        "platforms": [_platform_entry("pinterest", account_id)],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "metadata": md or None,
    }