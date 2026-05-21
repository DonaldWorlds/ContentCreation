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
    "instagram_reels": "instagram",
}


def _pin_title(caption: str) -> str | None:
    """Pinterest pin title — first line of caption, trimmed to 100 chars
    (PinterestPlatformData.title.max_length per the Zernio SDK model).
    """
    if not caption:
        return None
    first_line = (caption.splitlines() or [""])[0].strip()
    if not first_line:
        return None
    return first_line[:100]


def _platform_specific_data(
    zernio_platform: str, caption: str
) -> dict[str, Any] | None:
    """Return the SDK `platformSpecificData` payload for a given platform,
    or None if we have nothing platform-specific to send. The SDK accepts
    a per-platform Pydantic model (PinterestPlatformData, etc.) on each
    PlatformTarget entry; sending it makes the platform handle the post
    as a proper native pin/post type rather than relying on defaults.
    """
    if zernio_platform == "pinterest":
        # PinterestPlatformData — see late/models/_generated/models.py.
        # Fields available: title (<=100), boardId, link, coverImageUrl,
        # coverImageKeyFrameTime. Sending an explicit `title` makes the
        # pin's text deterministic; the video flag comes from
        # media_items[0].type="video" which we already set below. Pinterest
        # auto-derives a cover frame from the video if we don't send one.
        psd: dict[str, Any] = {}
        title = _pin_title(caption)
        if title:
            psd["title"] = title
        return psd or None
    return None


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

    # Platform-target entry. For Pinterest (and any future platform that
    # needs native-format hints), we attach platformSpecificData so the SDK
    # routes the post as the correct native type (video pin vs static pin
    # vs idea pin for Pinterest). Without this, Zernio uses defaults that
    # historically degraded video pins to image-with-thumbnail.
    platform_entry: dict[str, Any] = {
        "platform": zernio_platform,
        "accountId": zernio_account_id,
    }
    psd = _platform_specific_data(zernio_platform, caption)
    if psd is not None:
        platform_entry["platformSpecificData"] = psd

    payload: dict[str, Any] = {
        "content": caption,
        "platforms": [platform_entry],
        "media_items": [{"url": media_url, "type": media_type}],
        "scheduled_for": scheduled_for,
        "timezone": "UTC",
    }

    result = create_or_schedule_post(payload)

    # COMMIT POINT. The post now EXISTS on Zernio. The Zernio API has no
    # idempotency key, so a re-send = a duplicate post. Therefore nothing past
    # this line may raise: a raise makes the caller treat the post as "not
    # sent" and retry, which double-posts. If we can't parse the returned id,
    # log loudly and return a sentinel — the post is still sent.
    post_id = _extract_post_id(result)
    if not post_id:
        log.error(
            "poster: Zernio create SUCCEEDED but no post id could be parsed "
            "(post IS created on Zernio — do NOT retry/re-send). result=%r",
            result,
        )
        return "UNKNOWN"

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
