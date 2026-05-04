# app/batch_schedule/planner.py
"""
Scheduling “time planner” for batch posts.

What this file does:
- Generates scheduled timestamps for each batch item based on a strategy, e.g.:
  - fixed interval (every N minutes/hours)
  - specific posting windows (Mon–Fri, 9am–5pm)
  - skip weekends
  - max posts per day
- Accepts:
  - start datetime, timezone, count, cadence rules, blackout dates
- Outputs:
  - a list of scheduled datetimes aligned 1:1 with the items (or an iterator)

Key rule:
- planner.py decides *when* posts should go out, not *what* the posts are.

What this file should NOT do:
- No file/media handling (handler.py).
- No Zernio publishing (existing publisher).
"""



from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class BatchSchedulePlanner:
    """
    Computes scheduled datetimes for a batch of posts.

    V1 strategy: fixed interval scheduling.
    - start_at: first scheduled time (timezone-aware strongly recommended)
    - interval_minutes: spacing between posts
    """

    start_at: datetime
    interval_minutes: int = 120  # default: every 2 hours

    def plan(self, count: int) -> list[datetime]:
        if count <= 0:
            return []
        if self.start_at.tzinfo is None:
            # force timezone awareness to avoid silent bugs
            raise ValueError("start_at must be timezone-aware (e.g., datetime.now(timezone.utc))")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be > 0")

        step = timedelta(minutes=self.interval_minutes)
        return [self.start_at + i * step for i in range(count)]