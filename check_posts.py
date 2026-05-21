import sqlite3
from zerino.config import DB_PATH
c = sqlite3.connect(DB_PATH)
c.row_factory = sqlite3.Row
rows = c.execute("SELECT p.id, p.platform, p.status, p.attempts, p.last_error, a.handle FROM posts p JOIN accounts a ON a.id = p.account_id WHERE p.platform IN ('instagram_reels','pinterest') ORDER BY p.id DESC LIMIT 20").fetchall()
if not rows:
    print("NO POSTS for instagram_reels or pinterest - they were never created")
for r in rows:
    print(dict(r))
c.close()
