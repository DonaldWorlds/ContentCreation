import sqlite3

from zerino.config import DB_PATH

def get_connection():
    # Creates and returns a database connection with foreign key enforcement enabled.
    # This ensures all relationships (recordings → markers → clips) are respected.
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn