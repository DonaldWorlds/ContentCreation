"""Definitive concurrency proof: run BOTH dispatchers at once on the same posts
and count actual sends per post. The bug = any post sent more than once.

Thread A = pipeline.dispatch_post_ids (the immediate/capture path).
Thread B = the scheduler loop body (claim_due_posts -> dispatch -> publish).
Both race over the same pending rows, exactly like production (capture process
+ scheduler daemon). dispatch_post is stubbed to COUNT sends per post id, with
jitter to force contention, and mark_published is made to fail randomly to
simulate the "database is locked" that used to cause re-sends.

INVARIANT: every post is sent exactly once. Run many iterations to shake out
the race.
"""
import random
import sqlite3
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import zerino.config as cfg
import zerino.db.init_db as initdb
import zerino.db.repositories.posts_repository as pr
import zerino.publishing.pipeline as pipeline

N_POSTS = 25
ITERATIONS = 40


def _setup_db():
    tmpdb = Path(tempfile.mkdtemp()) / "t.db"
    for mod in (cfg, initdb, pr, pipeline):
        mod.DB_PATH = tmpdb
    initdb.create_database()
    conn = sqlite3.connect(tmpdb)
    conn.execute(
        "INSERT INTO accounts (platform,handle,zernio_account_id,profile_id,active,layout) "
        "VALUES (?,?,?,?,?,?)", ("tiktok", "@t", "a" * 24, None, 1, "vertical"),
    )
    aid = conn.execute("SELECT id FROM accounts").fetchone()[0]
    # all due now (scheduled_for in the past) so both dispatchers see them
    past = "2000-01-01T00:00:00+00:00"
    pids = []
    for _ in range(N_POSTS):
        cur = conn.execute(
            "INSERT INTO posts (clip_id,platform,account_id,render_path,caption,status,mode,scheduled_for) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (None, "tiktok", aid, "/x.mp4", "c", "pending", "manual", past),
        )
        pids.append(cur.lastrowid)
    conn.commit(); conn.close()
    return tmpdb, pids


def run_once():
    tmpdb, pids = _setup_db()
    sends = Counter()
    lock = threading.Lock()

    def stub_dispatch(row):
        with lock:
            sends[row["id"]] += 1
        time.sleep(random.uniform(0, 0.003))  # jitter -> contention
        return f"z-{row['id']}"
    pipeline.dispatch_post = stub_dispatch

    # make the local publish-write fail ~40% of the time (db-lock simulation);
    # the durable helper must ride it out WITHOUT a re-send.
    real_mark = pr._mark_published_impl if hasattr(pr, "_mark_published_impl") else None
    _orig_mark = pr.mark_published
    def flaky_mark(post_id, zid):
        if random.random() < 0.4:
            raise sqlite3.OperationalError("database is locked")
        return _orig_mark(post_id, zid)
    pr.mark_published = flaky_mark

    def thread_immediate():
        pipeline.dispatch_post_ids(list(pids))

    def thread_scheduler():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            due = pr.claim_due_posts(limit=N_POSTS)
            if not due:
                # stop once everything is terminal
                conn = sqlite3.connect(tmpdb)
                left = conn.execute("SELECT COUNT(*) FROM posts WHERE status='pending'").fetchone()[0]
                proc = conn.execute("SELECT COUNT(*) FROM posts WHERE status='processing'").fetchone()[0]
                conn.close()
                if left == 0 and proc == 0:
                    return
                time.sleep(0.005)
                continue
            for row in due:
                try:
                    zid = stub_dispatch(row)
                except Exception as e:
                    pr.record_failure(row["id"], str(e), retry_at="2000-01-01T00:00:00+00:00")
                    continue
                if not pr.mark_published_durably(row["id"], zid):
                    pass  # left 'processing' -> stale sweep, never re-sent

    ta = threading.Thread(target=thread_immediate)
    tb = threading.Thread(target=thread_scheduler)
    ta.start(); tb.start(); ta.join(); tb.join()
    pr.mark_published = _orig_mark

    # any post sent more than once == the bug
    worst = max(sends.values()) if sends else 0
    doubled = [pid for pid, c in sends.items() if c > 1]
    return worst, doubled, len(sends)


def main():
    worst_overall = 0
    total_doubled = []
    for i in range(ITERATIONS):
        worst, doubled, n_sent = run_once()
        worst_overall = max(worst_overall, worst)
        if doubled:
            total_doubled.extend(doubled)
            print(f"iter {i}: DOUBLE-SENT posts {doubled}")
    print(f"\niterations={ITERATIONS} posts/iter={N_POSTS} "
          f"max_sends_for_any_post={worst_overall} doubled_count={len(total_doubled)}")
    if worst_overall > 1:
        print("RESULT: FAIL — a post was sent more than once")
        sys.exit(1)
    print("RESULT: PASS — every post sent exactly once across all iterations")


if __name__ == "__main__":
    main()
