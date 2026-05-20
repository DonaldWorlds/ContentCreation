# Zerino — Operator Runbook

The thing you read before every session so you don't have to remember.

## TL;DR — what runs the whole show

```bash
# Two long-running processes. Start both before you stream.
python -m zerino.capture.main                              # T1 — watches recordings/, listens for F8/F9
python -m zerino.publishing.batch.scheduler_runner         # T2 — dispatches scheduled posts to Zernio
```

If those two are running and you've got captions + accounts set up (see §3 / §4), you just press F8 / F9 while you stream and the rest happens automatically.

---

## 0. One-time setup (run once per machine — Mac AND Windows)

```bash
# Install ffmpeg (must include libass + the subtitles filter):
#   macOS:    brew install ffmpeg
#   Windows:  download a "full" build from gyan.dev or BtbN/FFmpeg-Builds,
#             extract, add the bin/ folder to PATH.
ffmpeg -version
ffmpeg -hide_banner -h filter=subtitles | head -1        # must NOT say "Unknown filter"

# Python deps (project ships a requirements.txt at the root)
python -m venv venv
source venv/bin/activate                                  # Mac/Linux
# venv\Scripts\activate                                   # Windows
pip install -r requirements.txt

# Secrets — put your Zernio API key in .env at the repo root
echo "ZERNIO_API_KEY=<your-key>" > .env

# Initialize the DB (idempotent — safe to re-run any time)
python -m zerino.db.migrate

# macOS only: grant Accessibility permission to your terminal AND the python
# binary you'll run from. System Settings → Privacy & Security → Accessibility.
# Without this F8/F9 hotkeys silently don't fire.
```

---

## 1. Pre-flight (every session, before you stream)

```bash
# 1.1  Healthcheck — confirms ffmpeg, libass, and ZERNIO_API_KEY are all good
python -m zerino.healthcheck

# 1.2  Confirm the scheduler daemon is alive (if launchd-managed on Mac)
launchctl list | grep zerino
# Else: open a second terminal and run it manually
python -m zerino.publishing.batch.scheduler_runner

# 1.3  See what's currently in the pool — captions + accounts
python -m zerino.cli.captions list
python -m zerino.cli.add_account list

# 1.4  Check there's at least one active caption AND one active account per
#      platform you'll post to. If either is empty, see §3 and §4 below.
```

### What "warm-up delays" mean on the first clip of a session

- **libass / subtitles filter probe** — ~0.5 s. Pre-warmed by `run_capture_healthcheck()` at daemon start, so the first clip doesn't pay it.
- **faster-whisper model load** — ~5 s the first time `process_clip_job` runs. Downloads the `small` model (~500 MB) on first-EVER run; loads it into memory thereafter. The "every time I run the test the same thing pops up first stream" symptom = this load. Normal. Subsequent clips reuse the cached model.

If you want to eliminate the first-clip latency entirely, hit one F8 marker early in the stream — the system processes it after the recording ends regardless of when in the recording you pressed.

---

## 2. Live workflow (during a stream)

1. **Start capture daemon** in a terminal: `python -m zerino.capture.main`.
2. **Start OBS recording** to `recordings/` (any `.mp4` / `.mkv` / `.mov` in that folder gets watched).
3. **During the stream, press hotkeys at moments worth clipping:**
   - **F8** = talking-head moment → renders as 1:1 square (face-fill).
   - **F9** = gameplay moment → renders as 9:16 split (face on top + game on bottom).
   - Hotkey heartbeat logs `marker hotkey pressed (kind=…, press#=N)`. If `press#` stops increasing while you're pressing keys, macOS Accessibility permission was probably revoked — re-grant in System Settings.
4. **Stop OBS recording.**

### Which recording feeds which clip layout

| Hotkey | Layout | VIDEO comes from | AUDIO + captions |
|---|---|---|---|
| **F8** | square (1:1) | the **face** recording (dual) → sharp face-fill. No face pair → centre-crop of the game recording (fallback). | always the game recording (mic + desktop mix) |
| **F9** | split (9:16) | **face** recording on top + **game** recording centred on bottom (dual). No face pair → both cropped from the one game recording (fallback). | always the game recording |

### **RECOMMENDED — dual-source recording (sharp face + centred game, zero bleed)**

This is how pro clippers get a sharp full face on top and a clean centred game on the bottom with **no facecam bleed**. You record the **webcam and the gameplay as two separate clean files**; the pipeline composites them. With dual-source, **your OBS scene geometry no longer matters for clip quality** — the webcam can sit anywhere in your live scene, because the clip never crops the overlay.

**One-time OBS setup:**
1. **Main recording = clean gameplay.** Your normal scene records to `recordings/` as today. It does NOT need the webcam removed — the clip ignores the overlay because the face comes from its own file. (For the absolute cleanest game panel, a scene with game-only is ideal, but not required.)
2. **Install the Source Record plugin** (Exeldro, free) → restart OBS.
3. **Add a Source Record filter on your webcam source:**
   - Right-click the webcam source → **Filters** → `+` → **Source Record**.
   - **Recording Path:** `<project>/recordings/face/`
   - **Filename Formatting:** match OBS's main pattern (e.g. `%CCYY-%MM-%DD %hh-%mm-%ss`).
   - **Record Mode:** **"Recording"** — starts/stops with the main recording, so the two files are time-synced.
   - **Resolution:** native webcam res (e.g. 1920×1080). Bigger is better; the pipeline downscales it to a sharp top panel.
4. Each Record press now produces **two** files:
   - `recordings/<timestamp>.mp4` — gameplay + the audio mix (this is the source of truth for audio + captions)
   - `recordings/face/<timestamp>.mp4` — webcam only

The pipeline pairs them automatically by start time (within ±15 s), so the filenames don't have to match exactly. The watchdog only watches `recordings/` (not `recordings/face/`), so the face file never triggers its own clip run.

**Verify dual-source is active:** after a recording finishes, the log shows `paired face recording <name> (mtime delta N.Ns)`. If instead you see `no face recording within 15s … — single-source clips`, the pipeline fell back (see below) — check that Source Record wrote a file into `recordings/face/`.

### Fallback — single-source split (no Source Record plugin)

If there's no paired face file, F9 falls back to cropping both panels out of the **one** game recording, using the crop boxes in [`zerino/processors/split.py`](zerino/processors/split.py):
- **Face region** (`FACE_BOX`, default `(0, 777, 546, 303)`) → upscaled to the top panel.
- **Gameplay region** (`GAME_BOX`, default full frame `(0, 0, 1920, 1080)`) → centre cover-cropped to the bottom panel.

In this mode the webcam **must** sit at the `FACE_BOX` location in your live scene (default: small overlay at bottom-left, x=0 y=777, 546×303), or the top panel shows the wrong region. The known limitation of single-source is that you cannot get sharp-face + centred-game + no-bleed all at once from one overlay recording — that's exactly what dual-source fixes. If your overlay lives elsewhere, edit `FACE_BOX` to match your OBS Transform (Position x,y + box w,h).

5. The capture daemon detects the file is stable (10 seconds of unchanged file size — see `STABILITY_POLL_COUNT` in `recording_service.py`) → finishes recording → triggers the clip worker → renders every marker into a clip → queues posts.
6. **Posting cadence:**
   - Clip #1 → immediate (Zernio publishes on its next pass, ≤ 5 s).
   - Clip #N (N > 0) → scheduled for `now + N * 120 minutes`.
   - All posts are SENT TO ZERNIO right away; Zernio decides publish-now vs schedule based on `scheduled_for`.

### What auto-runs when a recording finishes
```
recording finish detected
    → pair the game recording with recordings/face/<closest>.mp4 (±15s) — dual-source if found
    → clip windows generated (60s each: 10s before marker, 50s after; clamped at file start)
    → for each marker:
        → Whisper transcribes the audio slice (English forced, VAD on)
        → .ass caption file written (mint preset: red+yellow karaoke, Marker Felt on Mac)
        → ffmpeg one-pass render: cut + crop + scale + burn captions + encode
        → post row created per (platform, account) for the layout
        → first clip posts immediately, rest scheduled +120 min apart
```

---

## 3. Captions pool — set up your post text and hashtags

Each clip pulls a RANDOM caption from this pool. Add 10-20 variants so the feed doesn't feel repetitive.

```bash
# Add a caption with hashtags
python -m zerino.cli.captions add "Wait for it 👀" --hashtags "#warzone #cod #fyp"

# Add another, weighted higher (chosen 3x as often)
python -m zerino.cli.captions add "Banger play 🔥" --hashtags "#cod #viral" --weight 3

# See everything in the pool
python -m zerino.cli.captions list

# Stop using one without deleting (keeps history)
python -m zerino.cli.captions deactivate --id 4

# Re-enable
python -m zerino.cli.captions reactivate --id 4

# Permanently delete
python -m zerino.cli.captions remove --id 4
```

Hashtag format: space-separated, each starting with `#`. The string is passed through to Zernio as-is.

---

## 4. Accounts — register where to post

Get the `zernio_account_id` (24-char) from the Zernio dashboard.

```bash
# Register a new account
python -m zerino.cli.add_account add \
    --platform tiktok \
    --handle @yourhandle \
    --zernio-account-id <24-char-id>
# Supported platforms: tiktok, youtube_shorts, facebook_reels, twitter,
#                      instagram_reels, pinterest
# Optional: --profile-id <zernio-profile-id>
# Optional: --layout square     (square 1:1 instead of vertical 9:16)

# List
python -m zerino.cli.add_account list

# Update an existing account (change handle, zernio id, active flag, or layout)
python -m zerino.cli.add_account update --id 3 --handle @newhandle
python -m zerino.cli.add_account update --id 3 --layout square
python -m zerino.cli.add_account update --id 3 --active false

# Stop using (preserves the row + post history)
python -m zerino.cli.add_account deactivate --id 3

# Hard-delete (use --force if posts reference this account)
python -m zerino.cli.add_account remove --id 3
```

**Layouts per platform** — each account picks one of `vertical` / `square` / `split`. The render reuses output across platforms that share the same (account-chosen) layout. So if you have:

- TikTok @yourhandle, layout = vertical
- YouTube Shorts @yourhandle, layout = vertical
- Instagram Reels @yourhandle, layout = square

Then ONE vertical render goes to TikTok + Shorts, and ONE separate square render goes to Reels. Two encode passes, three posts.

### Adding multiple accounts to the same platform

Just run `add` again with a different `--handle` + `--zernio-account-id`. Both rows go active; both get every clip routed through Zernio fan-out.

```bash
python -m zerino.cli.add_account add --platform tiktok --handle @backup --zernio-account-id xxx
```

---

## 5. Quality verifier — confirm a render is good before you trust it

After a clip renders, before you let it post (or to debug a clip you didn't like), run:

```bash
python -m zerino.cli.quality_verify path/to/render.mp4
# Drops a directory of artifacts at quality_report/<clip_stem>/
```

The output dir contains:
- `summary.md` — every metric in one page
- `frame_*pct.png` — 4 keyframes (10/40/70/95 %)
- `caption_sample.png` — frame at a dialogue-active timestamp (caption visible)
- `spectrogram.png` — audio over time
- `loudness.json` — ebur128 integrated / LRA / true peak
- `encoding.json` — encoder + rate control, bitrate, levels/color, edge-energy
- `ffprobe.json` — full stream info
- `captions.json` — parsed .ass + Latin-script + geometry verdict

**The "Encoding & signal quality" section** is the one that answers *actual* quality. It reads everything FROM the output file (no source needed), so the numbers are directly comparable between two renders — that's how you tell if a pipeline change made a clip softer / more starved / more orange:

- **Encoder & rate control** — pulled from the x264/x265 settings stamped in the bitstream: encoder family, CRF/ABR/QP, and the tuning fingerprint (preset params, tune). If it's a hardware encoder (NVENC / VideoToolbox) the report says so and notes the internal RC isn't readable from the file.
- **Bitrate** — measured avg + per-second peak/min (ABR starvation shows as a big peak-to-min spread) and **bits-per-pixel** (the real over-compression signal; < 0.04 = starved).
- **Levels & color** — luma min/max (crushed blacks / blown highlights), chroma cast (V up + U down = the "orange skin" look), saturation (over-processing).
- **Detail / scaling / sharpening** — a sobel edge-energy index: very low = soft / over-denoised, very high = ringing halos from over-sharpening or a bad upscale.

Each problem becomes a one-line **flag** with the number and the fix.

**Targets to compare against** (see [`CLIPPING_QUALITY_PLAN.md`](CLIPPING_QUALITY_PLAN.md) for the rationale on each):

| Check | Target | Bad |
|---|---|---|
| Codec | h264 (High profile) | anything else |
| Encoder / RC | libx264 CRF 18 (or NVENC CQ ~20) | ABR, CRF > 22 |
| Bits per pixel | 0.05-0.20 | < 0.04 (starved) |
| Video bit_rate | 5-12 Mbps actual | < 4 Mbps or > 12 Mbps |
| Audio codec | aac | mp3, opus |
| Audio bit_rate | 192-256 kbps | < 96 kbps |
| Audio sample_rate | 48000 | other |
| Color tags | bt709 × 4 explicit | "unknown" |
| Luma min / max | ~16 / ~235 | YMIN < 12 (crush), YMAX > 245 (clip) |
| Chroma (U / V avg) | near 128 | V up + U down (warm/orange cast) |
| Integrated loudness | -14 ± 1 LUFS | < -16 or > -12 |
| True peak | < -1.0 dBTP | > -0.5 dBTP |
| Caption Latin-script | PASS | FAIL (Korean/Welsh/Maori) |
| Caption geometry | PASS on all 5 checks | any FAIL |

If the verifier flags something, it tells you exactly what. Don't trust your eyes — trust the numbers and inspect the frame.

---

## 6. Ad-hoc clipping (no live stream — clip from an existing file)

```bash
# Whole file, all active platforms, random caption from pool
python -m zerino.cli.clip_file --file path/to/video.mp4

# A specific window
python -m zerino.cli.clip_file --file video.mp4 --start 30 --end 90

# Specific platforms + explicit caption
python -m zerino.cli.clip_file --file video.mp4 \
    --platforms tiktok,youtube_shorts \
    --caption "Wait for the end"

# Ad-hoc DUAL-SOURCE test (no capture daemon): pair a game file with a face
# file and force the layout. Audio + captions still come from --file (game).
python -m zerino.cli.clip_file --file game.mp4 --face-file face.mp4 \
    --layout split --start 30 --end 90
python -m zerino.cli.clip_file --file game.mp4 --face-file face.mp4 \
    --layout square --start 30 --end 90
```

Useful for:
- Testing pipeline changes without recording in OBS
- Clipping a downloaded VOD
- Re-rendering a window from a recording that's already on disk
- Verifying the dual-source split/square render with two real files

---

## 7. Disk cleanup

`recordings/`, `clips/`, and `renders/` grow forever without this.

```bash
# Show what WOULD be deleted (never deletes)
python -m zerino.cli.cleanup all --dry-run

# Defaults: recordings >7d, clips >30d, renders >30d (renders with pending
# posts are skipped automatically so you don't orphan a dispatch)
python -m zerino.cli.cleanup all

# Custom thresholds
python -m zerino.cli.cleanup recordings --days 3
python -m zerino.cli.cleanup renders --days 14
```

---

## 8. Troubleshooting (the same problems, the same answers)

### Pressing F8 / F9 does nothing
1. Check `logs/zerino.log` for `marker hotkey listener heartbeat: presses=N` lines.
2. If `presses=` stays at 0 across multiple heartbeats while you've been pressing keys, **macOS Accessibility permission was revoked.** Settings → Privacy & Security → Accessibility → re-grant for your terminal AND the python binary path.
3. On Windows, pynput doesn't need a permission — but the listener thread might be dead. Check for an exception in `logs/zerino.log` under `zerino.capture.marker_worker`.

### Recording stopped but no clip appeared
1. Check `recordings/` — is the file actually written? OBS sometimes buffers without flushing.
2. Check `logs/zerino.log` for `[end] Recording stopped (stable for 10s)`. If you see `[stable] N/20 checks` and N stays low, the file is still growing.
3. If the recording is under 1 KB it's rejected as "OBS not started." Re-record.

### Captions appear in the wrong language
- The Whisper pipeline forces English. If you're getting non-English, Whisper landed on a different language *despite* the force (rare, but happens on heavily-music-bedded content).
- Check `logs/zerino.log` for `whisper detected lang=XX ... but we forced en — dropping segments`. That's the safety net firing — clip gets no captions instead of foreign-script captions.
- If the language guard fires on every clip, the source audio is too noisy / music-heavy for Whisper at the `small` model size. Bump up to `medium` (one-line change in `_captions.py`).

### Captions overlap the speaker's face
- Run `quality_verify` on the render. The Captions section reports geometry pass/fail with the exact pixel reasoning.
- The MarginV constant is in `zerino/processors/_captions.py` (`LAYOUT_MARGIN_V` dict). Current values: vertical 380, square 240, split 1020. Move lower (larger number) for higher placement; move higher (smaller number) for lower placement.

### Audio sounds too quiet / pumpy / breathy
- Run `quality_verify`. The Loudness section reports integrated LUFS.
- Target is -14 LUFS for TikTok / IG. If integrated is below -16, the leveler chain needs tuning.
- Constant to adjust: `SPEECH_LEVELER` in `zerino/ffmpeg/export_generator.py`. Increase `dynaudnorm p=` (currently 0.95) for louder, but don't exceed 1.0.

### Render took forever / hung
- libx264 -slow on a 60s clip takes ~10-15 s on Apple Silicon. Longer if the source is 4K.
- If you have an NVIDIA GPU on Windows, NVENC is automatically used and renders are ~3-5x faster.
- If a render genuinely hangs (no progress in 60 s), kill the ffmpeg process and check `logs/zerino.log` for the actual command line — copy/paste it into a terminal to see ffmpeg's stderr directly.

### A post was queued but never showed up on Zernio
- Check `logs/zerino.log` for `pipeline: queued post id=N` and then `dispatch: post id=N platform=…` follow-ups.
- The scheduler claims rows atomically — if it crashed mid-dispatch, the row is stuck in `processing` state. Manual fix: `UPDATE posts SET status='pending' WHERE status='processing'` (the scheduler will retry).

---

## 9. Reference

### Paths

| What | Where |
|---|---|
| OBS recordings (input) | `recordings/` |
| Rendered clips (output) | `renders/<layout>/` where layout = `vertical`/`square`/`split` |
| Caption .ass sidecars | next to each rendered `.mp4` (kept post-burn since Stage 4) |
| Logs | `logs/zerino.log` (rotating, 5 MB × 5) |
| Database | `zerino.db` (SQLite) |
| Quality verifier reports | `quality_report/<clip_stem>/` |
| Env / secrets | `.env` at repo root |

### Key documents in the repo

| File | Purpose |
|---|---|
| `RUNBOOK.md` | This file — how to operate the system |
| `CLIPPING_QUALITY_PLAN.md` | The 40-item fix plan with rationale per item — read this before changing anything in the render path |
| `HANDOFF_TO_ZERINO.md` | Historical context (Reap pivot, original Phase 1-4 plan) |
| `ops/README.md` | launchd / Task Scheduler setup for the scheduler daemon |

### Hotkeys

| Key | Marker kind | Renders as |
|---|---|---|
| F8 | `talking_head` | square 1:1 (face-fill) |
| F9 | `gameplay` | split (face on top + game on bottom) |

### Constants you'll probably want to tweak someday

Editing these doesn't break anything — they're tuning knobs. See `CLIPPING_QUALITY_PLAN.md` for rationale.

| Constant | File | Default | Effect |
|---|---|---|---|
| `CLIP_DURATION` | `zerino/capture/services/clip_service.py` | 60 | Total clip length in seconds |
| `PRE_BUFFER` | same | 10 | Seconds of footage BEFORE the marker |
| `STABILITY_POLL_COUNT` | `zerino/capture/services/recording_service.py` | 20 | × 0.5 s = 10 s of file-size stability before finish |
| `FONT_NAME` / `MARGIN_V` | `zerino/processors/_captions.py` | Marker Felt / 380 | Caption font + position |
| `WHISPER_MODEL_SIZE` | same | "small" | Bigger = slower + more accurate (try "medium" for non-native speakers) |
| `DEFAULT_INTERVAL_MINUTES` | `zerino/publishing/clip_to_posts.py` | 120 | Minutes between scheduled clips in a batch |
| `LIBX264_CRF` / `NVENC_CQ` | `zerino/ffmpeg/export_generator.py` | 18 / 20 | Lower = better quality + larger files |

---

## 10. Cross-platform notes (Mac AND Windows)

This system runs on both. Some behaviors differ:

- **Caption font** — Marker Felt is preinstalled on Mac only. On Windows, libass silently falls back to DejaVu Sans (captions still work, just different look). To make both machines render the same font, bundle a TTF in `zerino/assets/fonts/` and pass `fontsdir=` to the subtitles filter.
- **Audio encoder** — Mac uses `aac_at` (Apple AudioToolbox, cleaner). Windows uses native `aac` with `-aac_coder twoloop`. Both predictable bitrate; the Mac one sounds marginally cleaner on plosives.
- **Video encoder** — Windows with NVIDIA GPU uses NVENC (faster). Both Mac and Windows-without-NVIDIA use libx264. NVENC is competitive with libx264 -slow on most content; libx264 wins on the split-face quadrant which is why split forces libx264 regardless.
- **OBS settings** — same on both. Recommended in `ops/README.md`.
- **Daemon autostart** — Mac uses launchd (`ops/com.zerino.scheduler.plist`); Windows uses Task Scheduler. See `ops/README.md` for both.
