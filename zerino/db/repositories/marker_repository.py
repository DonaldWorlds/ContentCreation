from zerino.db.db import get_connection



class MarkerRepository:

    def insert_marker(self, recording_id, streamer_id, timestamp, note=None):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO markers (recording_id, streamer_id, timestamp, note)
        VALUES (?, ?, ?, ?)
        """, (recording_id, streamer_id, timestamp, note))

        marker_id = cursor.lastrowid 

        conn.commit()
        conn.close()

        return marker_id

    def get_markers_by_recording(self, recording_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT id, timestamp, note
        FROM markers
        WHERE recording_id = ?
        """, (recording_id,))

        rows = cursor.fetchall()  

        conn.commit()
        conn.close()
        return rows

    def delete_marker(self, marker_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM markers WHERE id = ?", (marker_id,))

        conn.commit()
        conn.close()

    def get_markers_for_recording(self, recording_id: int) -> list:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, recording_id, streamer_id, timestamp, note
            FROM markers
            WHERE recording_id = ?
            ORDER BY timestamp ASC
        """, (recording_id,))

        rows = cursor.fetchall()

        markers = [
            {
                "id": row[0],
                "recording_id": row[1],
                "streamer_id": row[2],
                "timestamp": row[3],
                "note": row[4]
            }
            for row in rows
        ]

        conn.commit()
        conn.close()

        return markers
  
