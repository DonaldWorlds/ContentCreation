# app/integrations/zernio/posts.py
from __future__ import annotations

import logging
from typing import Any

from zerino.publishing.zernio.client import get_zernio_client

logger = logging.getLogger(__name__)


def _validate_account_id(account_id: str) -> None:
    if not (isinstance(account_id, str) and len(account_id) == 24):
        raise ValueError("accountId must be the 24-char string returned by client.accounts.list()")


def _extract_post_id(result: Any) -> str | None:
    # SDK Pydantic PostCreateResponse: result.post.field_id
    post_obj = getattr(result, "post", None)
    if post_obj is not None:
        pid = getattr(post_obj, "field_id", None) or getattr(post_obj, "id", None)
        if pid:
            return str(pid)

    if isinstance(result, dict):
        return (
            result.get("id")
            or result.get("postId")
            or result.get("field_id")
            or result.get("data", {}).get("id")
            or result.get("data", {}).get("field_id")
        )
    return None


def get_post(post_id: str) -> dict[str, Any]:
    """
    Fetch a post from Zernio so we can see whether it's draft/scheduled/failed/published.
    """
    if not post_id or not isinstance(post_id, str):
        raise ValueError("post_id must be a non-empty string")

    client = get_zernio_client()
    post = client.posts.get(post_id)  # expected SDK method; adjust if your client differs
    if not isinstance(post, dict):
        # keep it consistent for callers
        return {"raw": post}
    return post


def create_or_schedule_post(payload: dict[str, Any]):
    client = get_zernio_client()

    platforms = payload.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        raise ValueError("payload must include platforms=[{platform, accountId}, ...]")

    for i, p in enumerate(platforms):
        if not isinstance(p, dict):
            raise ValueError(f"platforms[{i}] must be a dict")
        if "accountId" not in p:
            raise ValueError(f"platforms[{i}].accountId is required")
        _validate_account_id(p["accountId"])

    clean = {k: v for k, v in payload.items() if v is not None}

    # Helpful debug without dumping secrets/huge objects
    logger.info(
        "Creating Zernio post platforms=%s scheduled_for=%s queued_from_profile=%s has_media=%s",
        [(p.get("platform"), p.get("accountId")) for p in platforms],
        clean.get("scheduled_for"),
        clean.get("queued_from_profile"),
        bool(clean.get("media_items")),
    )

    result = client.posts.create(**clean)

    post_id = _extract_post_id(result)
    logger.info("Zernio create result status=created post_id=%s raw=%s", post_id, result)

    # Immediately fetch status (draft/scheduled/failed/published)
    if post_id:
        try:
            post = get_post(post_id)
            logger.info(
                "Zernio post fetched post_id=%s status=%s scheduledAt=%s publishedAt=%s failure=%s raw=%s",
                post_id,
                post.get("status"),
                post.get("scheduledAt") or post.get("scheduled_for"),
                post.get("publishedAt"),
                post.get("failureReason") or post.get("lastError") or post.get("error"),
                post,
            )
        except Exception:
            logger.exception("Unable to fetch Zernio post after create post_id=%s", post_id)

    return result