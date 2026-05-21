import sqlite3
from zerino.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute("""
SELECT id, platform, handle, zernio_account_id
FROM accounts
ORDER BY id DESC
LIMIT 10
""").fetchall()

for row in rows:
    print(dict(row))

conn.close()
