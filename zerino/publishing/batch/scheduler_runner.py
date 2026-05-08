"""
Batch scheduler daemon — Phase 2 + Phase 3.

Polls the posts table for pending rows and dispatches them to Zernio.
Implements:
  - Exponential backoff retry (Phase 3)
  - Per-platform rate limiting (Phase 3)
  - Heartbeat log every 60 s so you can detect a dead daemon (Phase 3)

Run:
    python -m zerino.publishing.batch.scheduler_runner
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from zerino.config import DB_PATH, get_logger
from zerino.db.repositories.posts_repository import (
    claim_due_posts,
    mark_published,
    record_failure,
    recover_stale_claims,
)
from zerino.healthcheck import HealthcheckError, run_scheduler_healthcheck
from zerino.publishing.zernio.poster import dispatch_post

log = get_logger("zerino.publishing.scheduler")

# How often to sweep for stale 'processing' rows (scheduler crashed mid-dispatch).
_STALE_RECOVERY_INTERVAL_SECONDS = 300.0

# Phase 3: minimum seconds between consecutive posts to the same platform
PLATFORM_RATE_LIMITS: dict[str, float] = {
    "tiktok": 10.0,
    "youtube_shorts": 10.0,
    "instagram_reels": 10.0,
    "twitter": 5.0,
    "pinterest": 5.0,
}
_DEFAULT_RATE_LIMIT = 10.0

# Phase 3: exponential backoff — delay = BASE * 2^(attempts-1), capped at 1 hour
_RETRY_BASE_SECONDS = 60.0
_RETRY_CAP_SECONDS = 3600.0

_HEARTBEAT_INTERVAL = 60.0  # seconds between heartbeat log lines


def _next_retry_at(attempts: int) -> str:
    delay = min(_RETRY_BASE_SECONDS * (2 ** (attempts - 1)), _RETRY_CAP_SECONDS)
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()


def _sweep_stale_claims() -> None:
    """Sweep stranded 'processing' rows. Logged WARN with ids so the user can
    investigate (post may or may not have actually reached Zernio)."""
    try:
        recovered = recover_stale_claims()
    except Exception:
        log.exception("stale-claim sweep failed")
        return
    if recovered:
        log.warning(
            "recovered %d stale 'processing' post(s) -> marked 'failed': %s. "
            "Check Zernio dashboard before retrying.",
            len(recovered), recovered,
        )


def run_scheduler_loop(
    *,
    poll_seconds: float = 5.0,
    claim_limit: int = 20,
) -> None:
    log.info("Scheduler started. db=%s poll=%.1fs", DB_PATH, poll_seconds)

    try:
        run_scheduler_healthcheck()
    except HealthcheckError as e:
        log.error("scheduler healthcheck failed: %s", e)
        raise SystemExit(1)

    # On startup, sweep once: anything left in 'processing' from a prior run
    # is by definition stranded (no scheduler is alive holding it).
    _sweep_stale_claims()

    _platform_last_sent: dict[str, float] = {}
    _last_heartbeat = time.monotonic()
    _last_stale_sweep = time.monotonic()

    while True:
        # Phase 3: heartbeat
        now_mono = time.monotonic()
        if now_mono - _last_heartbeat >= _HEARTBEAT_INTERVAL:
            log.info("heartbeat: scheduler alive")
            _last_heartbeat = now_mono

        # Periodic stale-claim sweep (covers in-process crashes that didn't
        # come via a clean restart).
        if now_mono - _last_stale_sweep >= _STALE_RECOVERY_INTERVAL_SECONDS:
            _sweep_stale_claims()
            _last_stale_sweep = now_mono

        try:
            due = claim_due_posts(limit=claim_limit)
        except Exception:
            log.exception("Failed to claim due posts from DB")
            time.sleep(poll_seconds)
            continue

        if not due:
            time.sleep(poll_seconds)
            continue

        log.info("Claimed %d post(s) for dispatch", len(due))

        for row in due:
            post_id: int = row["id"]
            platform: str = row["platform"]
            attempts: int = row["attempts"]
            max_attempts: int = row["max_attempts"]

            # Phase 3: rate limiting — enforce minimum gap between posts per platform
            min_interval = PLATFORM_RATE_LIMITS.get(platform, _DEFAULT_RATE_LIMIT)
            elapsed = time.monotonic() - _platform_last_sent.get(platform, 0.0)
            if elapsed < min_interval:
                wait = min_interval - elapsed
                log.debug("rate-limit: waiting %.1fs before next %s post", wait, platform)
                time.sleep(wait)

            try:
                zernio_post_id = dispatch_post(row)
                mark_published(post_id, zernio_post_id)
                _platform_last_sent[platform] = time.monotonic()
                log.info(
                    "published post id=%d platform=%s zernio_post_id=%s",
                    post_id, platform, zernio_post_id,
                )

            except Exception as e:
                new_attempts = attempts + 1
                log.exception(
                    "post id=%d failed (attempt %d/%d): %s",
                    post_id, new_attempts, max_attempts, e,
                )

                if new_attempts >= max_attempts:
                    # permanent failure — no more retries
                    record_failure(post_id, str(e), retry_at=None)
                    log.warning(
                        "post id=%d permanently failed after %d attempts",
                        post_id, new_attempts,
                    )
                else:
                    # Phase 3: schedule retry with exponential backoff
                    retry_at = _next_retry_at(new_attempts)
                    record_failure(post_id, str(e), retry_at=retry_at)
                    log.info(
                        "post id=%d will retry at %s (attempt %d/%d)",
                        post_id, retry_at, new_attempts, max_attempts,
                    )

        # small yield so we don't spin immediately when many jobs are due
        time.sleep(0.1)


if __name__ == "__main__":
    run_scheduler_loop(poll_seconds=5.0, claim_limit=20)
