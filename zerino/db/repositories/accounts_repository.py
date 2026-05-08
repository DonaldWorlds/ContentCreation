from __future__ import annotations

import sqlite3
from typing import Any

from zerino.config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_account(
    platform: str,
    handle: str,
    zernio_account_id: str,
    profile_id: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO accounts (platform, handle, zernio_account_id, profile_id)
               VALUES (?, ?, ?, ?)""",
            (platform.lower(), handle, zernio_account_id, profile_id),
        )
        return cur.lastrowid


def get_accounts_for_platform(platform: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE platform=? AND active=1",
            (platform.lower(),),
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_accounts() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM accounts ORDER BY platform, handle"
        ).fetchall()
        return [dict(r) for r in rows]


def deactivate_account(account_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE accounts SET active=0 WHERE id=?", (account_id,))


def delete_account(account_id: int) -> int:
    """Hard delete. Returns the number of rows removed (0 if no match)."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        return cur.rowcount
