# Project Review — The Whole System & Where It's Going

> A single read-back of the project **as a whole**: what we have built and working today, what we're adding (catch the actual kills/plays), how the new piece **connects to** the existing one, the discipline that keeps us from breaking what works, and the roadmap.
> Companions: `DETECTION_DECISIONS.md` (**authoritative** locked decisions — wins on conflict), `ARCHITECTURE_FINDINGS.md` (deep audit), `HIGHLIGHT_DETECTION_BUILD_PLAN.md` (phase plan), `highlight_detection_semantics.md` (product rules). Date: 2026-06-23.

---

## PART A — What we have NOW (built, working, in active use)

`zerino` is a full **capture → marker → clip → render → post** pipeline. None of this is theoretical — it's the live system, and it is what we must protect.

**1. Capture (OBS, cross-platform).**
- The operator records **gameplay + a Sony face-cam together** (OBS main recording + OBS Source Record plugin writing the clean webcam to `recordings/face/`).
- Two hotkeys mark moments live: **F8 = `talking_head`**, **F9 = `gameplay`** (`capture/workers/marker_worker.py`).
- The capture daemon watches `recordings/`, detects a finished recording, and runs the clip flow (`capture/services/recording_service.py` → `clip_service.py`).

**2. Markers → clip windows.**
- A marker is `(recording_id, timestamp, kind)` in source-relative seconds (`db/init_db.py` markers table).
- `ClipService` turns each marker into a clip window and a `ClipJob(source_path, start, end, layout, face_source_path, …)` — the contract that flows to render+post (`models.py`).
- **Face pairing already works**: `ClipService._find_face_pair` matches the game recording to its `recordings/face/` partner by name (with validation + mtime fallback).

**3. Render (the part we are NOT changing — only reusing).**
- **F8 → square**, **F9 → split** (game + face stacked), plus **vertical** (`KIND_TO_LAYOUT`, `processors/`, `ffmpeg/export_generator.py`).
- **Dual-source split & square** are built and tuned: sharp face, centered game, zero bleed, accurate two-stage ffmpeg seek, color/audio leveling, captions, watermark. These are the quality-critical paths.

**4. Post (Zernio).**
- One render per layout fans out to one post per (platform, account); first clip posts immediately, the rest are spaced ~2h apart (`publishing/`).

**→ Bottom line:** capture (incl. the Sony face-cam), the split/square/vertical renders, and posting **all work today**. The clip *windows* are the only weak link — they come from a late human hotkey press, so they miss the play.

---

## PART B — Where we're going (the enhancement)

**The one thing we're adding: smarter clip windows that land on the actual kill / made shot.**

Today's window = `human press time ± fixed padding`. Verified root cause (`ARCHITECTURE_FINDINGS.md` §7): the operator presses **after** the play, so with a slow reaction the action falls outside the clip. The fix is an **event-driven detection layer** that reads the *game itself* (HUD + audio) and anchors the window on the detected event.

**How it CONNECTS to what we already built (this is the whole point):**

```
   [NEW]  Detection layer  ──►  emits the SAME ClipJob that F9 already produces
          (reads HUD + audio,        (layout="split", face_source_path set,
           anchors on the kill)       start/end = anchored window)
                                              │
                                              ▼
   [EXISTING, UNCHANGED]  create_clips ─► Router ─► split/square render ─► Zernio post
```

The detector's output is **indistinguishable** from a F9 press to everything downstream. It supplies the existing split renderer the exact `(source_path, face_source_path, start, end)` it already expects — so the face-cam split we already built is reused as-is, just with a window that's *on the play* instead of after it.

**Product rules already locked** (`highlight_detection_semantics.md`): my-events-only (own kills / own team's makes), cluster rapid action into one top-ranked clip, a tunable per-profile score dial (keep the good, drop the boring), a higher bar for 2K than Fortnite, and **detected clips default to `split` with a `vertical` fallback** if a VOD ever has no face-cam.

---

## PART C — Discipline: how we do NOT break what works

This is additive surgery on a working system, so the rules are explicit:

1. **Reuse, don't modify.** Detection sits *in front of* the existing pipeline. The F8/F9 hotkey flow, `process_single_marker` for manual markers, the processors, and `export_generator` render logic are **not touched**.
2. **Same ClipJob, same renderer.** Detected clips flow through the *existing* `create_clips → Router → split/square render` path. If the renderer can't tell a detected clip from a F9 clip, the split/square quality we built can't regress.
3. **Schema changes are additive only.** We *add* a home for detected-event metadata (a new `detections` table) and reuse the existing `kind='gameplay' → split` mapping — **no change to the markers or clips tables**, so existing rows read/write exactly as before.
4. **Hard import boundary.** All OCR/GPU/torch code is lazy-imported inside the detection package; the Mac side and the live capture daemon import nothing GPU-related and keep running untouched (`HIGHLIGHT_DETECTION_BUILD_PLAN.md` §2).
5. **Prove it every phase.** Every CP2/CP2.5 includes a **non-regression test**: the same F8/F9 input produces **byte-identical** render output before vs after. Any change that would alter existing output is a stop-and-rescope.

---

## PART D — Roadmap (where each piece lands)

| Phase | What | State |
|---|---|---|
| **0 — Audit** | Map the real system, verify root cause, lock decisions | ✅ DONE |
| **0.5 — Foundations** | Timebase util (+verify OBS VFR), additive `detections` table, window-emission seam, GameProfile config (identity + score dial + per-game bar + default layout), face-cam plumbing, Windows env check (sm_61) | ◀ **next (CP1 now)** |
| **1 — Core** | `zerino/detection/` spine: Event → fuse → score → anchor window → dedupe → emit. Synthetic fixtures only, no real video | after CP1 |
| **2 — Fortnite** | Elim-feed OCR (Tesseract-CPU) + gunshot audio gate, my-elims filter, golden-VOD precision/recall | |
| **3 — NBA 2K** | Score-delta OCR + crowd audio, higher bar (dunk/3PT/buzzer only), my-team filter — proves the core is game-agnostic | |
| **4 — Re-ranker** | Optional OpenCLIP/TwelveLabs re-rank of survivors | deferred |

We are **at CP1** (agree the approach + interfaces before any code). No `zerino/detection/` package exists yet; no feature code has been written.

---

## PART E — CP1 strategy (Phase 0.5 + Phase 1) — for sign-off

### E.1 Package layout (new, isolated)
```
zerino/detection/
  __init__.py        # NO heavy imports at module top (Mac-safe)
  timebase.py        # PTS / sample-index → source-relative seconds (Phase 0.5)
  events.py          # Event dataclass
  profile.py         # GameProfile dataclass + loader (Phase 0.5)
  core/
    fuse.py  score.py  window.py  dedupe.py   # game-agnostic spine (Phase 1)
  emit.py            # synthesize gameplay marker + detections row → create_clips (Phase 0.5)
  cache.py           # detector-version idempotency (Phase 0.5)
  adapters/
    base.py          # DetectorAdapter ABC (Phase 1 interface; impls in 2/3)
    fortnite.py      # Phase 2     nba2k.py  # Phase 3
  eval.py            # golden-VOD precision/recall (Phase 2)
cli/detect.py        # Windows batch entry: recording → detect → emit
```
GPU/OCR libs (`torch`, `cv2`, `pytesseract`, `easyocr`) are imported **inside functions**, never at module top.

### E.2 Interfaces
```python
@dataclass
class Event:
    t: float; type: str; source: str; confidence: float; weight: float; meta: dict

class DetectorAdapter(ABC):
    game_id: str
    @abstractmethod
    def detect(self, media, profile: "GameProfile") -> list[Event]: ...   # ALREADY identity-filtered (Decision 1)

@dataclass
class GameProfile:
    game_id: str
    player_identity: dict          # Fortnite gamertag / 2K team side (Decision 1)
    hud_regions: dict              # fractional 0-1 coords
    event_weights: dict            # rarity table
    score_threshold: float         # the tunable DIAL (Decisions 3+4; 2K > Fortnite)
    signal_weights: dict           # cluster / audio / special-event contributions
    pre: float; post: float        # asymmetric padding (PRE > POST)
    clip_budget: int               # ranked shortlist size (vs posting cadence)
    min_dur: float; max_dur: float
    default_layout: str = "split"  # Decision 5 (vertical fallback if no face)
```
**Format (LOCKED):** profiles are **YAML files** loaded into this dataclass — editable on disk, type-safe in code (`DETECTION_DECISIONS.md` §3).

### E.3 Schema migration (additive, zero-regression — RECOMMENDED)
- **Add one new table `detections`** (event metadata: clip/marker link, type, confidence, weight, source, meta JSON, detector_version, profile_version, source_hash).
- **Do NOT alter `markers` or `clips`.** Emit a normal marker with **`kind='gameplay'`** (already allowed by the CHECK, already maps to split) so the existing `create_clips`/FK path is satisfied untouched. The Event metadata lives in `detections`.
- Detected windows are built **anchored/asymmetric** and passed to `create_clips` directly, **bypassing** the fixed-60 s `process_single_marker`.
- **Idempotency:** before emitting, skip if `(source_hash, detector_version, profile_version)` already has detections — replaces float-exact `clip_exists` for this path.

### E.4 Entry point (LOCKED — both)
- **`cli/detect.py`** (Windows batch): given a recording/source, run the adapter → core → `emit` → existing render+post.
- **`reprocess --detect`**: a flag on the existing `cli/reprocess.py` for recovery / re-tuning. Both call the same `DetectionService` (no duplicated logic).

### E.5 Test plan
- **Phase 1 (CP2):** synthetic Event fixtures → window/anchor (~65–70%)/PRE-POST/clamp, clustering rank, score-dial keep/drop, dedupe+budget, emission validates against the real (post-migration) schema.
- **Non-regression (every phase):** existing F8/F9 → byte-identical render output.
- **Phase 2/3 (CP2):** golden-VOD precision/recall (targets set at CP1).

### E.6 CP1 decisions — DECIDED (2026-06-23; authority: `DETECTION_DECISIONS.md`)
1. **Schema:** additive `detections` table + reuse `kind='gameplay'` marker — no change to markers/clips. ✅
2. **GameProfile format:** YAML files + typed dataclass loader. ✅
3. **Entry point:** both — `cli/detect.py` **and** `reprocess --detect`. ✅
4. **Phase 1 scope:** pure core on synthetic events, zero detectors/real video. ✅
5. **Fortnite P/R gate:** floor recall ≥ 0.8 / precision ≥ 0.7 now; target recall ≥ 0.9 / precision ≥ 0.8 after first golden VOD; recall prioritized, weighted to high-value events. ✅

All five locked. CP1's remaining work is interfaces/DDL, not new decisions (§6 of `DETECTION_DECISIONS.md`). **No feature code until the CP1 interface review is signed off.**
