# Zerino — Automated Video Clipping & Posting Pipeline

An intelligent video clipping system that automatically captures, processes, and publishes game highlights and streaming moments. Built for content creators who stream on OBS and post to TikTok/YouTube Shorts.

```
🎬 OBS recording → 🔖 Hotkey marker (F8/F9) → ✂️ Auto-clip → 🎨 ffmpeg render → 📤 Post to Zernio
```

**Status:** ✅ Phase 2 complete (Fortnite highlight detection built & validated). Full hands-off pipeline merged, shipping OFF behind feature flags for final integration testing.

---

## What It Does

### Manual Clipping (Live Now)
Press **F8** or **F9** while streaming in OBS to create clips instantly:
- **F8** → Talking-head moment (1:1 square, face-fill)
- **F9** → Gameplay moment (9:16 split, face on top + game on bottom)

The system captures the marker, creates a 60-second clip (10s before the marker, 50s after), generates a styled output, and publishes it to your Zernio account.

### Auto-Detection (Ready to Ship)
Automatically detects game highlights (kill streaks, eliminations, multi-kills) and creates clips without manual intervention. Currently tuned for Fortnite; architecture is game-agnostic.

**Feature status:** Detection runs + emits events ✅ | Rendering tested ✅ | Auto-posting ready but disabled by default for safety

---

## Quick Start

### Prerequisites
- **macOS** (capture daemon) or **Windows** (detection/rendering batch)
- **OBS** recording to `./recordings/`
- **ffmpeg** with libass subtitle support
- **Python 3.8+**

### One-Time Setup (All Platforms)

```bash
# Install ffmpeg (must include libass + subtitles filter)
# macOS: brew install ffmpeg
# Windows: gyan.dev or BtbN/FFmpeg-Builds → add to PATH

# Create venv and install deps
python -m venv venv
source venv/bin/activate          # Mac/Linux
# venv\Scripts\activate            # Windows

pip install -r requirements.txt

# Add your Zernio API key to .env
echo "ZERNIO_API_KEY=<your-key>" > .env

# Initialize the database
python -m zerino.db.migrate
```

**macOS only:** Grant Accessibility permission to your terminal and Python binary:
- System Settings → Privacy & Security → Accessibility
- Add both your terminal app and the Python executable you'll run

### Pre-Stream Check

```bash
# Verify everything is configured correctly
python -m zerino.healthcheck

# Confirm daemon scheduler is running (or start it)
python -m zerino.publishing.batch.scheduler_runner &

# Check captions and accounts are set up
python -m zerino.cli.captions list
python -m zerino.cli.add_account list
```

### Start Streaming

Terminal 1:
```bash
python -m zerino.capture.main      # Watch for hotkey markers
```

Terminal 2:
```bash
python -m zerino.publishing.batch.scheduler_runner    # Process clips
```

Start recording in OBS → Press F8/F9 during moments → Clips post automatically once recording ends.

---

## Architecture

### Core Components

**Capture Daemon** (`zerino/capture/`)
- Watches `./recordings/` for new video files
- Listens for F8/F9 hotkeys (macOS, requires Accessibility permissions)
- Stores marker timestamps in the SQLite database

**Clip Service** (`zerino/clip_service.py`)
- Converts markers into clip windows: `start = marker_time - PRE_BUFFER`, `end = start + CLIP_DURATION`
- Default window: 60 seconds (10s pre, 50s post)

**Renderer** (`zerino/ffmpeg/export_generator.py`)
- Two-stage ffmpeg seek: input keyframe jump + output frame-accurate decode
- Supports three layouts:
  - **Vertical** (TikTok, Shorts): upright orientation, watermark, captions
  - **Square** (Instagram): 1:1 face-fill from facecam (dual-source) or fallback crop
  - **Split** (YouTube Shorts): 9:16 split with face on top, game on bottom

**Publisher** (`zerino/publishing/`)
- Handles transcription via faster-whisper (model: small, ~500MB)
- Applies KaraOKe caption styling (bouncing highlight effect)
- Queues jobs to Zernio for multi-platform distribution (TikTok, YouTube, Instagram, etc.)

**Detection Layer** (`zerino/detection/`) — *Opt-in via feature flags*
- Game-agnostic core: fuse audio + OCR signals → event scoring → clip windowing → deduplication
- **Fortnite adapter** (production-ready):
  - OCR + audio gating for kill-feed banner detection
  - Identity filtering (squadmate kills excluded)
  - Two-stage audio gating to minimize OCR calls (Tesseract-CPU by default)
- Emits detected events to the clip pipeline (same path as F8/F9)

### Data Flow

```
OBS Recording
    ↓
[Capture Daemon] ← F8/F9 hotkeys (macOS)
    ↓
Database: markers table (timestamp, kind, recording_id)
    ↓
[Clip Service] → Clip windows (start, end, layout, platforms)
    ↓
[Router] → De-duplicate by layout + Transcribe once
    ↓
[Processors] → Vertical | Square | Split (format-specific rendering)
    ↓
[ffmpeg Export] → H.264 + AAC, preset-tuned quality
    ↓
[Zernio Publisher] → TikTok | YouTube Shorts | Instagram | etc.
```

**Detection (off by default):**
```
Completed Recording
    ↓
[Detection CLI] or [ClipWorker hook] with ZERINO_DETECTION_AUTORUN=1
    ↓
[Fortnite OCR + Audio] → Event stream (KILL, MULTI_ELIM, KNOCK, etc.)
    ↓
[Scoring + Windowing + Dedup] → Detected clip windows
    ↓
[Emit] → Same clip pipeline as F8/F9
    ↓
[Auto-Post] if ZERINO_DETECTION_AUTOPOST=1
```

---

## Configuration & Management

### Captions (Subtitles)
Store caption data for your streams:
```bash
python -m zerino.cli.captions add <profile_name> <caption_file>
python -m zerino.cli.captions list
```

### Accounts (Publishing Targets)
Link accounts for Zernio distribution (TikTok, YouTube, Instagram, etc.):
```bash
python -m zerino.cli.add_account add <account_name> <platform> <zernio_account_id>
python -m zerino.cli.add_account list
```

### Detection Configuration (Fortnite)
Fortnite detection profiles live in `zerino/detection/profiles/` as YAML files:
```yaml
name: fortnite
game_id: fortnite
ocr:
  model: tesseract-cpu
  dt: 0.333  # probe interval (seconds) — 3fps ≈ good OCR recall
audio:
  gates:
    - banner_eliminated  # OBS audio signal
    - multi_kill_signal
score_threshold: 0.75
clip_budget: 20  # max 20 clips per session
```

### Environment Variables
```bash
# Core
ZERNIO_API_KEY=<your-key>

# Detection (both default OFF for safety)
ZERINO_DETECTION_AUTORUN=1   # Run detection when a recording finishes
ZERINO_DETECTION_AUTOPOST=1  # Post detected clips automatically

# Optional
DEBUG=1                       # Verbose logging
```

---

## Key Docs

| Doc | Purpose |
|-----|---------|
| **RUNBOOK.md** | How to operate the system (one-time setup, pre-stream checks, live workflow) |
| **ARCHITECTURE_FINDINGS.md** | Deep dive: repo structure, clip schema, render path, timebase details |
| **DETECTION_DECISIONS.md** | *Authoritative* for highlight detection design choices, Fortnite adapter spec |
| **CLIPPING_QUALITY_PLAN.md** | Current backlog of quality fixes (compression, audio, foreign-language captions) |
| **highlight_detection_semantics.md** | Product rules: which game events trigger clips, scoring logic |
| **HIGHLIGHT_DETECTION_BUILD_PLAN.md** | Phase 1 & Phase 2 milestones, checkpoints |

---

## Execution Environment (Locked)

**macOS:** Live capture daemon (hotkey listening, no GPU needed)
**Windows:** Detection + rendering batch stage (GTX 1050 Ti, sm_61, 4GB shared with Whisper)

**GPU constraints:**
- Default OCR = Tesseract-CPU (high-contrast HUD text, frees GPU for Whisper)
- EasyOCR fallback only if Tesseract recall insufficient
- **Never run OCR + Whisper concurrently on GPU** — sequence them or put one on CPU

---

## Non-Regression Guardrail

⚠️ **This is a live revenue pipeline.** All new work must be **additive**. Do not modify:
- F8→square / F9→split render paths
- Hotkey/marker flow or capture daemon
- OBS recording integration
- Posting to Zernio

Quality-critical files that must not regress without byte-identical proof:
- `zerino/ffmpeg/export_generator.py`
- `zerino/processors/split.py`, `square.py`
- `zerino/composition/_captions.py`
- `zerino/composition/composition_rules.py`

---

## Testing & Validation

### Unit Tests
```bash
pytest tests/ -v
```

### Detection Validation (Fortnite)
Manually render detected clips for review:
```bash
python -m zerino.cli.detect <recording_id> --render-review ./renders/
```

Outputs detected clip windows to `./renders/detection_review/` for operator spot-check.

### End-to-End
1. Create a test clip manually: `python -m zerino.cli.clip_file --file <video> --start 10 --end 70`
2. Verify it renders: check `output/` for video file
3. Verify it posts: check the Zernio dashboard

---

## Common Tasks

### Process an Old Recording
```bash
python -m zerino.cli.reprocess <recording_id>
```

### Detect Highlights in a Recording
```bash
python -m zerino.cli.detect <recording_id>
```

### Check What's in the Queue
```bash
# Check the database for pending/processing clips
python -c "from zerino.db import get_db; [print(r) for r in get_db().execute('SELECT id, status FROM clips WHERE status IN (\"pending\", \"processing\")').fetchall()]"
```

### Manual Clip from File
```bash
python -m zerino.cli.clip_file --file path/to/video.mp4 --start 10 --end 70
```

---

## Troubleshooting

**"Hotkeys don't fire (F8/F9 pressed but no marker)"**
- macOS: Check Accessibility permissions for terminal + Python binary
- Verify `zerino.capture.main` is running and logs show `hotkey listener initialized`

**"First clip takes 5 seconds to process"**
- faster-whisper model loads into memory on first use (~500 MB)
- Subsequent clips are much faster (model cached)
- To pre-warm, press F8 early in your stream

**"Detection runs but doesn't emit events"**
- Check OBS is recording to `./recordings/` (exact path)
- Run `python -m zerino.healthcheck` to verify ffmpeg + API key
- Check `DETECTION_DECISIONS.md` for Fortnite OCR recall tuning

**"Posted to Zernio but not showing on social media"**
- Verify account is linked in `python -m zerino.cli.add_account list`
- Check Zernio dashboard for posting errors
- Captions may have tripped the platform's text filter (see `CLIPPING_QUALITY_PLAN.md`)

---

## Development

### Setup
```bash
pip install -r requirements-dev.txt   # Includes pytest, black, etc.
```

### Run Tests
```bash
pytest tests/ -v
pytest tests/test_detection.py -xvs   # Detection tests only
```

### Code Style
```bash
black zerino/ tests/
```

---

## License & Attribution

Built and maintained for automated streaming content creation.

---

## Contact & Support

For issues, check the docs above first. If you find a bug:
1. Reproduce it with `--debug` flag
2. Check relevant doc for known issues (see "Which doc is authoritative" section)
3. File an issue with logs + the exact command you ran
