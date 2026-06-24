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
Highlight detection: CP1 interface spec APPROVED 2026-06-24 (see PROJECT_REVIEW.md §E).
Work on branch `highlight-detection`; main stays shippable. Phase 0.5 next, tests-first.
No feature code yet / no zerino/detection/ package.
