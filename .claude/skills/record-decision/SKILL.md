---
name: record-decision
description: Use when the operator locks or finalizes a project decision. Record it in the authoritative doc, update the memory file + index, reconcile the other docs so none contradict it, and report what changed.
---

# Record a locked decision

Run this whenever the operator locks or finalizes a decision, so it lands in one
authoritative place and nothing else contradicts it.

## Steps
1. **Write it to the authoritative doc.** Find the doc that owns the topic (per
   CLAUDE.md "Which doc is authoritative" — e.g. `DETECTION_DECISIONS.md` for
   highlight detection). Add the decision there, dated, in that doc's style.
   That doc wins on any conflict.
2. **Update memory.** Edit the relevant file in
   `~/.claude/projects/-Users-donaldk-Desktop-Content-Business/memory/` (create one
   if none fits) and add/update its one-line pointer in `MEMORY.md`. Keep it to the
   durable fact + why, not a transcript.
3. **Reconcile the other docs.** Scan related docs (e.g. `PROJECT_REVIEW.md`,
   `HIGHLIGHT_DETECTION_BUILD_PLAN.md`) and fix anything that now contradicts the
   decision — point them at the authoritative doc instead of duplicating it. Mark
   superseded docs historical.
4. **Confirm back.** Tell the operator exactly what changed: which doc got the
   decision, which memory file + index line, and which other docs were reconciled.

## Rules
- One authoritative home per decision; everything else points to it (no drifting copies).
- Non-destructive: add or clarify, don't delete prior context unless it's now wrong.
- Don't invent the decision — only record what the operator actually locked.
