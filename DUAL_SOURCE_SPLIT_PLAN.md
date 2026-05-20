# Plan — Dual-source split clips (separate face + game recordings)

## Context

The split (F9) clip stacks the streamer's face on top of gameplay on the bottom. Today both panels are cropped from a **single** OBS recording where the facecam is overlaid on the gameplay. The live test exposed a hard geometry constraint: from one overlay recording you cannot get **centered game + sharp face + no facecam bleed** all at once — the facecam either bleeds into the centered game crop, or you shrink it (soft face) / shift the game crop (off-center). Math, not a bug.

The professional fix (what big clippers do): capture the **facecam and gameplay as two separate clean sources** and composite them. Then:
- Game source = clean full-screen gameplay (no facecam overlay) → centered crop, no bleed.
- Face source = full webcam → **downscaled** to fill the top panel → sharp (no upscale starvation).

The user will produce the two files via the **OBS Source Record plugin** (Exeldro, free). This plan covers the OBS setup, the file-pairing logic, and the pipeline changes to consume two inputs — while keeping single-source clips (F8/square, vertical, ad-hoc `clip_file`) working unchanged.

Intended outcome: F9 split clips with a sharp full webcam on top and centered full gameplay on the bottom, zero facecam bleed.

---

## OBS setup (operator side — prerequisite)

1. **Main recording = clean gameplay.** Build/duplicate a scene that has the **Game Capture only** (NO facecam overlay in it). OBS records this to the main recording path → lands in `recordings/`.
2. **Install Source Record plugin** (Exeldro) → restart OBS.
3. **Add a Source Record filter on the webcam source:**
   - Right-click the webcam source → Filters → `+` → **Source Record**.
   - **Recording Path:** `<project>/recordings/face/`
   - **Filename Formatting:** match OBS's main pattern, e.g. `%CCYY-%MM-%DD %hh-%mm-%ss`
   - **Record Mode:** "Recording" (starts/stops with the main recording, so the two files are time-synced).
   - Resolution: native webcam res (e.g. 1920×1080) — bigger is better; we downscale.
4. Result on each Record press:
   - `recordings/<timestamp>.mp4` — clean gameplay (main)
   - `recordings/face/<timestamp>.mp4` — webcam only

Both start together → frame-synced → F8/F9 marker timestamps map to both.

**Note:** the watchdog watches `recordings/` non-recursively, so files in `recordings/face/` do NOT trigger their own clip runs — they're only looked up as the pair for a finished game recording.

---

## Architecture / data flow

```
OBS ─┬─ recordings/<ts>.mp4         (clean gameplay, has the audio mix)
     └─ recordings/face/<ts>.mp4    (webcam only)

Game recording finishes (watchdog) ─> ClipService.process_recording
  ClipService pairs the game file with the closest face file in recordings/face/
  For each F9 marker:
    ClipJob(source_path=<game>, face_source_path=<face>, layout='split')
  For each F8 / vertical marker:
    ClipJob(source_path=<game>, face_source_path=None)   # unchanged

Router.route_clip_job ─> SplitProcessor.process_clip_job
  if job.face_source_path is set:
    ExportGenerator.run_dual_split_export(game, face, ...)   # NEW two-input path
  else:
    ExportGenerator.run_split_export_from_source(game, ...)  # existing single-source fallback

Two-input render (filter_complex):
  [0:v] game  -> centered crop -> scale cover -> [game_panel]   (bottom, 1080x960)
  [1:v] face  -> scale cover    -> [face_panel]                 (top, 1080x960)
  [face_panel][game_panel] vstack -> [stacked]
  -> watermark overlay -> subtitles burn -> encode
  audio: -map 0:a  (from the GAME recording — it has the mic+desktop mix)
  captions: transcribed from the GAME recording audio (unchanged)
```

---

## File pairing strategy

When a game recording `recordings/<name>.mp4` finishes, find its face partner in `recordings/face/`:

- **Pair by closest start time**, not exact filename — the two outputs may differ by a second and the plugin's filename pattern may not match exactly. Compare the game file's mtime (or parsed timestamp) against each `recordings/face/*.mp4` mtime; pick the closest within a window (default ±15 s).
- If a match is found → split clips use dual-source.
- If **no** face file within the window → log a WARN and fall back to the existing single-source split (crop face from the game recording, as today). No hard failure — the clip still renders.
- Pairing happens in `ClipService.create_clips` when the ClipJob is built, so the resolved face path rides on the job.

Why time-based pairing: robust to whatever filename the Source Record plugin emits, and to the slight start-time skew between the two outputs.

---

## Code changes

### 1. `zerino/models.py` — ClipJob gets a face source

Add field:
```python
face_source_path: Path | None = None
```
Docstring: "For split clips produced from separate face + game recordings, the path to the clean webcam recording. `source_path` is then the clean gameplay recording. None = single-source clip (face cropped from source_path, or non-split layout)."

### 2. `zerino/capture/services/clip_service.py` — pair the face file

In `create_clips` (around [clip_service.py:96-104](zerino/capture/services/clip_service.py#L96)), after resolving `source_path`:
```python
face_source_path = self._find_face_pair(source_path)  # NEW helper
```
New helper `_find_face_pair(game_path)`:
- Look in `RECORDINGS_DIR / "face"` for `*.mp4`.
- Return the one whose mtime is closest to `game_path`'s mtime within `FACE_PAIR_WINDOW_SEC = 15`.
- Return None if none / dir missing.

Then build split jobs with `face_source_path=face_source_path` (only set it; vertical/square ignore it). Add `FACE_RECORDINGS_DIR = RECORDINGS_DIR / "face"`.

### 3. `zerino/processors/split.py` — pass the face source through

`SplitProcessor.process_clip_job` reads `job.face_source_path`. If set + exists, call the new dual-input export; else the existing single-source export. Caption transcription stays sourced from `job.source_path` (the game recording with audio).

### 3b. `zerino/processors/square.py` — F8/square uses the FACE source

When `job.face_source_path` is set + exists, the square render crops/scales the **face recording** (clean webcam) to fill 1080×1080 — a sharp face-only square clip. This fixes the live-test problem where F8/square cropped the game launcher (because the face was a small overlay). Falls back to the current single-source center-crop when no face source (pure talking-head streamers whose single recording IS the face).

Caption transcription for the square still comes from `job.source_path` audio (the game recording carries the mic + desktop mix). So an F8 face-only square clip still gets captioned from what was said.

Workflow this enables:
- **F8 (talking_head → square)** → face-only 1:1 clip from the face recording.
- **F9 (gameplay → split)** → face-top + centered-game-bottom from both recordings.
- **Vertical** layout (account-default, not in the F8/F9 flow) stays single-source — VerticalProcessor ignores `face_source_path` for now. Easy to extend later if a vertical face-only clip is wanted.

### 4. `zerino/ffmpeg/export_generator.py` — new dual-input split render

New method `run_dual_split_export_from_source(game_path, face_path, output_path, start, end, canvas_width, canvas_height, platform, subtitles_path, margin_v_for_subs)`:
- Two inputs, each with the SAME seek (`-ss before -i (start-pre_roll) ... -ss after -i pre_roll -t duration`) applied per-input so both slices align.
- `filter_complex`:
  - `[0:v]` game → `_video_normalize_prefix` + **centered** crop of the full frame → cover-scale to `1080×half_h` → `[game_panel]` (no facecam to avoid; dead-center).
  - `[1:v]` face → `_video_normalize_prefix` + cover-scale to `1080×half_h` (downscale from the full webcam → sharp) → `[face_panel]`.
  - `[face_panel][game_panel]vstack=inputs=2[stacked]`.
  - watermark overlay (seam position) then subtitle burn (existing order: watermark first, captions on top).
  - `-map 0:a` (game audio), `-af` leveler, encoder = SPLIT_VIDEO_ENCODER (libx264), color tags, `-ar`.
- Scalers via `_pick_scaler` per panel (face is downscale → lanczos; game is downscale-ish → lanczos).
- Reuse all existing constants (SPLIT_*_BITRATE, SPLIT_VIDEO_ENCODER_ARGS, COLOR_TAG_ARGS, AUDIO_*).

Game centered crop: since the game source is clean (no facecam), `GAME_BOX` is no longer needed for dual-source — crop the full frame `(0,0,src_w,src_h)` and cover-scale centered. `FACE_BOX` is also unused in dual-source (the face file IS the face). Keep both constants for the single-source fallback path.

### 5. `zerino/router.py` — no signature change

`route_clip_job` already passes the whole `ClipJob` to processors; `face_source_path` rides along. The transcribe-once cache still transcribes from `job.source_path` (game audio). No change needed beyond confirming the job flows through.

### 6. Backward compatibility

- **Vertical / square / ad-hoc `clip_file`**: `face_source_path=None` → all existing single-source paths unchanged.
- **Split with no face pair**: falls back to `run_split_export_from_source` (today's behavior) → no regression if the user hasn't set up Source Record yet.
- **`clip_file` CLI**: gains an optional `--face-file <path>` so ad-hoc dual-source splits can be tested without the capture daemon.

---

## Verification

1. **Synthetic two-source test (local, no OBS):** generate two labeled 1920×1080 sources — a "GAME" pattern with a centered CENTER-ACTION marker, and a "FACE" pattern (distinct color + "FACE" text). Run `run_dual_split_export_from_source` and read a frame: top half = FACE source (sharp, full), bottom half = GAME centered, NO bleed of either into the other.
2. **Pairing unit test:** drop two files with near-equal mtimes in `recordings/` + `recordings/face/`, confirm `_find_face_pair` matches; drop a face file 30 s off, confirm it does NOT match (falls back).
3. **Fallback test:** split job with `face_source_path=None` still renders via the single-source path.
4. **Real OBS test (operator):** set up Source Record per above, record a short Fortnite session, F9 a moment, confirm the rendered split has sharp face (downscaled webcam) + centered gameplay + no bleed. Push the quality report for review.

---

## Open items to confirm during build

- **Filename format from Source Record plugin** — pairing is time-based so it's robust, but confirm the face files actually land in `recordings/face/` and start within ~1 s of the main recording.
- **Time-sync precision** — if the two files start more than a frame apart, captions/markers could drift between panels. v1 assumes Source Record's "start with recording" keeps them synced. If drift appears, add a per-pair offset (compare the two files' creation timestamps and shift the face seek).
- **Face file audio** — ignored; we use the game recording's audio (`-map 0:a`). The Source Record face file may carry the webcam mic — we do NOT use it (avoids the echo/double-audio problem entirely).

---

## Files touched

- `zerino/models.py` — `ClipJob.face_source_path`
- `zerino/capture/services/clip_service.py` — `_find_face_pair`, `FACE_RECORDINGS_DIR`, set face path on ALL jobs from a paired recording (split + square use it; vertical ignores it)
- `zerino/processors/split.py` — route to dual vs single export
- `zerino/processors/square.py` — F8/square uses the face source when set
- `zerino/ffmpeg/export_generator.py` — `run_dual_split_export_from_source` + a face-source square render path (can reuse `run_export_from_source` with the face file as the source for square)
- `zerino/cli/clip_file.py` — optional `--face-file` for ad-hoc testing
- `RUNBOOK.md` — Source Record setup steps + the F8/F9 source-usage table

## Pipeline safety (why existing flows don't break)

- `face_source_path` defaults to None; all current callers are unaffected.
- Dual paths are gated on `face_source_path` being set; otherwise the exact current code runs.
- Split with no face pair → existing `run_split_export_from_source` (no regression).
- Square with no face pair → existing center-crop (no regression).
- Vertical + ad-hoc `clip_file` → untouched.
- `_find_face_pair` is defensive: returns None on any error → fallback.
- Verification step 3 explicitly re-tests the single-source vertical / square / split-fallback paths to confirm no regression before shipping.
