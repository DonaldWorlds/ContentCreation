# ARCHITECTURE_FINDINGS.md — Phase 0 Discovery & System Health Audit

> Companion to `HIGHLIGHT_DETECTION_BUILD_PLAN.md`. Produced at the Phase 0 🛑 consult gate.
> **No feature code written. No refactors performed.** This maps the repo as it actually is.
> Date: 2026-06-03. Repo: `Content_Business`, primary package `zerino`.

---

## Execution environment — LOCKED (operator decision, 2026-06-03)

Cross-platform project (Mac + Windows). **Detection runs as a Windows-side batch stage**, separate from the live macOS capture daemon. Constraints that shape every detector design choice below:

- **Windows GPU = GTX 1050 Ti** (Pascal, compute capability **sm_61**, 4 GB VRAM, **no tensor cores**) — entry-level, VRAM-constrained, and **already shared with faster-whisper transcription**. **Mac has no NVIDIA.**
- **Runtime device detection** (CUDA if present, else CPU). **No hardcoded CUDA.** All OCR/GPU/torch deps **lazy-imported and optional** so the Mac side and the live capture daemon run without them installed.
- **Phase 0.5 env check (gating):** verify the installed torch+CUDA build actually supports **sm_61** before relying on the GPU at all.
- **Keep OCR light.** Do **not** run OCR and Whisper on the GPU concurrently — sequence them or put one on CPU. **Default: Tesseract-CPU** for HUD text (high-contrast → frees the GPU for Whisper); **EasyOCR (small models) is the fallback** only if recall falls short. Lean hard on **two-stage audio gating** to minimize OCR calls. Choose empirically against the eval harness; **assume no GPU headroom.**

---

## 0. TL;DR for the operator

- The repo is **not** the "Cinematic Media Engine" the plan assumes. It is **`zerino`**: a macOS capture daemon → marker (F8/F9 hotkey) → fixed-window clip → ffmpeg render → Zernio post pipeline. There is **no parallel task scheduler**, **no "standard vs cinematic longform pipeline" split**, and **zero existing frame/audio analysis** beyond Whisper captions. The plan's §0.1 vocabulary doesn't map 1:1 — I've translated each item to what's actually here.
- **The render path is solid and reusable.** Clip windows are `(source_path, start, end)` in **source-relative float seconds**; ffmpeg does an accurate two-stage seek. A detected timestamp in source-relative seconds will map to the cut **as exactly as the renderer allows** — so the detection layer can reuse the existing marker→clip→render→post chain wholesale.
- **"Clips miss the play" — evidence corrected my first guess (see §7).** I verified a *real, systematic* ~5s drift between OBS frame-0 and the daemon's `start_time` (n=22 recordings), but its **direction makes the pre-roll bigger, not smaller** — so the drift does **not** explain "captures aftermath." The leading cause is **human reaction latency exceeding the fixed 10s pre-buffer** + the non-event-anchored window (`clip_service.py:142-143`). The feature's real win is that **event-anchored detection removes reaction latency entirely** (and media-derived timestamps also kill the ~5s drift). OBS VFR-ness is still unverified (source files aren't on this Mac).
- **The detection infrastructure does not exist at all** — no OCR, no audio signal analysis, no per-game config, no content-hash cache, no eval harness, no calibration/overlay tooling. All Phase 0 §0.1 "gap" items are MISSING or only partially present. They must be built, and several need DB-schema and config-home work *before* the core can even emit.
- **Core can stay game-neutral.** Nothing in the repo is coupled to any game (the only "gameplay" concept is `F9 → split layout`, which is a *render* choice, orthogonal to detection). Fortnite (kill-feed) and 2K (no kill-feed) both reduce to `Event` streams the core never has to distinguish.

---

## 1. Marker model (schema, units, source-relative vs absolute)

### On disk — SQLite (`zerino/db/init_db.py`)
**`markers`** table (`init_db.py:40-53`):
| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `streamer_id` | INTEGER FK→streamers (ON DELETE SET NULL) | |
| `recording_id` | INTEGER NOT NULL FK→recordings (ON DELETE CASCADE) | |
| `timestamp` | **REAL NOT NULL** | **source-relative seconds** since recording start; float for sub-second precision (comment lines 36-39) |
| `kind` | TEXT NOT NULL DEFAULT 'talking_head' **CHECK(kind IN ('talking_head','gameplay'))** | hotkey-derived; drives render *layout* only |
| `note` | TEXT | freeform, currently unused by the pipeline |
| `created_at` | TIMESTAMP | |

**`clips`** table (`init_db.py:60-77`): `id, recording_id, marker_id (NOT NULL FK→markers CASCADE), video_file, clip_start REAL, clip_end REAL, status, output_path, error_message, timestamps`. Status ∈ pending/processing/completed/failed.

### In memory
- A marker is a **plain dict** built by `MarkerRepository.get_markers_for_recording` (`marker_repository.py:67-77`): `{id, recording_id, streamer_id, timestamp, kind, note}`.
- The clip window is the **`ClipJob` dataclass** (`models.py:17-81`) — the real cross-layer contract: `clip_id, source_path, start: float, end: float, platforms, transcript_path, caption, mode, scheduled_for, layout, face_source_path, metadata: dict`. `start`/`end` are **source-relative float seconds**. `metadata` is a documented **write-once cache** (Router writes before fan-out; processors read-only).

### Gap vs the plan's `Event` schema
The plan's `Event(t, type, source, confidence, weight, meta)` has **no home** in `markers`. Concretely blocking:
- **`kind` has a CHECK constraint** locking it to `talking_head`/`gameplay`. Inserting `kind='KILL'` or `'auto_highlight'` **fails the constraint** → detection cannot emit markers without a migration.
- No `confidence`, `weight`, `type`, `source`, or `meta` columns anywhere.
- `clips.marker_id` is **NOT NULL** with a FK — every clip *must* reference a marker row. Detected clips need either a marker row each, or a schema change.

---

## 2. Where clip timestamps come from today

Two origins, both ending at the same `ClipJob`:

1. **F8/F9 hotkey (live capture, primary):**
   `MarkerIngestWorker._on_hotkey` (`marker_worker.py:113-119`) → `MarkerService.create_marker` (`marker_service.py:18-55`). The timestamp is:
   ```python
   timestamp = time.time() - start_time      # marker_service.py:45
   ```
   where `start_time = time.time()` is captured in `RecordingService.start_recording` (`recording_service.py:127`). **This is wall-clock, not media-derived** (see §7 timebase).
   Then `ClipService.process_single_marker` (`clip_service.py:127-154`) turns each marker into a **fixed window**: `start = max(0, marker_time - PRE_BUFFER)`, `end = start + CLIP_DURATION`, with `CLIP_DURATION=60`, `PRE_BUFFER=10` (`clip_service.py:38-39`). So every clip is exactly 60s, 10s pre / 50s post — **symmetric-ish, not event-anchored**.

2. **Ad-hoc file (`cli/clip_file.py`):** builds a `ClipJob` directly from `--file --start --end` (no DB rows). This is the **cleanest test/eval entry point** for detection.

3. **Reprocess (`cli/reprocess.py`):** re-runs `ClipService.process_recording(recording_id)` for recovery; same code path as the daemon.

---

## 3. Render path (what consumes markers → produces a clip)

Chain: `ClipService.create_clips` (`clip_service.py:164-259`) builds `ClipJob`s → `queue_clip_jobs_for_posting` (`clip_to_posts.py:161-279`) → `process_and_queue_clip_job` (`publishing/pipeline.py`) → **`Router.route_clip_job`** (`router.py:108-203`) → per-layout processor → **`ExportGenerator`** (`ffmpeg/export_generator.py`).

- **Layout fan-out is de-duped by *layout*, not platform** (`router.py:137-143`): same layout = same render bytes, shared across platforms. Fan-out is currently **serial** (the `for platform, layout in unique_targets:` loop, `router.py:182-202`) even though `ClipJob.metadata`'s docstring describes an intended "parallel section."
- **Shared transcription** happens once per job before fan-out (`router.py:156-178`), cached in `job.metadata['karaoke_segments']`.
- Processors: `processors/vertical.py`, `square.py`, `split.py` each call `ExportGenerator.run_*_export_from_source(...)`.
- **What the renderer expects from the job:** only `source_path, start, end, layout, face_source_path` (+ caption/subtitle sidecar). It applies **no temporal padding of its own** — the window is whatever `start/end` say. Composition (`composition_rules.py`) decides crop-vs-pad and canvas, purely spatial.

### ffmpeg invocation (the timebase-critical part)
`run_export_from_source` (`export_generator.py:517-630`), command at lines **596-624**:
```
ffmpeg -y -nostdin \
  -ss {start - pre_roll:.3f} -i {source} \   # input seek → nearest keyframe (fast)
  -ss {pre_roll:.3f} -t {duration:.3f} \     # output seek → frame-accurate decode + trim
  -vf <crop,scale,fps?,watermark,subtitles,setparams> \
  -af <fade,highpass,dynaudnorm,alimiter,fade> \
  -c:v libx264|h264_nvenc ... -maxrate ... -bufsize ... -pix_fmt yuv420p \
  -c:a aac_at|aac ... -ar 48000 -movflags +faststart {output}
```
- `pre_roll = min(2.0, start)` (`export_generator.py:550`) warms rate control.
- **Seek is accurate** (input keyframe jump + output decode-to-frame). No `-copyts`, `-avoid_negative_ts`, or `-r`.
- **FPS / CFR:** `_target_fps = min(60, src_fps)` (`export_generator.py:342-348`); `_fps_filter` appends `,fps=NN` **only when src≠target** (`export_generator.py:351-358`). So **output is forced to CFR via the `fps=` filter**, but there is **no explicit VFR *detection*** — `src_fps` comes from `avg_frame_rate` (`ffmpeg_utils.py:158-165`).
- **Start/end are passed as seconds straight to `-ss`/`-t`** — never converted to frame numbers. This is good for us (PTS-seconds), with one caveat in §7.
- Variants: `run_split_export_from_source` (632-785, single source split), `run_dual_split_export_from_source` (787-929, separate game+face inputs — **both seeked to `start - pre_roll` assuming a shared timeline**), `run_dual_square_export_from_source` (931-1034).

### Decodes per clip (efficiency)
Per clip, the source is opened **2–3×**: `probe_metadata` ffprobe + (audio slice extract for Whisper, unless `metadata['karaoke_segments']` cached) + the render encode. **For N layouts → N re-encodes**, each re-decoding the same source slice. A detection pass that needs to decode the whole file is **new, heavier I/O** — it must be cached (§6 gap C) and is the main efficiency concern on long VODs.

---

## 4. Concurrency / job model (plan's "parallel task scheduler")

**There is no general task scheduler.** Concurrency is ad-hoc threads + one stdlib `queue.Queue`:
- Daemon (`capture/main.py:60-138`) starts 3 long-lived threads: watchdog `Observer`, `marker-hotkey` listener, `clip-worker` consumer. One global `threading.Lock` guards a shared `state` dict.
- `RecordingService.handle_new_recording` spawns a short-lived thread per recording for readiness + size-stability monitoring (`recording_service.py:58, 152`).
- `PipelineQueueService` (`capture/services/queue_service.py`) wraps `queue.Queue`; the single `clip-worker` consumes `recording_finished` events → `ClipService.process_recording` (`clip_worker.py` → `clip_service.py:261`).
- **Render fan-out is serial** (`router.py:182-202`). Whisper model is a process-global behind a lock (`_captions.py:71-73`).
- **No `ThreadPoolExecutor`/`ProcessPoolExecutor`/`asyncio`/`concurrent.futures` anywhere.**

**Implication (revised for the locked env):** detection is a **Windows-side batch stage**, **not** part of the live macOS capture daemon. It plugs into the batch/CLI path (`cli/reprocess.py` / `cli/clip_file.py`, or a new `cli/detect.py`) and runs serially there, bringing its own internal parallelism if desired. The live Mac daemon — and the Mac side generally — must keep running with **no torch/OCR deps imported** (lazy-optional). Don't build a generic scheduler for this.

---

## 5. Media I/O & existing analysis

- **ffmpeg/ffprobe**: `ffmpeg/ffmpeg_utils.py` (`probe_metadata`, `has_video_stream`, `get_video_duration_seconds`) + raw `subprocess` calls in `export_generator.py`, `processors/image.py`, `cli/quality_verify.py`. **Reusable**: `probe_metadata` (w/h/fps/duration/color/audio), the accurate-seek pattern, and `extract_audio_slice` (`_captions.py:323-368`, → mono 16 kHz WAV).
- **Frame grabs**: `processors/image.py:33-45` does `ffmpeg -ss T -i f -vframes 1` single-frame extraction (for carousels) — a reusable pattern for OCR frame sampling.
- **Audio**: only **faster-whisper** transcription (`_captions.py`). **No librosa / scipy.signal / numpy signal analysis. No onset/energy detection.**
- **Image analysis**: only **PIL resize/crop** (`processors/image.py`) and an ffmpeg `sobel,signalstats` quality metric in `cli/quality_verify.py`. **No OpenCV, no template matching, no scene/motion detection.**
- **OCR**: **none** (no easyocr/paddleocr/pytesseract).
- **`_old_clip_engine/`**: dead — only a stale `.pyc`, zero imports. Safe to ignore/delete.

---

## 6. SCOPE & GAPS (plan §4a / §4b answered against the repo)

### 4a. Is the design game-agnostic? — **YES, the repo imposes no game coupling.**
There is **no game concept anywhere** in `zerino`. The only "gameplay" token is `kind='gameplay'` → `KIND_TO_LAYOUT['gameplay']='split'` (`clip_service.py:43-46`) — purely a *render layout* choice, orthogonal to detection. So a game-neutral core (Event fusion → scoring → windowing → emit) sits cleanly in front of the existing marker system.
- **Fortnite** (elim-feed OCR + gunshot onset) and **NBA 2K** (scoreboard score-delta OCR + crowd-roar energy; **no kill feed**) both reduce to `list[Event]`. The core never distinguishes them — it sees `(t, type, weight, confidence)`. The structurally-opposite pair (kill-feed vs none) is handled entirely in the **adapters**.
- **Player-identity filtering** (Fortnite: keep only "you eliminated"; 2K: keep only your team's makes) belongs in the **adapter + GameProfile**, never the core — preserving neutrality. *Caveat:* gamertag/identity is **not stored anywhere today** (streamers table has name/platform only, `init_db.py:11-19`) → must be added to GameProfile config (§4b item 2).

### 4b. Gap inventory (exists / missing / where it should live)
| # | Item | Status in repo | Where it should live |
|---|---|---|---|
| A | **Canonical timebase + VFR→CFR** so markers map to the cut | **PARTIAL.** Render seeks by PTS-seconds (accurate) and forces CFR *output* via `fps=` (`export_generator.py:351-358`); `probe_metadata` reads `avg_frame_rate`. But **no VFR-aware frame/sample→seconds mapping for a detector**, and the **marker source itself is wall-clock-derived** (§7). | New `zerino/detection/timebase.py`: map frame **PTS** (not `index/avg_fps`) and audio **sample position** → source-relative seconds consistent with the render's `-ss`. Verify OBS VFR/CFR on a real recording first. |
| B | **GameProfile/adapter config** (fractional 0–1 HUD regions, event weights/rarity, thresholds, PRE/POST, clip budget, min/max duration, player identity) | **MISSING.** `config.py` is paths+logging only; composition presets are **integer-pixel, hardcoded** (`composition_rules.py:1-77`); detector params would be the first per-profile config. | New `zerino/detection/profiles/` (dataclass or YAML/JSON per game). Player identity added here (+ optionally persisted per streamer). |
| C | **Detection result cache** (source hash + detector version) | **MISSING.** Only a path+size+mtime *fingerprint* for export dedup (`batch_schedule_handler.py:93-100`) and the Whisper-model cache exist; **no hashlib on media**. | New `zerino/detection/cache.py`, keyed by `(content-hash or size+mtime, detector_version, profile_version)`. |
| D | **Two-stage sampling** (cheap audio pass gates expensive OCR) | **MISSING** (no audio analysis, no OCR). | New in `zerino/detection/`; reuse the `extract_audio_slice` WAV pattern (`_captions.py:323-368`) extended to whole-file, and the `image.py` frame-grab pattern for gated OCR. |
| E | **Ground-truth label format + precision/recall eval runner** | **MISSING.** Tests are 2 hand-run scripts (`tests/test_double_post*.py`), **no pytest, no fixtures-with-labels**. | Introduce pytest; label format = JSON sidecar per golden VOD; eval runner in `zerino/detection/eval.py` or a CLI. |
| F | **Region calibration tool** (preview a HUD region on a frame, test OCR) | **MISSING.** `cli/quality_verify.py` extracts sample frames (reusable) but no region preview/OCR test. | New `zerino/cli/calibrate.py`. |
| G | **Debug overlay** (events + score curve burned onto video) | **MISSING.** | New `zerino/cli/detect_overlay.py` (ffmpeg `drawbox`/`drawtext` or PIL). |
| H | **No-events / detection-failure handling** | **PARTIAL (by analogy).** The pipeline already no-ops gracefully on "no markers"/"no windows" (`clip_service.py:264-272`). Detection must mirror this: 0 events → 0 clips (or fall back to manual markers); OCR/audio failure → degrade, never crash the batch. | New behavior in the detection service, modeled on existing graceful no-ops. |

---

## 7. Timebase deep-dive — VERIFIED, with a correction to the earlier hypothesis

I ran the evidence check (2026-06-03, Mac, against `zerino_snapshot.db`; source `.mkv` files are not on this box so VFR is still unverified — see below). **A systematic timebase drift is confirmed, but its *direction* refutes my earlier "captures aftermath" framing.** Recording honestly per the evidence-first rule.

**Chain of custody of a manual marker timestamp:**
1. OBS starts the file at wall-clock `W0`; media t=0 ↔ `W0`. The OBS filename encodes `W0` (e.g. `2026-05-20 01-25-08.mkv`).
2. watchdog → `handle_new_recording` → `_process_file_async` → **`wait_for_file_ready`** (`recording_service.py:74-93`) loops on 0.5s polls, returning only when two consecutive polls show equal size.
3. Only **then** `start_recording` sets `start_time = time.time()` (`recording_service.py:127`) and writes `recordings.created_at` (same lock block) — so `created_at ≈ start_time`.
4. A press computes `timestamp = time.time() - start_time` (`marker_service.py:45`).

**Evidence (n=22 recordings):** comparing OBS filename (`W0`, local) to `recordings.created_at` (`start_time`, UTC), every row is `4.00h + 5–6s`. The 4.00h is the EDT→UTC offset (consistent across all 22); the residual is the real drift: **Δ = start_time − W0 ≈ +5s (min 5, max 6, mean 5.2), systematic.** So `start_time` lands ~5s *after* media t=0, and `marker.timestamp` **under-counts** the true media position of a press by ~5s. (Also visible in the data: markers flipped from integer seconds to floats at recording #17 / 2026-05-20 — the S1.1 truncation fix.)

**The correction:** under-counting by Δ means the cut `start = timestamp − 10` lands at `M_press − 15` in the media — i.e. the drift makes the effective **pre-roll *larger* (~15s instead of 10s)**, pushing the play *later* into the clip, **not** cutting it off. That is the **opposite** of "captures aftermath." **So the ~5s start_time drift is real but does NOT explain "clips miss the play."**

**More likely actual cause (now the leading hypothesis):** **human reaction latency exceeding the fixed pre-buffer.** Users press F8 *after* the play resolves; with action at `M_press − R` and clip start at `M_press − 15`, the play sits at clip-position `15 − R`. When reaction `R ≳ 15s` (easy to exceed mid-game), the play falls *before* the clip starts → all aftermath. The fixed, non-event-anchored 10/50 window (`clip_service.py:142-143`) is the structural problem; the ~5s drift is a smaller, separate timebase imprecision.

**Why the feature still wins (the real argument):** event-anchored detection removes **reaction latency `R` from the equation entirely** — the window is anchored on the *detected event*, not on a late human press (plan §4.3 step 4). Detected timestamps are also media-derived (frame PTS / audio sample position), so they additionally eliminate the ~5s drift. Net: the feature fixes the dominant cause (`R`) *and* the secondary one (Δ). The detector's one hard rule: compute event time from **real PTS / sample index**, never `frame_index / avg_fps`.

**Still open (needs the Windows box / a fresh OBS recording):**
- **VFR/CFR of OBS `.mkv`** — not verifiable here (no source files; only rendered CFR outputs remain). Decides how strict the detector's PTS handling must be.
- **Direct Δ confirmation from a source file** — `ffprobe` the `.mkv`'s embedded `creation_time` / first-frame PTS vs `created_at` (the filename-vs-`created_at` proof above is already strong, but the embedded metadata is the independent cross-check).
- **Eyeball test** — seek a real source to a known `marker.timestamp` and see where the play lands relative to the press (would directly show the `R`-dominates story).

---

## 8. System health audit (§0.3) — pitfalls & inefficiencies

- **Timebase/drift:** wall-clock marker zero (§7, the big one). `min(60, src_fps)` resamples 120fps sources to 60 for *output* (fine), but a detector must analyze at *source* fps/PTS, not output.
- **Error handling & failure modes:** `MarkerService.create_marker` prints warnings and **silently returns** on missing state (`marker_service.py:24-36`) → markers can be dropped with no exception. `ClipService.create_clips` catches broad `Exception` and fails the **whole batch** (`clip_service.py:246-254`). `run_export_from_source` raises bare `Exception(result.stderr)` (`export_generator.py:627-629`). `subprocess.run(capture_output=True)` buffers all encoder stderr in memory.
- **Marker schema rigor:** `kind` CHECK constraint blocks new event types; no confidence/weight/type/source/meta; `clips.marker_id` NOT NULL forces a marker per clip; **`clip_exists(recording_id, start, end)` uses float-exact equality** (`clip_service.py:208`) → fragile idempotency, and detection re-runs with slightly different windows will create duplicates.
- **Idempotency/caching:** no content-hash anywhere; only path+size+mtime fingerprint (batch) + Whisper model cache. Detection needs detector-version-aware idempotency.
- **Concurrency safety:** one coarse global lock; `ClipJob.metadata` write-once contract is documented but fragile for future writers (models.py:45-56); render fan-out is serial despite the documented "parallel section."
- **ffmpeg correctness:** accurate-seek pattern is good. **Risk:** dual-source split seeks both game and face to `start - pre_roll` assuming a shared timeline (`export_generator.py:905-906`) — desync if the two files have different start offsets. No `-r`/`-copyts` (acceptable given the filter-based CFR).
- **Test coverage gaps:** only double-post regression tests; **nothing for marker timing, window math, composition, or ffmpeg**; no pytest. The eval harness must bring test infra with it.
- **Config sprawl:** dozens of module-level constants across `export_generator.py` (encoder/bitrate/fades/leveler), `_captions.py` (Whisper), `composition_rules.py` (presets), `clip_service.py` (`CLIP_DURATION`/`PRE_BUFFER`). **No central config home** → GameProfile has nowhere to live yet.
- **Observability:** `print()` mixed with `logging` throughout (e.g., `recording_service.py` all prints; `clip_to_posts.py` prints + logs). No metrics/counters → detection observability (events found, OCR calls, cache hits) has no home.
- **Efficiency / long VODs:** per-clip 2–3 source opens + N re-encodes (one per layout, each re-decoding the slice). A naive full-frame OCR every frame on a long VOD would be ruinous → two-stage sampling (gap D) is mandatory. Must stream frames, not buffer the whole VOD in memory.
- **GPU contention (locked env):** the GTX 1050 Ti (4 GB, sm_61, no tensor cores) is **shared with faster-whisper**. OCR and Whisper must **not** run on the GPU concurrently — sequence them or pin one to CPU. Default OCR = **Tesseract-CPU**; reserve the GPU for Whisper. Two-stage audio gating is the primary lever to keep OCR call volume (and VRAM pressure) down. Assume no GPU headroom and measure against the eval harness.

---

## 9. Integration seams (exact files/functions where the layer plugs in)

| Seam | Location | Role for detection |
|---|---|---|
| **Pre-cut analysis stage** (primary) | `ClipService.process_recording` (`clip_service.py:261-274`), between `get_markers_for_recording` and `generate_clip_windows` | Run `HighlightDetectionService.detect(source_path, profile)` → produce **anchored (start,end) windows** → feed `create_clips`. Runs serially in the clip-worker thread. |
| **Window emission** | `ClipService.create_clips` (`clip_service.py:164-259`) | Detected windows enter here as `{marker_id, start, end, kind, layout}` dicts. **Must bypass the fixed-60s `process_single_marker`** so the core's asymmetric anchoring (PRE>POST, climax ~65–70%) survives. Needs a marker row per window (or schema change — §10 MUST-FIX). |
| **Marker emit** | `MarkerRepository.insert_marker` (`marker_repository.py:8-27`) | Where `Event`-derived markers persist. Blocked by the `kind` CHECK constraint until migrated. |
| **Ad-hoc / eval entry** | `cli/clip_file.py` | Add a `--detect` mode: run detection on an arbitrary VOD → emit N clips with no DB rows. Ideal for the golden-VOD precision/recall harness. |
| **Reprocess entry** | `cli/reprocess.py` | Re-run detection on an existing recording for recovery / re-tuning. |
| **Reusable media helpers** | `ffmpeg_utils.probe_metadata`, `_captions.extract_audio_slice`, `processors/image.py` frame grab | Reuse for one shared decode for audio onset + gated frame OCR. |
| **Schema/migrations** | `db/init_db.py`, `db/migrate.py` | Home for the detections-table / extended-markers migration. |

---

## 10. Proposed revision to Phases 1–4

The plan's bones are right; reality requires inserting prerequisites the plan assumed away.

**New Phase 0.5 — Foundations (MUST precede Phase 1; these are the MUST-FIX items):**
1. **Timebase utility + VFR verification.** Build `detection/timebase.py` (PTS/sample→seconds), confirm OBS VFR/CFR on a real recording, and run the §7 verification recipe so we know whether marker-drift is the real bug.
2. **Schema migration.** Add a `detections` table (or extend `markers` with `confidence/weight/type/source/meta` + relax the `kind` CHECK), and make `clips.marker_id` nullable or auto-create marker rows for detected events. Without this the core **cannot emit**.
3. **Window-emission path** that bypasses the fixed-60s window so anchored asymmetric windows reach `create_clips`.
4. **Detector-version-aware idempotency** to replace float-exact `clip_exists`.
5. **Execution environment — DECIDED (see top-of-doc LOCKED note).** Detection = Windows-side batch stage; runtime device detection (CUDA-if-present else CPU); lazy-optional torch/OCR deps so Mac + live daemon run without them; **Phase 0.5 env check verifying torch+CUDA supports sm_61** before trusting the GPU; Tesseract-CPU default with EasyOCR-small fallback; OCR and Whisper never share the GPU concurrently.

**Phase 1 — Game-agnostic core (as planned), plus:** the core lives in a new `zerino/detection/` package and **emits into the existing marker/clip path** via the §9 seams. Test on synthetic `Event` fixtures (plan §4.4) — but also add an emission test that validates output against the *real* (post-migration) schema.

**Phase 2 — Fortnite & Phase 3 — 2K (as planned), plus the gap infra as first-class deliverables (the repo has none of it):** GameProfile config (gap B), detection cache (gap C), two-stage sampling (gap D), golden-VOD label format + pytest eval runner (gap E), calibration tool (gap F), debug overlay (gap G), graceful no-events/failure (gap H). The plan lists these inline; here they are explicit build items because nothing exists to extend.

**Phase 4 — Semantic re-ranker:** unchanged, deferred.

---

## 11. Prioritized improvement backlog

### MUST-FIX (block building the feature safely)
1. **Media-derived canonical timebase** (PTS/sample, not `index/avg_fps`); verify OBS VFR. *Without it, detected times drift on VFR and we can't trust the cut.*
2. **Schema can't host detected events** — `kind` CHECK blocks new types, no confidence/weight/meta, `clips.marker_id` NOT NULL. *Migration required before emit works.*
3. **Window emission must bypass the fixed 60s/10-pre window** so asymmetric anchoring survives.
4. **Replace float-exact `clip_exists` with detector-version-aware idempotency** to prevent duplicate clips on re-runs.
5. **Execution environment — DECIDED (see top-of-doc LOCKED note).** Windows-side batch detection; runtime CUDA-else-CPU; lazy-optional torch/OCR deps (Mac + live daemon stay clean); Phase 0.5 env check verifies torch+CUDA supports sm_61; Tesseract-CPU default, EasyOCR-small fallback; OCR and Whisper never share the GPU concurrently.

### HEALTH (logged, not executed now)
- Replace `print()` with structured logging + add detection metrics/counters (observability home).
- Introduce pytest + a real test suite (needed anyway for the eval harness; nothing covers marker/window/ffmpeg today).
- Establish a central/per-profile **config home** (kills config sprawl; gives GameProfile a place).
- Parallelize the render fan-out (the documented-but-unused "parallel section"); optional.
- One shared source decode reused across detection + layouts (per-clip 2–3 opens + N re-encodes today).
- DB hygiene: connection-per-call, `commit()` on read-only SELECTs (`marker_repository.py:41,79`).
- Harden dual-source split against game/face timeline desync (`export_generator.py:905-906`).
- Narrow broad `except Exception` swallowing (silent marker drops in `marker_service.py`).
- Delete dead `_old_clip_engine/`.

---

## 12. Open questions / risks for the operator

1. **Evidence-first:** confirm the §7 marker-drift hypothesis (and OBS VFR-ness) before we treat it as the cause.
2. ~~**Where will detection run?**~~ **RESOLVED 2026-06-03** → Windows-side batch stage; GTX 1050 Ti (sm_61, 4 GB, shared with Whisper); runtime device detection; lazy-optional GPU deps; Tesseract-CPU default. See top-of-doc LOCKED note.
3. **Source of game VODs** — the same `recordings/` OBS files, or downloaded VODs via `clip_file.py`? Resolution/bitrate/HUD will differ (OCR calibration depends on this).
4. **Layout for auto-detected clips** — a full-screen gameplay VOD usually has **no face-cam pair**. Should detected highlights render `vertical` (cropped gameplay), `split` (only if a face pair exists), or a new layout? `KIND_TO_LAYOUT` currently maps `gameplay→split`, which assumes a face pair.
5. **Player identity** has no storage today — where do we capture each streamer's Fortnite name / 2K team to filter events?
6. **Clip budget vs posting cadence** — detection may produce many candidates; the poster spaces clips 2h apart (`clip_to_posts.py:37`). Budget/ranking must reconcile with that.
