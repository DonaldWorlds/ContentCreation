"""Fixtures for detection tests: an isolated DB built with the REAL schema."""
import sqlite3

import pytest

import zerino.db.init_db as initdb


@pytest.fixture
def tmp_db_conn(tmp_path, monkeypatch):
    """A fresh sqlite DB created via the real init_db (markers/clips/recordings/...)."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(initdb, "DB_PATH", str(db))
    initdb.create_database()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def recording_id(tmp_db_conn):
    cur = tmp_db_conn.execute(
        "INSERT INTO recordings (filename) VALUES (?)", ("2026-01-01 00-00-00.mkv",)
    )
    tmp_db_conn.commit()
    return cur.lastrowid
