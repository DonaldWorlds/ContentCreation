from zerino.db.db import get_connection
from zerino.db.repositories.streamer_repository import *
from zerino.db.repositories.recording_repository import *
from zerino.db.repositories.marker_repository import MarkerRepository

class ClipRepository:
    
    # Create Clip 
    def create_clip(self, recording_id, marker_id, start, end, video_file=None):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO clips (
            recording_id, marker_id, video_file, clip_start, clip_end, status
        )
        VALUES (?, ?, ?, ?, ?, 'pending')
        """, (recording_id, marker_id, video_file, start, end))

        clip_id = cursor.lastrowid
        
        conn.commit()
        conn.close()

        return clip_id
    
    # Read Clips 
    def get_pending_clips(self, limit=10):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, recording_id, marker_id, video_file, clip_start, clip_end, status
            FROM clips
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()
        return rows
    
    def get_clips_by_recording(self, recording_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT * FROM clips WHERE recording_id = ?
        """, (recording_id,))

        rows = cursor.fetchall()

        conn.commit()
        conn.close()
        return rows
    
    def get_clips_by_marker(self, marker_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT * FROM clips WHERE marker_id = ?
        """, (marker_id,))

        rows = cursor.fetchall()

        conn.commit()
        conn.close()
        return rows
    
    def get_clip_by_id(self, clip_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        SELECT * FROM clips WHERE id = ?
        """, (clip_id,))

        row = cursor.fetchone()

        conn.commit()
        conn.close()
        return row
    
    # state management 
    def mark_processing(self, clip_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE clips
        SET status = 'processing',
            processing_started_at = CURRENT_TIMESTAMP
        WHERE id = ?
        AND status = 'pending'
        """, (clip_id,))

        conn.commit()
        conn.close()

    def mark_completed(self, clip_id, video_file):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE clips
        SET status = 'completed',
            video_file = ?,
            processing_finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (video_file, clip_id))

        conn.commit()
        conn.close()
    
    def mark_failed(self, clip_id, error_message):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        UPDATE clips
        SET status = 'failed',
            error_message = ?,
            processing_finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (error_message, clip_id))

        conn.commit()
        conn.close()
    
    # DELETE 
    def delete_clip(self, clip_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM clips WHERE id = ?", (clip_id,))

        conn.commit()
        conn.close()
    
    def delete_clips_by_recording(self, recording_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        DELETE FROM clips WHERE recording_id = ?
        """, (recording_id,))

        conn.commit()
        conn.close()

    def delete_clips_by_marker(self, marker_id):
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
        DELETE FROM clips WHERE marker_id = ?
        """, (marker_id,))

        conn.commit()
        conn.close()

    def clip_exists(self, recording_id: int, start: float, end: float) -> bool:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1 FROM clips
            WHERE recording_id = ?
            AND clip_start = ?  
            AND clip_end = ?
            LIMIT 1
        """, (recording_id, start, end))

        result = cursor.fetchone()

        conn.commit()
        conn.close()

        return result is not None


if __name__ == "__main__":
    print("=== FULL PIPELINE TEST ===")

    marker_repo = MarkerRepository()
    clip_repo = ClipRepository()
    streamer = StreamerRepository()

    # =========================
    # 1. CREATE STREAMER
    # =========================
    streamer_id = streamer.create_streamer("TestStreamer", "twitch")
    print("Streamer ID:", streamer_id)

    # =========================
    # 2. CREATE RECORDING
    # =========================
    recording_id = create_recording("video_test.mp4")
    print("Recording:", get_recording(recording_id))

    # =========================
    # 3. CREATE MARKERS
    # =========================
    marker_repo.insert_marker(recording_id, streamer_id, 120, "First moment")
    marker_repo.insert_marker(recording_id, streamer_id, 300, "Second moment")

    markers = marker_repo.get_markers_by_recording(recording_id)
    print("Markers:", markers)

    # =========================
    # 4. CREATE CLIPS FROM MARKERS
    # =========================
    for marker in markers:
        marker_id = marker[0]
        timestamp = marker[1]

        # simple clip window (example logic)
        start = max(0, timestamp - 10)
        end = timestamp + 20

        clip_id = clip_repo.create_clip(
            recording_id,
            marker_id,
            start,
            end,
            video_file="video_test.mp4"
        )

        print(f"Created clip {clip_id} from marker {marker_id}")

    # =========================
    # 5. FETCH CLIPS
    # =========================
    clips = clip_repo.get_clips_by_recording(recording_id)
    print("Clips:", clips)

    # =========================
    # 6. PROCESS ONE CLIP
    # =========================
    if clips:
        clip_id = clips[0][0]

        print("\n--- Processing Clip ---")

        clip_repo.mark_processing(clip_id)

        # simulate success
        clip_repo.mark_completed(clip_id, "output/clip1.mp4")

        updated_clip = clip_repo.get_clip_by_id(clip_id)
        print("Updated Clip:", updated_clip)

    # =========================
    # 7. TEST FAILURE
    # =========================
    if len(clips) > 1:
        clip_id = clips[1][0]

        print("\n--- Simulating Failure ---")

        clip_repo.mark_processing(clip_id)
        clip_repo.mark_failed(clip_id, "FFmpeg crashed")

        failed_clip = clip_repo.get_clip_by_id(clip_id)
        print("Failed Clip:", failed_clip)

    # =========================
    # 8. CLEANUP TEST (CASCADE)
    # =========================
    print("\n--- Testing Cascade Delete ---")

    delete_recording(recording_id)

    markers_after = marker_repo.get_markers_by_recording(recording_id)
    clips_after = clip_repo.get_clips_by_recording(recording_id)

    print("Markers after delete (should be empty):", markers_after)
    print("Clips after delete (should be empty):", clips_after)

    print("\n=== TEST COMPLETE ===")



    




    


 