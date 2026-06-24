# Highlight Detection Layer — Build & Test Plan

> **For:** a Claude Code session working inside the `zerino` repo (`Content_Business`).
> **Status:** Phase 0 (discovery/audit) **DONE** — see `ARCHITECTURE_FINDINGS.md`. The consult gate is **cleared** and the decisions below are **LOCKED**. We are at **CP1 for Phase 0.5 + Phase 1** (agree interfaces before any code). Phases 1–4 are now finalized against the real repo, not provisional.
> **Companion docs:** `DETECTION_DECISIONS.md` (**authoritative** single-source of the locked foundational/CP1 decisions — wins on any conflict), `ARCHITECTURE_FINDINGS.md` (the audit + system-health findings), and `highlight_detection_semantics.md` (the product decisions, integrated below as §3).
> **⚠️ NON-REGRESSION GUARDRAIL (whole feature):** detection is **additive** and **reuses the existing pipeline unchanged**. It must not modify or break the working **F8→square** / **F9→split** render paths, the hotkey marker flow, or any existing behavior. See §4.

---

## 0. Problem statement

The current pipeline places clip markers by timestamp, stores them, and renders them. The markers don't land on the actual highlight moments, so clips miss the play (no kill, no made shot, no payoff). Pressing "clip" captures aftermath, not action.

**Root cause:** we are clipping by *human press time*, not by *event*. Motion detection is the wrong sensor — in any game the whole screen is always moving. The fix is an **event-driven detection + scoring layer** that sits in front of the existing marker system. The render pipeline does **not** change; we just generate smarter markers.

> **Verified in Phase 0 (see `ARCHITECTURE_FINDINGS.md` §7):** the dominant cause is **human reaction latency exceeding the fixed 10 s pre-buffer** — users press F8/F9 *after* the play resolves, so with a slow reaction the action falls before the clip even starts. (A real but *separate* ~5 s `start_time` drift was also confirmed, n=22 recordings, but its direction makes the pre-roll *larger*, so it does **not** cause "miss the play.") **Event-anchored detection removes reaction latency entirely** — that is the real win. OBS VFR-ness is still unverified (needs the Windows box / a fresh recording).

---

## 1. Core design principle (the thing that makes it work for any game)

Two layers, strictly separated:

- **Game-agnostic core** (built once, never game-specific): event schema → fusion → density scoring → asymmetric windowing → dedupe → emit markers in the existing format. **The core only ever sees an already-filtered `list[Event]`; it never knows whose events they are or which game produced them.**
- **Per-game detector adapter** (one per game): answers only two questions —
  1. *What counts as an event in this game?*
  2. *Where/how do I read it?* (HUD region + audio signature)
  Plus: the adapter applies the **"my events only" identity filter** (§3 Decision 1) before handing events to the core.

Adding a new game = writing a new adapter. Nothing downstream changes. First two adapters:

| Game | Event types | Visual signal | Audio signal |
|---|---|---|---|
| **Fortnite** | elimination, multi-elim, knock, victory | elim feed (OCR) + hitmarker (template) | gunshots / elim sound (onset density) |
| **NBA 2K** | made shot (esp. 3PT/dunk), block, steal, poster | scoreboard score-delta (OCR) + shot feedback popup | crowd roar + commentary pitch spike (onset/energy) |

These two are structurally opposite (kill feed vs. no kill feed). If the core serves both unchanged, it serves anything. **Confirmed in Phase 0:** nothing in the repo is game-coupled, so the core can stay fully game-neutral.

---

## 2. Execution environment — LOCKED (operator, 2026-06-03)

Cross-platform project (Mac + Windows). **Detection runs as a Windows-side batch stage**, separate from the live macOS capture daemon.

- **Windows GPU = GTX 1050 Ti** (Pascal, compute capability **sm_61**, 4 GB VRAM, no tensor cores), **already shared with faster-whisper**. **Mac has no NVIDIA.**
- **Runtime device detection** (CUDA if present, else CPU). **No hardcoded CUDA.** All OCR/GPU/torch deps are **lazy-imported and optional**, so the Mac side and the live capture daemon run without them installed.
- **Phase 0.5 env check (gating):** verify the installed torch+CUDA build actually supports **sm_61** before relying on the GPU.
- **Keep OCR light.** Do **not** run OCR and Whisper on the GPU concurrently — sequence them or pin one to CPU. **Default OCR = Tesseract-CPU** (HUD text is high-contrast → frees the GPU for Whisper); **EasyOCR (small models) is the fallback** only if recall falls short. Lean hard on **two-stage audio gating** to minimize OCR calls. Assume no GPU headroom; tune empirically against the eval harness.

---

## 3. Detection semantics — LOCKED (operator, 2026-06-22)

These define **what counts as a highlight** — the product rules every adapter and the core must satisfy. Full rationale in `highlight_detection_semantics.md`.

**Decision 1 — My events only.** Detect **only the operator's own positive events**: Fortnite = the operator's eliminations (ignore deaths); 2K = the operator's team's made shots/highlight plays (ignore opponent scores). Player identity (gamertag / team side) lives in **GameProfile** and is filtered **in the adapter, never in the core**. → *Resolves Open Question #5 + Gap B. Identity storage is a Phase 0.5 item; filtering is Phase 2/3.*

**Decision 2 — Multi-event clustering.** Rapid consecutive events (multi-kills, scoring runs) **cluster into ONE window**, ranked **higher** than an isolated event — a triple-kill in 5 s is one top-ranked clip, not three. This is exactly the core's **density scoring + clustering bonus + asymmetric anchored windowing** (climax at ~65–70%). *Confirms the Phase 1 design; no architectural change.*

**Decision 3 — Tunable scoring dial.** Every event is **scored** from combined signals; only events **above a threshold** become clips. Score-raising signals: **cluster bonus** (Decision 2), **audio intensity** (loud gunshot / crowd roar / swish), **special-event type** (headshot, dunk, 3PT, buzzer-beater). Boring isolated event → low score → skipped; strong isolated event → kept. The **threshold is an adjustable per-profile DIAL in GameProfile**, not a fixed constant. *Must reconcile with posting cadence (Open Question #6): produce a ranked, clip-budgeted shortlist, not a flood.*

**Decision 4 — Per-game bar.** The threshold is **per-game**, and **2K's bar is higher than Fortnite's** (basketball produces far more scoring events). 2K: only clip score-deltas carrying a signal — **dunk / 3PT / buzzer-beater / crowd roar**; routine layups score low and are skipped. Fortnite: a lower bar is fine since elims are rarer. GameProfile thresholds **must support per-game tuning** (each profile carries its own dial + signal weights).

**Decision 5 — Layout for auto-detected clips.** The operator records gameplay **and** a Sony face-cam together. Auto-detected gameplay highlights **default to `split`** (game + face) — the layout the operator would press F9 for. Rules: default = `split`; per-profile override allowed; **no-face fallback = `vertical`** (cropped gameplay) so the clip still renders instead of a broken split pane. *Plumbing (Phase 0.5):* the detector must know the **time-aligned face-cam source path** and feed the *existing* split renderer the same `(source_path, face_source_path, start, end)` it already expects (`export_generator.py:905-906`).

| # | Decision | Lives in | Phase |
|---|---|---|---|
| 1 | My events only (identity filter) | GameProfile + adapter | 0.5 (storage), 2/3 (filter) |
| 2 | Clustering → one clip, ranked up | Core (density/windowing) | 1 |
| 3 | Tunable scoring dial above threshold | GameProfile (dial) + core (score) | 0.5 (config home) + 1 |
| 4 | Per-game bar (2K > Fortnite) | GameProfile (per-profile threshold) | 2/3 |
| 5 | Detected → split default, vertical fallback | Router / KIND_TO_LAYOUT | 0.5 |

---

## 4. NON-REGRESSION guardrail (the discipline rule)

The detection layer sits **in front of** the existing marker → clip → render → post pipeline and **reuses it unchanged**:

- **Additive only.** New event types, the GameProfile config, and the detected-window emission path are *new code*. They must not alter the F8 (square) / F9 (split) hotkey flow, `ClipService.process_single_marker`'s existing behavior for manual markers, the processors, or `export_generator` render logic.
- **Schema changes are non-destructive.** The Phase 0.5 migration *adds* a home for detected events (e.g. a `detections` table and/or additive marker columns + relaxed `kind` CHECK) without changing how existing rows are read or written.
- **Regression proof every phase.** Each phase's CP2/CP2.5 includes a regression check proving the existing **F8/F9 → clip → render output is byte-identical to before** (same input → same bytes). If a change would alter existing render output, stop and re-scope.

---

## 5. Checkpoint discipline (applies to every phase below)

- **CP1 — Strategy.** Agree the approach + interface for the phase before writing anything. Operator sign-off.
- **CP2 — Tests.** Write the tests first — **including the §4 non-regression check.**
- **CP2.5 — Red proof.** Run the tests, paste output proving they fail for the right reason (not import errors). Operator sign-off.
- **CP3 — Batch diff review.** Implement, then present the full diff for review before merge.

**Consult gates** (🛑) are hard stops where Claude Code reports and waits for the operator. No proceeding past a 🛑 without explicit go-ahead.

---

## 6. PHASE 0 — Discovery & architecture audit ✅ DONE (2026-06-03)

**Goal was:** stop building blind. Map what actually exists, then re-plan against reality. **Delivered:** `ARCHITECTURE_FINDINGS.md` (marker model, render path, clip origin, concurrency, media I/O, existing analysis, coupling map, integration seams, system-health audit, MUST-FIX backlog).

**Key outcomes that shaped this plan:**
- The repo is `zerino` — a macOS capture daemon → F8/F9 marker → fixed-60 s clip → ffmpeg render → Zernio post. **No task scheduler, no "cinematic pipeline," and zero detection infra** (no OCR, no audio signal analysis, no per-game config, no content cache, no eval harness).
- The **render path is solid and reusable**: `ClipJob(source_path, start, end, layout, face_source_path)` in source-relative float seconds, accurate two-stage ffmpeg seek. Detection reuses it wholesale.
- **Cleanest seam:** `ClipService.process_recording` (`clip_service.py:261`) → run detection → emit anchored windows via `create_clips`, **bypassing** the fixed-60 s `process_single_marker`. Batch entry = a new **`cli/detect.py`** + a **`reprocess --detect`** flag (LOCKED — `DETECTION_DECISIONS.md` §4).
- Timebase root-cause verified + corrected (§0). Execution env locked (§2). Five semantic decisions locked (§3).

> 🛑 Consult gate: **CLEARED.** Open questions resolved — source = OBS `recordings/` files; layout = existing split (Decision 5); player identity = GameProfile (Decision 1); timebase verified.

---

## 7. PHASE 0.5 — Foundations (MUST-FIX before Phase 1)

These are the prerequisites the original plan assumed away. All **additive / non-regression** (§4).

1. **Canonical timebase utility** — `zerino/detection/timebase.py`: map frame **PTS** (never `frame_index / avg_fps`) and audio **sample position** → source-relative seconds, consistent with the renderer's `-ss`. **Verify OBS VFR/CFR** on a real recording first.
2. **Schema migration** (`db/migrate.py`) — give detected events a home: a `detections` table and/or additive `markers` columns (`confidence`, `weight`, `type`, `source`, `meta`) + relax the `kind` CHECK; make `clips.marker_id` nullable or auto-create marker rows. Non-destructive to existing rows.
3. **Window-emission path** — feed detected anchored `(start, end)` windows into `create_clips` **without** going through the fixed-60 s `process_single_marker`, so asymmetric anchoring survives.
4. **Detector-version-aware idempotency** — replace float-exact `clip_exists` matching so re-runs don't duplicate clips; key on `(source identity, detector_version, profile_version)`.
5. **GameProfile config home** — **YAML files** in `zerino/detection/profiles/` loaded into a **typed `GameProfile` dataclass** (editable on disk + type-safe in code — `DETECTION_DECISIONS.md` §3), carrying per-game: **fractional 0–1 HUD regions**, event weights/rarity, the **scoring-threshold dial** (Decision 3), **per-game bar** (Decision 4), PRE/POST padding, clip budget, min/max duration, **player identity** (Decision 1), and **default layout** (Decision 5).
6. **Layout plumbing** — detected clips default `layout="split"` with the time-aligned **face-cam source path** resolved (reuse `ClipService._find_face_pair`), `vertical` fallback when no face source (Decision 5). Reuses existing split renderer untouched.
7. **Execution-env check** — runtime CUDA-else-CPU; verify torch+CUDA supports **sm_61**; confirm Tesseract-CPU present; keep all GPU/OCR deps lazy-optional so the Mac side imports nothing GPU-related (§2).

---

## 8. PHASE 1 — Game-agnostic core

Build the spine with **zero real video** — tested entirely against synthetic event fixtures so it's verifiable in isolation. Lives in `zerino/detection/`.

### 8.1 Event schema
```
Event:
  t: float            # seconds, source-relative (from the Phase 0.5 timebase util)
  type: str           # adapter-defined (KILL, MADE_SHOT_3PT, DUNK, BLOCK, ...)
  source: str         # detector id (ocr_killfeed, audio_onset, scoreboard_delta, ...)
  confidence: float   # 0..1
  weight: float       # base highlight value of this event type (rarity)
  meta: dict          # freeform (shot value, killstreak count, special-event flags, ...)
```

### 8.2 Adapter contract
```
DetectorAdapter:
  game_id: str
  detect(media) -> list[Event]      # adapters implement this; events ALREADY identity-filtered (Decision 1)
  event_weights: dict[str, float]   # rarity table for scoring
```

### 8.3 Core algorithm
1. **Fusion:** merge all adapter events onto one timeline.
2. **Density scoring:** slide a window; score = sum(weight × confidence) with a **clustering bonus** (events close together score super-linearly — 3 kills in 6 s ≫ 3 kills over a minute) plus **audio-intensity** and **special-event** signals (Decision 3). This is the "is this moment good" signal.
3. **Peak detection:** windows scoring **above the per-profile threshold dial** (Decisions 3 + 4) become clip candidates.
4. **Anchoring + asymmetric windowing:** anchor on the highest-weight event in the cluster (the climax). Window = `[anchor − PRE, anchor + POST]` with `PRE > POST` so the climax sits at ~65–70% through the clip. Clamp to media bounds.
5. **Dedupe/merge:** overlapping candidates merge or the higher-scoring one wins. Apply the **clip budget** so the ranked shortlist respects posting cadence (Decision 3).
6. **Emit:** convert candidates into the existing marker/clip format via the Phase 0.5 emission path — **additive, non-regression** (§4).

### 8.4 Tests (CP2)
- Synthetic event streams → correct candidate windows, anchor placement (climax ~65–70%), PRE/POST padding, clamping.
- Clustering: dense cluster outranks sparse same-count cluster (Decision 2).
- Scoring dial: events below threshold dropped, above kept; raising the dial drops more (Decision 3).
- Dedupe + clip budget: overlapping windows merge; output respects the budget.
- Emission validates against the **real (post-migration) marker/clip schema**.
- **Non-regression:** an existing F8/F9 marker still produces byte-identical clip output (§4).

---

## 9. PHASE 2 — Fortnite adapter (first vertical slice)

Pick Fortnite first: clean FPS model, proves the full chain end-to-end (real VOD in → real clips out).

### 9.1 Detectors
- **Elim-feed OCR:** crop the elim-feed region from **GameProfile** fractional coords (calibrate off a representative OBS recording — region/font/resolution vary, do **not** hardcode blind). **Default Tesseract-CPU; EasyOCR-small fallback** (§2). **Filter to events where the operator is the eliminator** using the GameProfile gamertag (Decision 1) — done in the adapter, not the core.
- **Audio onset density:** ffmpeg → audio → onset detection → density over a sliding window. Gunshot/elim clusters = firefights. Drives the **two-stage gate** (cheap audio pass first; OCR only where audio is hot — §2).
- (Optional) **Hitmarker template match** (OpenCV) for landed-shot confirmation pre-elim.

### 9.2 Calibration step (per game, done once off one VOD)
Document the elim-feed region (fractional 0–1), font, OCR sample rate, and kill-sound signature. Store in **GameProfile config**, not code constants. (Region-calibration tooling is a Phase 2 deliverable — Gap F.)

### 9.3 Tests (CP2)
- **Golden VOD fixtures:** hand-label 2–3 short Fortnite clips with ground-truth elim timestamps. Measure detector **precision/recall** against labels. **Gate (LOCKED — `DETECTION_DECISIONS.md` §5):** provisional floor recall ≥ 0.8 / precision ≥ 0.7 to proceed; real target recall ≥ 0.9 / precision ≥ 0.8 after the first golden VOD if the footage supports it; **recall is prioritized over precision** and weighted toward **high-value events** (multi-kills, clutches). This is the objective "does it catch the play" gate.
- Window-placement spot-check: elim lands at ~65–70%.
- Lower per-game bar than 2K (Decision 4).
- Integration smoke test: VOD → clips (default `split` + face, Decision 5), manual spot-check checklist.
- **Non-regression (§4).**

---

## 10. PHASE 3 — NBA 2K adapter (generalization proof)

No kill feed. Proves the core is truly game-agnostic.

### 10.1 Detectors
- **Scoreboard score-delta OCR:** read the score region (GameProfile fractional coords); a +2/+3 jump = made basket. Tesseract-CPU default.
- **Shot-feedback / "+3" popup** detection if present (template or OCR).
- **Crowd-roar audio:** energy/onset spike on made shots, dunks, big plays — a strong, cheap signal; drives the two-stage gate.
- (Optional later) commentary pitch-rise detection.
- **Higher per-game bar (Decision 4):** only clip score-deltas carrying a signal — **dunk / 3PT / buzzer-beater / crowd roar**; skip routine layups. Weight dunks/3PT/blocks/posters higher (rarity table).
- **Filter to the operator's team** (Decision 1) — opponent baskets ignored.

### 10.2 Watch-outs specific to 2K
- Replays and cutscenes re-show plays — dedupe against the replay or you'll double-clip.
- Score changes on free throws / opponent baskets — filter to the player's team / exciting shot types.

### 10.3 Tests (CP2)
- Golden 2K VOD fixtures, hand-labeled made-shot/dunk/block timestamps → precision/recall.
- Confirm the **same core, unchanged**, consumes 2K events. If the core needed edits to fit 2K, the abstraction leaked — fix the abstraction, not patch the core.
- Per-game bar holds (routine layups skipped, dunks/3PT kept).
- **Non-regression (§4).**

---

## 11. PHASE 4 — Semantic re-ranker (deferred)

OpenCLIP / TwelveLabs as a **re-ranker over surviving candidates only** — never the primary detector. Re-score the top-N candidates for visual "excitement," drop false positives, reorder. Optional; ship Phases 1–3 first. (Torch/GPU path — honor §2: lazy-optional, sm_61, no concurrent GPU use with Whisper.)

---

## 12. Testing strategy (summary)

| Layer | Method | Pass criteria |
|---|---|---|
| Core | Synthetic event fixtures | Deterministic window/anchor/dedupe/scoring correctness |
| Detectors | Hand-labeled golden VODs | Precision/recall ≥ targets set at CP1 |
| Windowing | Spot-check on real clips | Climax at ~65–70% of clip |
| Integration | VOD → clips smoke test | Manual checklist; clips contain the play; default split + face |
| **Non-regression** | **F8/F9 → clip → render, before vs after** | **Byte-identical existing output (§4)** |

The golden-VOD precision/recall harness is the heart of it — it's how we objectively answer "is it catching the gameplay" instead of eyeballing.

---

## 13. Known risks (flag early)

- OCR flakiness at low bitrate / motion blur / overlapping HUD elements.
- HUD region drift across resolutions, platforms, and game versions → keep regions in **GameProfile**, never hardcoded.
- Music/voice chat drowning audio cues → may need band-pass or a small sound classifier later.
- Elim feed shows enemy kills too → must filter to the player (Decision 1).
- 2K replays/cutscenes polluting detection → dedupe.
- **GPU contention:** the GTX 1050 Ti (4 GB, sm_61) is shared with Whisper — never run OCR + Whisper on the GPU at once; default Tesseract-CPU; assume no headroom (§2).
- **Regression risk:** any change that alters existing F8/F9 render output is a stop-and-rescope (§4).

---

## 14. Order of operations recap

1. **Phase 0** — audit repo, write findings, 🛑 consult, finalize this plan. ✅ DONE.
2. **Phase 0.5** — foundations / MUST-FIX (timebase, schema migration, emission seam, GameProfile, layout plumbing, env check).
3. **Phase 1** — core, tested on synthetic events.
4. **Phase 2** — Fortnite adapter, validated end-to-end on labeled VODs.
5. **Phase 3** — 2K adapter, proves generalization.
6. **Phase 4** — semantic re-ranker (optional).

Every phase runs CP1 → CP2 → CP2.5 → CP3, **and a §4 non-regression check at CP2/CP2.5**. Honor every 🛑.
