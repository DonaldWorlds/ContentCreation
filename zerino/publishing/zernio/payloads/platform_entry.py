from __future__ import annotations
from datetime import datetime
from typing import Any


def _platform_entry(platform: str, account_id: str) -> dict[str, Any]:
    return {"platform": platform, "accountId": account_id}
