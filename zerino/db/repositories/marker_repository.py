from zerino.db.db import get_connection

VALID_MARKER_KINDS = ("talking_head", "gameplay")


class MarkerRepository:

    def insert_marker(self, recording_id, streamer_id, timestamp, note=None,
                      kind: str = "talking_head"):
        if kind not in VALID_MARKER_KINDS:
            raise ValueError(
                f"invalid marker kind={kind!r}. valid: {VALID_MARKER_KINDS}"
            )
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO markers (recording_id, streamer_id, timestamp, kind, note)
        VALUES (?, ?, ?, ?, ?)
        """, (recording_id, streamer_id, timestamp, kind, note))

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
            SELECT id, recording_id, streamer_id, timestamp, kind, note
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
                "kind": row[4] or "talking_head",
                "note": row[5],
            }
            for row in rows
        ]

        conn.commit()
        conn.close()

        return markers
  
