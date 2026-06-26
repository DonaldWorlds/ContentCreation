# zerino — project rules for Claude Code

zerino: OBS capture → marker (F8 square / F9 split) → ffmpeg render → Zernio post.
Cross-platform: macOS capture daemon + Windows render/detection batch.
This file auto-loads every session — keep it short. It points to the authoritative
docs; it does not duplicate them.

## Working discipline (every change)
- Checkpoints: CP1 strategy → CP2 tests-first → CP2.5 red-proof → CP3 diff review.
  🛑 consult gates are hard stops — no code past a 🛑 without my explicit sign-off.
- Evidence-first: pull DB/log/ffprobe ground truth before claiming a cause or fix.
  Never guess or over-claim.
- Verification-before-done: run the check and show output before saying it works.

## When to use which skill (superpowers — installed)
- Writing a plan → writing-plans
- CP2 (tests-first) → test-driven-development
- CP2.5 / before claiming done → verification-before-completion
- CP3 (diff review) → requesting-code-review
- Diagnosing a bug or cause → systematic-debugging + the debug_get_evidence_first memory
- Locking / finalizing a decision → record-decision (project skill)

## NON-REGRESSION guardrail (this is a live revenue pipeline)
- All new work is ADDITIVE. Do NOT change the working F8→square / F9→split render
  paths, the hotkey/marker flow, OBS capture, or posting.
- Quality-critical — do not touch without approval + a regression check:
  zerino/ffmpeg/export_generator.py, zerino/processors/split.py, square.py,
  _captions.py, zerino/composition/composition_rules.py
- Prove F8/F9 render output is byte-identical before vs after changes near these.

## Execution environment (LOCKED)
- Detection = Windows-side batch stage; live capture daemon = macOS.
- Windows GPU = GTX 1050 Ti (sm_61, 4GB) shared with faster-whisper. Mac has no NVIDIA.
- torch/OCR/GPU deps lazy-imported + optional; runtime CUDA-else-CPU;
  default OCR = Tesseract-CPU; never run OCR + Whisper on the GPU at once.

## Which doc is authoritative
- DETECTION_DECISIONS.md — AUTHORITATIVE for highlight detection (wins on conflict)
- HIGHLIGHT_DETECTION_BUILD_PLAN.md — detection phase plan / checkpoints
- ARCHITECTURE_FINDINGS.md — repo audit · PROJECT_REVIEW.md — whole-system review
- highlight_detection_semantics.md — detection product rules
- RUNBOOK.md — how to run/operate · CLIPPING_QUALITY_PLAN.md — render-quality backlog
- HISTORICAL (do not follow as current): HANDOFF_TO_ZERINO.md, DUAL_SOURCE_SPLIT_PLAN.md

## Status
Highlight detection: PHASE 2 BUILT + UNSEEN-VALIDATED (Fortnite adapter, merged to main).
Two-stage audio-gated OCR (banner = own-elim + multi-kill signal; left feed + fuzzy/alias
gamertag; identity filter strict — squadmate elims excluded), MediaHandle, probe_timebase
(OBS = CFR 30/60), cache idempotency, cli/detect.py + reprocess --detect.
Render-mode integration CP (merged to main) — all gated steps DONE:
  Step A ✅ UNSEEN-FOOTAGE VALIDATION PASSED on 2026-06-26 00-08-55 — the real detector emitted
    8 events (380 KILL, 410 MULTI_ELIM x6, 476/487/492 KNOCK, 496 MULTI_ELIM x4, 934 KILL) the
    operator confirmed match real kills, NO code change. Detection GENERALIZES.
  Step B ✅ seg1 golden times 38/42/54 OPERATOR-RATIFIED; test_golden_pr flipped xfail -> HARD
    assert (P/R 1.00 on the 3 calibrated segments).
  Step C ✅ RENDER-TO-REVIEW PROVEN — cli/detect.py --render-review DIR renders detected windows
    via the existing split renderer + face pair (Decision 5) into a review dir, NO post. Smoke
    test made 2 split clips on the multi-kill climaxes (clips in renders/detection_review/);
    operator spot-check PASSED.
FULL HANDS-OFF PIPELINE COMPLETE + MERGED (shipping OFF behind two independent switches):
  recording finishes -> ClipWorker.process_recording (manual F8/F9, unchanged) -> [AUTORUN]
  detect_recording -> detect/core/emit -> [AUTOPOST] create_clips -> queue_clip_jobs_for_posting
  -> Zernio (the SAME path F8/F9 uses; reviewed on the Zernio dashboard, no human-in-loop step).
Two switches, BOTH DEFAULT OFF (feature fully inert; daemon byte-identical until flipped):
  - ZERINO_DETECTION_AUTORUN  = does detection run when a recording finishes (clip_worker hook).
  - ZERINO_DETECTION_AUTOPOST = does detection post (gated inside detect_recording).
Rails when ON: per-profile score_threshold (only high-value), clip_budget + ~2h cadence (no flood).
§4 honored: manual F8/F9 + create_clips/queue/render untouched (recipe-pins green); detection
lazy-imported (flag OFF -> daemon imports no detection/OCR/GPU); detection errors caught per-job.
TO GO LIVE (operator, in order): (1) fix the capture card (Elgato NO-SIGNAL face); (2) flip
AUTORUN=1 for a dress rehearsal (detection runs + emits, still no posts); (3) flip AUTOPOST=1
to actually post. See DETECTION_DECISIONS.md + memory [[fortnite-detection-calibration]].
