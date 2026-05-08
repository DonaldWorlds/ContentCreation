import sqlite3

from zerino.config import DB_PATH

def create_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    conn.execute("PRAGMA foreign_keys = ON")

  
    # STREAMERS (GLOBAL ENTITY)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streamers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        platform TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)


    # RECORDINGS (CORE PIPELINE OBJECT)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)


    # MARKERS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS markers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        streamer_id INTEGER,
        recording_id INTEGER NOT NULL,
        timestamp INTEGER NOT NULL,
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(streamer_id) REFERENCES streamers(id) ON DELETE SET NULL,
        FOREIGN KEY(recording_id) REFERENCES recordings(id) ON DELETE CASCADE
    )
    """)
  
    # CLIPS (PIPELINE CORE)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recording_id INTEGER NOT NULL,
        marker_id INTEGER NOT NULL,
        video_file TEXT,
        clip_start INTEGER NOT NULL,
        clip_end INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
        output_path TEXT,
        error_message TEXT,
        processing_started_at TIMESTAMP,
        processing_finished_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(marker_id) REFERENCES markers(id) ON DELETE CASCADE,
        FOREIGN KEY(recording_id) REFERENCES recordings(id) ON DELETE CASCADE
    )
    """)


    # EXPORTS
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS exports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clip_id INTEGER NOT NULL,
        platform TEXT NOT NULL CHECK(platform IN ('tiktok','instagram','youtube')),
        file_path TEXT,
        status TEXT NOT NULL DEFAULT 'pending' 
            CHECK(status IN ('pending','processing','completed','failed')),
        error_message TEXT,
        processing_started_at TIMESTAMP,
        processing_finished_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(clip_id) REFERENCES clips(id) ON DELETE CASCADE
    );
    """)

  
    # INDEXES (PERFORMANCE)
  

    # Markers
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_markers_recording 
    ON markers(recording_id)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_markers_recording_timestamp 
    ON markers(recording_id, timestamp)
    """)

    # Clips
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_clips_recording 
    ON clips(recording_id)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_clips_marker 
    ON clips(marker_id)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_clips_status 
    ON clips(status)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_clips_status_created 
    ON clips(status, created_at)
    """)

    # Exports
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_exports_clip 
    ON exports(clip_id)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_exports_status 
    ON exports(status)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_exports_status_created 
    ON exports(status, created_at)
    """)


    # ACCOUNTS — one row per connected social account
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        handle TEXT NOT NULL,
        zernio_account_id TEXT NOT NULL UNIQUE,
        profile_id TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_platform_handle
    ON accounts(platform, handle)
    """)

    # POSTS — one row per (clip, platform, account) fan-out unit
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clip_id INTEGER,
        platform TEXT NOT NULL,
        account_id INTEGER NOT NULL,
        render_path TEXT NOT NULL,
        caption TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','processing','published','failed','cancelled')),
        mode TEXT NOT NULL DEFAULT 'manual'
            CHECK(mode IN ('manual','scheduled')),
        scheduled_for TEXT,
        zernio_post_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 5,
        next_retry_at TEXT,
        last_error TEXT,
        claimed_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(clip_id) REFERENCES clips(id) ON DELETE SET NULL,
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_posts_status_due
    ON posts(status, scheduled_for, next_retry_at)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_posts_clip_id
    ON posts(clip_id)
    """)

    # CAPTIONS POOL — random rotation source for auto-posted clips
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS captions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        hashtags TEXT,
        weight INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_captions_active
    ON captions(active)
    """)

    conn.commit()
    conn.close()

    print("✅ Database initialized at:", DB_PATH)


if __name__ == "__main__":
    create_database()