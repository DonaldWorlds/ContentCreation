# Detection — Locked Foundational Decisions (single source of truth)

> The authoritative handoff for **CP1**. Every decision here is **LOCKED** with the operator. Where this doc and any other doc disagree, **this doc wins**.
> Companions (detail, not authority): `PROJECT_REVIEW.md` (whole-system review), `HIGHLIGHT_DETECTION_BUILD_PLAN.md` (phases), `ARCHITECTURE_FINDINGS.md` (audit), `highlight_detection_semantics.md` (product rationale).
> Dates: semantics locked 2026-06-22; foundational/CP1 decisions locked 2026-06-23.

---

## 0. Non-negotiables (the frame for everything below)

- **Additive only / non-regression.** Detection sits *in front of* the existing pipeline and reuses it unchanged. It must **not** modify or break the working **F8→square** / **F9→split** render paths, OBS capture (incl. the Sony face-cam), the hotkey marker flow, or posting. Every phase's CP2/CP2.5 proves existing F8/F9 → render output is **byte-identical** before vs after.
- **Execution environment (LOCKED):** detection is a **Windows-side batch stage**; GPU = GTX 1050 Ti (sm_61, 4 GB, shared with Whisper); runtime CUDA-else-CPU; all torch/OCR/GPU deps **lazy-imported + optional** (Mac + live daemon import nothing GPU-related); **default OCR = Tesseract-CPU**, EasyOCR-small fallback; never run OCR + Whisper on the GPU at once; lean on two-stage audio gating.

---

## 1. Semantics — what counts as a highlight (5 product decisions)

Full rationale in `highlight_detection_semantics.md`. Summary:

1. **My events only.** Detect only the operator's own positive events (Fortnite: own elims, ignore deaths; 2K: own team's makes, ignore opponent). Identity lives in GameProfile, filtered **in the adapter, never the core**.
2. **Clustering.** Rapid consecutive events → **one** window, **ranked higher** than an isolated event (triple-kill in 5 s = one top clip, not three). Implemented by the core's density scoring + clustering bonus + asymmetric anchored window (climax ~65–70%).
3. **Tunable scoring dial.** Every event scored from cluster bonus + audio intensity + special-event type; only **above threshold** becomes a clip. The threshold is a **per-profile DIAL**, tuned by the operator. Output is a **ranked, clip-budgeted shortlist** that respects the ~2 h posting cadence.
4. **Per-game bar.** Threshold is per-game; **2K's bar is higher than Fortnite's** (2K: only dunk / 3PT / buzzer-beater / crowd-roar — skip routine layups; Fortnite: lower bar, elims are rarer).
5. **Layout.** Detected clips default to **`split`** (game + Sony face-cam, like F9); per-profile override allowed; **`vertical` fallback** if a VOD has no face-cam source. Feeds the *existing* split renderer the same `(source_path, face_source_path, start, end)` it already expects.

---

## 2. Storage — additive `detections` table (decision: A1)

- **Add one new table `detections`.** No change to the `markers` or `clips` tables → existing rows read/write exactly as before (zero regression).
- Each detected clip emits a **normal marker with `kind='gameplay'`** (already allowed by the CHECK, already maps to `split`), so the existing `create_clips` path + FK are satisfied untouched. Event metadata (type, confidence, weight, source, meta, anchor) lives in `detections`, linked to the marker/clip.
- Detected windows are **anchored/asymmetric** (PRE > POST) and passed to `create_clips` **directly**, bypassing the fixed-60 s `process_single_marker` (which still serves manual F8/F9 markers, unchanged).
- **Idempotency:** key on `(source_hash, detector_version, profile_version)`; skip re-emit if already present. Replaces float-exact `clip_exists` for the detection path.
- `detections` columns (CP1 to finalize): `id, clip_id, marker_id, recording_id, t_anchor, type, source, confidence, weight, meta(JSON), source_hash, detector_version, profile_version, created_at`.

---

## 3. GameProfile — YAML on disk + typed dataclass loader

- **Config format = YAML files** (one per game, in `zerino/detection/profiles/`), so the operator can tune without touching code.
- **Loaded into a typed `GameProfile` dataclass** with validation — type-safety in code, editability on disk. (YAML + dataclass, not one or the other.)
- Carries: `game_id`, `player_identity` (Fortnite gamertag / 2K team side — Decision 1), `hud_regions` (fractional **0–1** coords), `event_weights` (rarity), **`score_threshold`** (the dial — per-game, Decisions 3+4), `signal_weights` (cluster / audio / special-event), `pre`/`post` padding, `clip_budget`, `min_dur`/`max_dur`, `default_layout` (`split`, Decision 5).
- A malformed/missing profile fails loudly at load (never silently mis-detects).

---

## 4. Invocation — `cli/detect.py` **and** `reprocess --detect`

- **`cli/detect.py`** — dedicated Windows batch entry: recording/source → adapter → core → emit → existing render+post. Clean boundary, doesn't modify existing CLIs.
- **`reprocess --detect`** — add a `--detect` flag to the existing `cli/reprocess.py` so a recording can be re-run through detection for recovery / re-tuning.
- Both call the **same** `DetectionService` + emission path (no logic duplicated).

---

## 5. Precision/Recall gate (Fortnite elim detector)

- **Provisional floor (set now, at CP1 — minimum to proceed):** recall ≥ **0.8**, precision ≥ **0.7**.
- **Real target (set after the first golden VOD, Phase 2):** raise based on measured difficulty, aiming for recall ≥ **0.9**, precision ≥ **0.8** *if the footage supports it*.
- **Recall is prioritized over precision** — a missed highlight is gone forever; a false positive is just deleted in review.
- **Recall is weighted toward high-value events** (multi-kills, clutches) over boring solo kills — missing a clutch is far worse than missing a routine kill.
- 2K gets its own targets at Phase 3 (higher bar per Decision 4).

---

## 6. What CP1 still defines (interfaces, not new decisions)

These follow from the above and get finalized in the CP1 strategy review (no code until signed off):
- Exact `Event` / `DetectorAdapter` / `GameProfile` signatures and the `detections` DDL.
- The `zerino/detection/` package layout with the hard import boundary (no torch/cv2/ocr at module top).
- The timebase utility (PTS / sample-index → source-relative seconds) and the OBS VFR verification.
- The synthetic-fixture test suite (Phase 1) + the non-regression test (all phases) + the golden-VOD precision/recall harness (Phase 2).

**Roadmap position:** Phase 0 ✅ done → **at CP1** for Phase 0.5 (foundations) + Phase 1 (core). No `zerino/detection/` package exists yet; no feature code written.
