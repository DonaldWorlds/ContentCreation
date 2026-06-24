# Detection Semantics — Locked Product Decisions

> Add this section to `HIGHLIGHT_DETECTION_BUILD_PLAN.md`.
> These define **what counts as a highlight** — the product/semantic rules the
> detector must honor. They were agreed with the operator and were not captured
> in the original plan. Date locked: 2026-06-22.

---

## What "a highlight" means (the core semantics)

The detector does **not** clip everything that happens. It clips the operator's
own strong moments, clusters rapid action into single clips, and uses a tunable
score to keep good moments and drop boring ones. The four rules below are the
contract every adapter and the core must satisfy.

---

### Decision 1 — My events only (operator-positive events)

Detect **only the operator's own positive events**:

- **Fortnite:** the operator's **eliminations** ("you eliminated X"). Ignore
  deaths ("X eliminated you").
- **NBA 2K:** the operator's **team's made shots / highlight plays**. Ignore
  opponent scores.

Player identity (Fortnite gamertag, 2K team side) lives in **GameProfile** and
is filtered **in the adapter, never in the game-agnostic core**. The core only
ever sees an already-filtered `list[Event]`; it never knows whose events they are.

- **Resolves:** Open Question #5 (player identity has no storage today) and Gap B.
- **Phase impact:** GameProfile identity storage becomes a **Phase 0.5** item —
  it must exist before the Fortnite adapter (Phase 2) can filter.

---

### Decision 2 — Multi-event clustering (rapid action = one clip, ranked higher)

Rapid consecutive events (multi-kills, scoring runs) **cluster into ONE window**,
not several separate clips, and that clustered window is **ranked higher** than
an isolated event.

- A triple-kill in 5 seconds → **one** clip of the whole sequence, scored at the
  top — not three clips.
- This is already the plan's **density scoring + clustering bonus + asymmetric
  anchored windowing** (climax at ~65–70% of the window). This decision
  **confirms** that design covers the requirement; no architectural change.

---

### Decision 3 — Tunable scoring dial (keep the good, drop the boring)

Every detected event is **scored** from combined signals, and only events that
score **above a threshold** become clips. This is the quality mechanism — it is
how a boring single kill is skipped while a great single kill is kept.

**Signals that raise an event's score:**
- **Cluster bonus** — part of a multi-event burst (Decision 2).
- **Audio intensity** — loud gunshot / crowd roar / swish at the moment.
- **Special-event type** — headshot, dunk, three-pointer, buzzer-beater, etc.

**Behavior:**
- Boring isolated event (no cluster, no audio spike, nothing special) → **low
  score → skipped**.
- Strong isolated event (e.g. headshot + loud hit, or dunk + crowd roar) →
  **high score → kept**.

**The threshold is an adjustable per-profile DIAL in GameProfile**, tuned by the
operator watching results (raise it if too many boring clips, lower it if too
few). It is not a fixed constant.

- **Must reconcile with posting cadence:** the poster spaces clips ~2h apart
  (Open Question #6). The score-ranked output + clip budget must respect that
  cadence — produce a ranked shortlist, not an unbounded flood.

---

### Decision 4 — Per-game bar (different threshold per game)

The scoring threshold is **per-game**, and **2K's bar is set higher than
Fortnite's**, because basketball produces far more scoring events.

- **2K:** do **not** clip every made basket. Only clip score-deltas that carry a
  signal — **dunk, three-pointer, buzzer-beater, or crowd roar**. A routine
  layup scores low and is skipped.
- **Fortnite:** a lower bar is acceptable since eliminations are rarer than
  baskets.
- GameProfile thresholds **must support per-game tuning** (each profile carries
  its own dial value and signal weights).

---

### Decision 5 — Layout for auto-detected clips (LOCKED)

The operator records gameplay **and** a Sony face-cam together. The existing
hotkey already chooses layout:

- **F8 → square** (`kind='talking_head'`)
- **F9 → split** (`kind='gameplay'`, game + face side by side)

Both render paths (split and square) **already work** and are in active use.

**Decision:** auto-detected gameplay highlights **default to `split`** (gameplay
+ Sony face-cam) — because that is exactly the layout the operator would press F9
for. Rules:

- **Default:** detected highlight → `split` (game + face).
- **Per-profile override:** GameProfile may set a different default layout per
  game if ever needed.
- **No-face fallback:** if a given VOD has no face-cam source, fall back to
  `vertical` (cropped gameplay) so the clip still renders instead of producing a
  broken/empty split pane.

**Plumbing the detector must satisfy (Phase 0.5):**
- The detector must know the **face-cam source path** for a recording and that
  the game + face sources are **time-aligned** (the dual-split renderer seeks
  both to a shared timeline — `export_generator.py:905-906`). Detected windows
  feed the *existing* split renderer; the detector supplies the same
  `(source_path, face_source_path, start, end)` the renderer already expects.

> ⚠️ **NON-REGRESSION GUARDRAIL (applies to the whole feature):** The detection
> layer sits **in front of** the existing marker → clip → render → post pipeline
> and **reuses it unchanged**. It must **not** modify or break the working split
> (F9) or square (F8) render paths, the hotkey marker flow, or any existing
> behavior. New event types and the detected-window emission path are **additive
> only**. Every phase's CP2/CP2.5 must include a regression check proving the
> existing F8/F9 → clip → render output is byte-identical to before.

---

## Summary table

| # | Decision | Lives in | Phase | Status |
|---|---|---|---|---|
| 1 | My events only (identity filter) | GameProfile + adapter | 0.5 (storage), 2/3 (filter) | LOCKED |
| 2 | Multi-event clustering → one clip, ranked up | Core (density/windowing) | 1 | LOCKED (confirms design) |
| 3 | Tunable scoring dial above a threshold | GameProfile (dial) + core (score) | 1 + 0.5 (config home) | LOCKED |
| 4 | Per-game bar (2K higher than Fortnite) | GameProfile (per-profile threshold) | 2/3 | LOCKED |
| 5 | Detected clips → split (game+face) default, vertical fallback | Router / KIND_TO_LAYOUT | 0.5 | LOCKED |

> **Guardrail:** detection is additive and reuses the existing pipeline. It must not
> break the working F8 (square) / F9 (split) render paths. Every phase's CP2/CP2.5
> includes a regression check on existing behavior.
