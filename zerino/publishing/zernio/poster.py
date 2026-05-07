"""
Single-post Zernio dispatch.

Takes one post row dict (joined with accounts) and calls the Zernio API.
Returns the zernio_post_id string on success; raises on any failure.

Internal platform names → Zernio platform identifiers:
    youtube_shorts  → youtube
    instagram_reels → instagram
    twitter         → twitter
    tiktok          → tiktok
    pinterest       → pinterest
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from zerino.publishing.zernio.media import upload_media
from zerino.publishing.zernio.posts import create_or_schedule_post

log = logging.getLogger("zerino.publishing.poster")

_PLATFORM_MAP: dict[str, str] = {
    "youtube_shorts": "youtube",
    "facebook_reels": "facebook",
}


def dispatch_post(post_row: dict[str, Any]) -> str:
    """
    Dispatch a single post row to Zernio. Returns zernio_post_id.
    Raises RuntimeError or SDK exceptions on failure.
    """
    platform = post_row["platform"]
    zernio_account_id = post_row["zernio_account_id"]
    render_path = post_row["render_path"]
    caption = post_row.get("caption") or ""
    scheduled_for = post_row.get("scheduled_for")

    zernio_platform = _PLATFORM_MAP.get(platform, platform)
    media_type = "video" if render_path.lower().endswith(".mp4") else "image"

    log.info(
        "poster: uploading %s for platform=%s account=%s",
        render_path, zernio_platform, zernio_account_id,
    )
    media_url = upload_media(render_path)

    # Zernio creates a DRAFT (not a live post) if `scheduled_for` is missing.
    # When the row has no scheduled_for it means "post immediately" — so we
    # send "now" as the time and Zernio publishes right away.
    if not scheduled_for:
        scheduled_for = datetime.now(timezone.utc).isoformat()

    payload: dict[str, Any] = {
        "content": caption,
        "platforms": [{"platform": zernio_platform, "accountId": zernio_account_id}],
        "media_items": [{"url": media_url, "type": media_type}],
        "scheduled_for": scheduled_for,
        "timezone": "UTC",
    }

    result = create_or_schedule_post(payload)
    post_id = _extract_post_id(result)
    if not post_id:
        raise RuntimeError(f"Zernio returned no post id. result={result!r}")

    log.info("poster: created zernio_post_id=%s platform=%s", post_id, zernio_platform)
    return post_id


def _extract_post_id(result: Any) -> str | None:
    # SDK returns a Pydantic PostCreateResponse — check result.post.field_id first
    post_obj = getattr(result, "post", None)
    if post_obj is not None:
        pid = getattr(post_obj, "field_id", None) or getattr(post_obj, "id", None)
        if pid:
            return str(pid)

    # Fallback: plain dict shapes
    if isinstance(result, dict):
        return (
            result.get("id")
            or result.get("postId")
            or result.get("field_id")
            or (result.get("data") or {}).get("id")
            or (result.get("data") or {}).get("field_id")
        )
    return None
