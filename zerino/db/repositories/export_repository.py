from zerino.db.db import get_connection


class ExportRepository:

    # -------------------------
    # CREATE
    # -------------------------

    def create_export(self, clip_id: int, platform: str) -> int:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO exports (clip_id, platform, status)
            VALUES (?, ?, 'pending')
        """, (clip_id, platform))

        export_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return export_id

    def create_exports_for_clip(self, clip_id: int):
        platforms = ["tiktok", "instagram", "youtube"]

        conn = get_connection()
        cursor = conn.cursor()

        for platform in platforms:
            cursor.execute("""
                INSERT INTO exports (clip_id, platform, status)
                VALUES (?, ?, 'pending')
            """, (clip_id, platform))

        conn.commit()
        conn.close()

    # -------------------------
    # FETCHING (Worker uses this)
    # -------------------------

    def get_pending_exports(self):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT e.* 
            FROM exports e
            JOIN clips c ON e.clip_id = c.id
            WHERE e.status = 'pending'
            AND c.status = 'completed'
            ORDER BY e.created_at ASC
            LIMIT 1
        """)

        row = cursor.fetchone()
        conn.close()

        return row

    def get_exports_by_clip(self, clip_id: int):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM exports
            WHERE clip_id = ?
            ORDER BY created_at ASC
        """, (clip_id,))

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_export_by_id(self, export_id: int):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM exports
            WHERE id = ?
        """, (export_id,))

        row = cursor.fetchone()

        conn.close()

        return row

    # -------------------------
    # STATE MANAGEMENT
    # -------------------------

    def mark_processing(self, export_id: int):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE exports
            SET status = 'processing',
                processing_started_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (export_id,))

        conn.commit()
        conn.close()

    def mark_completed(self, export_id: int, output_path: str):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE exports
            SET status = 'completed',
                file_path = ?,
                processing_finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (output_path, export_id))  # ✅ correct order

        conn.commit()
        conn.close()

    def mark_failed(self, export_id: int, error_message: str):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE exports
            SET status = 'failed',
                error_message = ?,
                processing_finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (error_message, export_id))

        conn.commit()
        conn.close()

    # -------------------------
    # METADATA UPDATES
    # -------------------------
    def update_file_path(self, export_id: int, file_path: str):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE exports
            SET file_path = ?
            WHERE id = ?
        """, (file_path, export_id))
        conn.commit()
        updated = cursor.rowcount
        conn.close()
        return updated

    # -------------------------
    # DELETE (mainly for dev/debug)
    # -------------------------

    def delete_export(self, export_id: int):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM exports WHERE id = ?", (export_id,))

        conn.commit()
        conn.close()

    def delete_exports_by_clip(self, clip_id: int):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM exports WHERE clip_id = ?
        """, (clip_id,))

        conn.commit()
        conn.close()

    def export_exists(self, clip_id: int, platform: str) -> bool:
        """
        Returns True if an export already exists for this clip and platform
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM exports WHERE clip_id = ? AND platform = ? LIMIT 1",
            (clip_id, platform)
        )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists