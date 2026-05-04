import sqlite3
from pathlib import Path
from zerino.db.db import get_connection

class RecordingRepository:

    def create_recording(self, filename):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO recordings (filename, status)
        VALUES (?, 'pending')
        """, (filename,))

        recording_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return recording_id

    def get_recording(self, recording_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,))
        row = cursor.fetchone()

        conn.commit()
        conn.close()
        return row
    
    def get_by_filename(self, filename):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM recordings WHERE filename = ?",
            (filename,)
        )
        row = cursor.fetchone()

        conn.commit()
        conn.close()
        return row

    def update_status(self, recording_id, status):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE recordings
        SET status = ?
        WHERE id = ?
        """, (status, recording_id))

        conn.commit()
        conn.close()

    

    def delete_recording(self, recording_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))

        conn.commit()
        conn.close()
    
    def mark_recording_processing(self, recording_id):
        self.update_status(recording_id, "processing")

    def mark_recording_completed(self, recording_id):
        self.update_status(recording_id, "completed")

    def mark_recording_failed(self, recording_id):
        self.update_status(recording_id, "failed")
    


if __name__ == '__main__':
    print("=== TESTING RECORDING REPO ===")

    # CREATE
    rid = create_recording("test_video.mp4")
    print("Created recording ID:", rid)

    # READ
    rec = get_recording(rid)
    print("Fetched recording:", rec)

    # UPDATE
    update_recording_status(rid, "processing")
    rec = get_recording(rid)
    print("Updated recording:", rec)

    # DELETE
    delete_recording(rid)
    rec = get_recording(rid)
    print("After delete (should be None):", rec)

    all_records = get_all_recordings()
    print("All recordings:", all_records)

    for rec in all_records:
        delete_recording(rec[0])

    print("After deleting all:", get_all_recordings())


