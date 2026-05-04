from zerino.db.db import get_connection

class StreamerRepository:

    def create_streamer(self, name, platform):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO streamers (name, platform)
        VALUES (?, ?)
        """, (name, platform))

        streamer_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return streamer_id

    def get_streamer(self, streamer_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT * FROM streamers WHERE id = ?
        """, (streamer_id,))

        row = cursor.fetchone()

        conn.commit()
        conn.close()

        return row
    
    def get_streamer_by_name(self, name):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT * FROM streamers WHERE name = ?
        """, (name,))

        row = cursor.fetchone()

        conn.commit()
        conn.close()

        return row

    def get_all_streamers(self):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM streamers")
        rows = cursor.fetchall()

        conn.commit()
        conn.close()
        return rows

    def delete_streamer(self, streamer_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM streamers WHERE id = ?", (streamer_id,))

        conn.commit()
        conn.close()

if __name__ == "__main__":
    print("=== TESTING STREAMER REPO ===")

    repo = StreamerRepository()

    # CREATE
    sid = repo.create_streamer("TestStreamer", "twitch")
    print("Created:", sid)

    # READ
    print("Fetched:", repo.get_streamer(sid))

    # GET ALL
    print("All:", repo.get_all_streamers())

    # DELETE
    repo.delete_streamer(sid)
    print("After delete:", repo.get_streamer(sid))