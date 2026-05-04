# app/batch_schedule/zernio_publisher.py
from __future__ import annotations

import logging
from typing import Any

from zerino.publishing.publish_job import PublishJob
from zerino.publishing.zernio.accounts import resolve_account_id
from zerino.publishing.zernio.media import upload_media
from zerino.publishing.zernio.posts import create_or_schedule_post, get_post

from zerino.publishing.zernio.payloads.instagram_payload import build_instagram_post_payload
from zerino.publishing.zernio.payloads.youtube_shorts_payload import build_youtube_shorts_payload
from zerino.publishing.zernio.payloads.tiktok_payload import build_tiktok_post_payload
from zerino.publishing.zernio.payloads.twitter_payload import build_twitter_post_payload


logger = logging.getLogger(__name__)


def _infer_media_type(path: str) -> str:
    return "video" if path.lower().endswith((".mp4", ".mov", ".m4v")) else "image"


def _build_media_items(media_paths: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in media_paths:
        url = upload_media(path)
        items.append({"url": url, "type": _infer_media_type(path)})
    return items


def _extract_post_id(create_result: Any) -> str | None:
    if isinstance(create_result, dict):
        return (
            create_result.get("id")
            or create_result.get("postId")
            or create_result.get("field_id")
            or create_result.get("data", {}).get("id")
            or create_result.get("data", {}).get("field_id")
        )
    return None


def publish_scheduled_job(job: PublishJob) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Batch/scheduled publisher that matches PublishJob.platform_targets (list[dict]).

    Each target dict should look like:
      {"platform": "instagram", "accountId": "...", "metadata": {...}?}
    """
    if not job.platform_targets:
        raise ValueError("PublishJob.platform_targets is empty")

    # upload media once per job (shared URLs)
    media_items = _build_media_items(job.media_paths) if job.media_paths else []

    results: list[Any] = []

    for t in job.platform_targets:
        platform = (t.get("platform") or "").lower()
        account_id = t.get("accountId") or t.get("account_id")

        if not (isinstance(account_id, str) and len(account_id) == 24):
            account_id = resolve_account_id(platform)

        # merge metadata (job.metadata + target.metadata)
        merged_md: dict[str, Any] = {}
        if job.metadata:
            merged_md.update(job.metadata)
        if isinstance(t.get("metadata"), dict):
            merged_md.update(t["metadata"])

        if platform == "instagram":
            payload = build_instagram_post_payload(
                caption=job.content,
                media_items=media_items,
                account_id=account_id,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                hashtags=job.hashtags,
                mentions=job.mentions,
                metadata=merged_md or None,
            )

        elif platform == "youtube":
            payload = build_youtube_shorts_payload(
                title=job.title or "Untitled",
                description=job.content,
                video_url=media_items[0]["url"] if media_items else "",
                account_id=account_id,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                tags=job.tags,
                privacy=(merged_md.get("youtube", {}) or {}).get("privacyStatus", "public"),
                metadata=merged_md or None,
            )

        elif platform == "tiktok":
            payload = build_tiktok_post_payload(
                account_id=account_id,
                caption=job.content,
                media_items=media_items,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                hashtags=job.hashtags,
                mentions=job.mentions,
                tiktok_settings=job.tiktok_settings,
                metadata=merged_md or None,
            )

        elif platform in ("twitter", "x"):
            payload = build_twitter_post_payload(
                account_id=account_id,
                text=job.content,
                media_items=media_items or None,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                hashtags=job.hashtags,
                mentions=job.mentions,
                platform="twitter",
                metadata=merged_md or None,
            )

        else:
            raise ValueError(f"Unsupported platform in batch publisher: {platform}")

        # optional Zernio queue scheduling
        if job.queued_from_profile:
            payload.pop("scheduled_for", None)
            payload["queued_from_profile"] = job.queued_from_profile

        create_result = create_or_schedule_post(payload)
        results.append(create_result)

        post_id = _extract_post_id(create_result)
        if post_id:
            try:
                post = get_post(post_id)
                logger.info("Created Zernio post %s (status=%s)", post_id, post.get("status"))
            except Exception:
                logger.exception("Failed to fetch Zernio post %s after creation", post_id)

    return results[0] if len(results) == 1 else results