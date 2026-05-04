# app/services/zernio_publisher.py
from __future__ import annotations

import logging
from typing import Any, Iterable

from zerino.publishing.publish_job import PublishJob, PlatformTarget
from zerino.publishing.zernio.accounts import resolve_account_id
from zerino.publishing.zernio.media import upload_media
from zerino.publishing.zernio.posts import create_or_schedule_post, get_post  # <-- add get_post

from zerino.publishing.zernio.payloads.instagram_payload import build_instagram_post_payload
from zerino.publishing.zernio.payloads.youtube_shorts_payload import build_youtube_shorts_payload
from zerino.publishing.zernio.payloads.tiktok_payload import build_tiktok_post_payload
from zerino.publishing.zernio.payloads.twitter_payload import build_twitter_post_payload


logger = logging.getLogger(__name__)


def _infer_media_type(path: str) -> str:
    return "video" if path.lower().endswith(".mp4") else "image"


def _build_media_items(media_paths: list[str]) -> list[dict[str, Any]]:
    items = []
    for path in media_paths:
        url = upload_media(path)
        items.append({"url": url, "type": _infer_media_type(path)})
    return items


def _extract_post_id(create_result: Any) -> str | None:
    """
    Best-effort extraction of a post id from whatever create_or_schedule_post returns.
    Adjust this if your integrations return a different shape.
    """
    if isinstance(create_result, dict):
        return (
            create_result.get("id")
            or create_result.get("postId")
            or create_result.get("field_id")
            or create_result.get("data", {}).get("id")
            or create_result.get("data", {}).get("field_id")
        )
    return None


def _summarize_post_status(post: Any) -> dict[str, Any]:
    if not isinstance(post, dict):
        return {"raw": post}

    status = post.get("status")
    scheduled_at = post.get("scheduledAt") or post.get("scheduled_for")
    published_at = post.get("publishedAt")
    error = post.get("error") or post.get("lastError") or post.get("failureReason") or post.get("message")

    return {
        "status": status,
        "scheduledAt": scheduled_at,
        "publishedAt": published_at,
        "error": error,
    }


def publish_job(job: PublishJob):
    """
    Crosspost behavior:
    - If job.targets has multiple entries, and all platforms can share the same payload fields,
      we submit ONE Zernio post with platforms=[...].
    Customized behavior:
    - If platforms need different fields (e.g. YouTube title/description vs IG caption),
      submit separate Zernio posts per platform target.
    """
    if not job.targets:
        raise ValueError("PublishJob.targets is empty")

    # resolve account ids if missing/invalid
    resolved_targets: list[PlatformTarget] = []
    for t in job.targets:
        account_id = t.account_id
        if not (isinstance(account_id, str) and len(account_id) == 24):
            account_id = resolve_account_id(t.platform)
        resolved_targets.append(
            PlatformTarget(platform=t.platform, account_id=account_id, metadata=t.metadata)
        )

    media_items = _build_media_items(job.media_paths) if job.media_paths else []

    results = []

    for t in resolved_targets:
        platform = t.platform.lower()

        # per-platform metadata merged with job metadata (simple shallow merge)
        merged_md: dict[str, Any] = {}
        if job.metadata:
            merged_md.update(job.metadata)
        if t.metadata:
            merged_md.update(t.metadata)

        if platform == "instagram":
            payload = build_instagram_post_payload(
                caption=job.content,
                media_items=media_items,
                account_id=t.account_id,
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
                account_id=t.account_id,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                tags=job.tags,
                privacy=(merged_md.get("youtube", {}) or {}).get("privacyStatus", "public"),
                metadata=merged_md or None,
            )

        elif platform == "tiktok":
            payload = build_tiktok_post_payload(
                account_id=t.account_id,
                caption=job.content,
                media_items=media_items,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                hashtags=job.hashtags,
                mentions=job.mentions,
                tiktok_settings=job.tiktok_settings,
                metadata=merged_md or None,
            )

        elif platform == "twitter":
            payload = build_twitter_post_payload(
                account_id=t.account_id,
                text=job.content,
                media_items=media_items or None,
                scheduled_for=job.scheduled_for,
                timezone=job.timezone,
                hashtags=job.hashtags,
                mentions=job.mentions,
                platform="twitter",  # Zernio platform id stays "twitter"
                metadata=merged_md or None,
            )

        else:
            raise ValueError(f"Unsupported platform in publisher: {t.platform}")

        # Zernio queue scheduling option
        if job.queued_from_profile:
            payload.pop("scheduled_for", None)
            payload["queued_from_profile"] = job.queued_from_profile

        # 1) Create/schedule
        create_result = create_or_schedule_post(payload)
        results.append(create_result)

        # 2) Immediately fetch post to see if it's draft/scheduled/failed/published
        post_id = _extract_post_id(create_result)
        if not post_id:
            logger.warning("Zernio returned 201 but no post id found. create_result=%s", create_result)
            continue

        try:
            post = get_post(post_id)  # you implement in app/integrations/zernio/posts.py
            summary = _summarize_post_status(post)
            logger.info("Zernio post %s status: %s", post_id, summary)
        except Exception:
            logger.exception("Failed to fetch Zernio post %s after creation", post_id)

    # if multiple platforms, return list; if one, return single result
    return results[0] if len(results) == 1 else results


def publish_one(job: PublishJob):
    """
    For now: requires exactly ONE target.
    Returns a single PostCreateResponse.
    """
    if len(job.targets) != 1:
        raise ValueError("publish_one requires exactly one target. Use publish_job for multi-target.")
    return publish_job(job)  # your existing function will return a single result for one target


def publish_many(jobs: Iterable[PublishJob]):
    """
    Batch: publish multiple jobs (each job may be single-target).
    Returns list of results in the same order.
    """
    results = []
    for job in jobs:
        results.append(publish_one(job))
    return results