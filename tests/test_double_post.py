"""Regression guard for the double-posting bug.

Run:  python tests/test_double_post.py   (needs ffmpeg/ffprobe on PATH for the
imports; no network — Zernio + the failing DB write are stubbed.)

Test A (pipeline): a post that was SENT (dispatch_post returned) must never be
left 'pending' when a post-send step fails (e.g. sqlite 'database is locked').
Pre-fix this FAILED: the except recorded a retry -> status 'pending' -> the
scheduler re-claimed and double-posted.

Test B (poster): when the Zernio create call SUCCEEDS but the response id
can't be parsed, dispatch_post must NOT raise (raising makes the caller retry
-> double). It returns a sentinel (the post is already created on Zernio).

Test C: happy path publishes exactly once, and re-dispatching an already-
published row is a no-op (the atomic claim skips it).
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

# Make `import zerino...` work when run as `python tests/test_double_post.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import zerino.config as cfg
import zerino.db.init_db as initdb
import zerino.db.repositories.posts_repository as pr
import zerino.publishing.pipeline as pipeline
import zerino.publishing.zernio.poster as poster


def _fresh_db():
    tmpdb = Path(tempfile.mkdtemp()) / "test.db"
    for mod in (cfg, initdb, pr, pipeline):
        mod.DB_PATH = tmpdb
    initdb.create_database()
    conn = sqlite3.connect(tmpdb)
    conn.execute(
        "INSERT INTO accounts (platform, handle, zernio_account_id, profile_id, active, layout) "
        "VALUES (?,?,?,?,?,?)",
        ("tiktok", "@t", "a" * 24, None, 1, "vertical"),
    )
    acct_id = conn.execute("SELECT id FROM accounts").fetchone()[0]
    conn.execute(
        "INSERT INTO posts (clip_id, platform, account_id, render_path, caption, status, mode, scheduled_for) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (None, "tiktok", acct_id, "/x.mp4", "cap", "pending", "manual", None),
    )
    pid = conn.execute("SELECT id FROM posts").fetchone()[0]
    conn.commit()
    conn.close()
    return tmpdb, pid


def _status(tmpdb, pid):
    conn = sqlite3.connect(tmpdb)
    row = conn.execute("SELECT status, attempts FROM posts WHERE id=?", (pid,)).fetchone()
    conn.close()
    return row


def test_A_pipeline_db_lock_after_send():
    tmpdb, pid = _fresh_db()
    sent = {"n": 0}
    mp = {"n": 0}

    def _dispatch(row):
        sent["n"] += 1
        return "zernio-123"  # SENT

    _real_mark = pr.mark_published

    def _flaky_mark(post_id, zid):
        mp["n"] += 1
        if mp["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return _real_mark(post_id, zid)

    o1, o2 = pipeline.dispatch_post, pr.mark_published
    # durable helper lives in pr and calls pr.mark_published at the global name,
    # so patch it there to simulate the first write hitting a lock.
    pipeline.dispatch_post, pr.mark_published = _dispatch, _flaky_mark
    try:
        pipeline.dispatch_post_ids([pid])
    finally:
        pipeline.dispatch_post, pr.mark_published = o1, o2

    status, attempts = _status(tmpdb, pid)
    print(f"[A] send_calls={sent['n']} status={status} attempts={attempts}")
    assert sent["n"] == 1, f"[A] dispatch_post called {sent['n']}x"
    assert status != "pending", (
        f"[A] DOUBLE-POST RISK: SENT post left 'pending' -> scheduler re-dispatches"
    )
    print("[A] OK")


def test_B_poster_unparseable_id_does_not_raise():
    o_up, o_create = poster.upload_media, poster.create_or_schedule_post
    poster.upload_media = lambda p: "https://media/x.mp4"
    poster.create_or_schedule_post = lambda payload: {"unexpected": "shape"}  # created, no id
    row = {
        "platform": "tiktok", "zernio_account_id": "a" * 24,
        "render_path": "/x.mp4", "caption": "c", "scheduled_for": None,
    }
    try:
        result = poster.dispatch_post(row)  # must NOT raise (post is created)
        print(f"[B] dispatch_post returned {result!r} (no raise) -> OK")
    finally:
        poster.upload_media, poster.create_or_schedule_post = o_up, o_create


def test_C_happy_path_single_publish():
    tmpdb, pid = _fresh_db()
    sent = {"n": 0}

    def _dispatch(row):
        sent["n"] += 1
        return "zernio-OK"

    o1 = pipeline.dispatch_post
    pipeline.dispatch_post = _dispatch
    try:
        pipeline.dispatch_post_ids([pid])
    finally:
        pipeline.dispatch_post = o1

    status, attempts = _status(tmpdb, pid)
    print(f"[C] send_calls={sent['n']} status={status} attempts={attempts}")
    assert sent["n"] == 1 and status == "published", f"[C] expected single publish, got {status}"
    # re-dispatching an already-published row must be a no-op (claim skips it)
    pipeline.dispatch_post = _dispatch
    try:
        pipeline.dispatch_post_ids([pid])
    finally:
        pipeline.dispatch_post = o1
    assert sent["n"] == 1, f"[C] already-published row was re-sent ({sent['n']}x)!"
    print("[C] OK")


test_A_pipeline_db_lock_after_send()
test_B_poster_unparseable_id_does_not_raise()
test_C_happy_path_single_publish()
print("ALL PASS")
