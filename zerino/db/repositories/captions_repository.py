from __future__ import annotations

import random
import sqlite3
from typing import Any

from zerino.config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_caption(text: str, hashtags: str | None = None, weight: int = 1) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO captions (text, hashtags, weight) VALUES (?, ?, ?)",
            (text.strip(), hashtags.strip() if hashtags else None, max(1, weight)),
        )
        return cur.lastrowid


def list_captions(active_only: bool = False) -> list[dict[str, Any]]:
    with _connect() as conn:
        sql = "SELECT * FROM captions"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def deactivate_caption(caption_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE captions SET active=0 WHERE id=?", (caption_id,))


def reactivate_caption(caption_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE captions SET active=1 WHERE id=?", (caption_id,))


def delete_caption(caption_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM captions WHERE id=?", (caption_id,))


def pick_random_caption() -> str:
    """Pick a random active caption (weighted) and return formatted post text.

    Format: "<text>\n\n<hashtags>" if hashtags exist, else "<text>".
    Returns "" if the pool has no active rows.
    """
    rows = list_captions(active_only=True)
    if not rows:
        return ""

    weights = [max(1, int(r["weight"] or 1)) for r in rows]
    chosen = random.choices(rows, weights=weights, k=1)[0]

    text = (chosen["text"] or "").strip()
    hashtags = (chosen["hashtags"] or "").strip()
    if text and hashtags:
        return f"{text}\n\n{hashtags}"
    return text or hashtags
