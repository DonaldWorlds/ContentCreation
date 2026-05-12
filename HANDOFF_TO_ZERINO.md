# Handoff: Pivot from `clipper_for_clients` to `content_business/zerino`

> **Session note 2026-05-09:** Fixes #1–#5 from the in-session debug plan
> have landed in this checkout (test on Mac, then push for Windows verify).
> See bottom of this file (§15) for the changelog.

> **For the next session.** Drop this file alongside the zerino project's CLAUDE.md (or just paste it as your first message). It captures everything we learned over two days of building+debugging that the next session needs to start fast.

---

## 1. The decision

**We are abandoning the Reap-based clipper architecture.** The clipper repo at `~/Desktop/Clipper_for_clients/` will be archived. We're building the real clipping-for-clients system **inside `~/Desktop/content_business/`** (the zerino project) because zerino already has the video-engine pieces working, and clipper had to be rebuilt around them anyway.

This handoff explains:
- Why Reap failed
- What clipper proved out (architecture-wise)
- What zerino already has (engine-wise)
- What needs to be added to zerino to ship a clients-ready product
- A concrete first-week plan

---

## 2. Why we left Reap — the incident log

Over one session (2026-05-10 and 2026-05-11) we hit **five distinct production-blocking issues** with Reap's API:

1. **Wrong field-name silent failure.** `create-clips` uses `exportResolution`. `create-captions` uses `resolution` (different field). We sent `exportResolution` to both; create-captions silently ignored ours and capped output at the preset's preferred resolution (**720 for every system preset**). 1080p source → 720p output. Cost us a half-day to find.

2. **Reap rejects its own clipUrls as create-captions input.** The clip URLs Reap returns from create-clips cannot be passed back as `sourceUrl` to create-captions — Reap returns HTTP 400 *"Unable to process video from reap-video-main-bkt-prod..."*. Workaround was download → re-upload → use uploadId. Forced an entire architecture change.

3. **`viralityScore: None` parsing crash.** create-captions responses come back with `viralityScore: null` (since scoring already happened upstream), but our parser was shared with create-clips parsing and crashed on the null. Cost us a run worth of budget.

4. **500 Internal Server Error with no useful message.** Sending `--selected-end` with a sub-2-minute source triggered Reap's backend to 500. Their docs say minimum is 2 min but they accepted shorter inputs sometimes and rejected them others. Inconsistent.

5. **Projects vanish from Reap mid-render.** A project Reap accepted, gave us an `id` for, and processed up to status `finalizing` for 30 minutes — then returned **HTTP 404 "Project not found"** when we polled it after our local timeout. Source-hours were billed. Output didn't exist. No refund mechanism.

**Pattern: Reap is unreliable for serious automation.** Their virality model is good but everything else around it is brittle. Every test costs real money AND produces unpredictable results.

**Conclusion: build the video engine locally.** Whisper + ffmpeg + libass + Ollama. Zero per-clip cost. Deterministic output. Full resolution control.

---

## 3. Stack decisions (locked)

| Concern | Decision | Why |
|---|---|---|
| Transcription | `faster-whisper` (model: `small`) on CPU with int8 | Already working in zerino. Word-level timestamps. ~$0 per source. |
| Clip moment selection | **Ollama (fully local)**, default model `llama3.1:8b` | No external API calls. Free. Pluggable interface so we can swap to Claude/OpenAI later if quality demands it. |
| Caption rendering | **libass `.ass` files + ffmpeg `subtitles=` filter** | Zerino's `processors/_captions.py` already does this with TikTok-style 3-word karaoke chunks. Production-tested. |
| Vertical reframing | ffmpeg crop, **center crop in MVP**, dynamic speaker-tracked crop in v2 | Zerino's `processors/vertical.py` does center crop. Speaker-tracking via MediaPipe+YOLOv8 added in Phase 2. |
| Resolution | **Honor source resolution up to 4K**. Locked at 1080×1920 portrait in zerino's current code. | Match source. Software cannot create 4K from a 1080p input — the user's hard requirement on 4K means the SOURCE must be 4K. |
| Multi-client | `client_id` on every row. Output paths under `storage/clients/<client_id>/...`. CLI accepts `--client <id>`. Default client = `self`. | Productization step. Clipper had this designed but never built. |
| Publishing | Zerino already has `publishing/` using `zernio-sdk==1.3.68` | Reuse as-is. |
| LLM cost | $0 (Ollama is local) | Sustainable per-client economics. |

---

## 4. What clipper proved out — patterns worth porting

The clipper repo (`~/Desktop/Clipper_for_clients/`) is being archived, but **these architectural patterns work well and should be ported into zerino**:

### 4a. Sources abstraction → `~/Desktop/Clipper_for_clients/clipper/sources/`
Files worth reading:
- `base.py` — Source interface returning `FetchResult(remote_url, upload_id, platform, original_locator)`
- `link_router.py` — URL host detection (twitch.tv → Twitch, youtube.com → YouTube, kick.com → Kick, local file → upload)
- `upload.py` — local file source
- `reap_url_source.py` — URL passthrough (no longer needs Reap; just a passthrough handler)

Port to: `~/Desktop/content_business/zerino/sources/` (new directory).

### 4b. SQLite DB with WAL + migrations → `~/Desktop/Clipper_for_clients/clipper/db/`
Files worth reading:
- `schema.sql` — source, reap_project, candidate, variant, post tables. Variant table has `virality_score` denormalized from candidate (the right pattern).
- `migrate.py` — versioned migrations from #001, idempotent, pre-migration snapshot backup
- `store.py` — typed CRUD per model

Key fixes already learned:
- WAL mode + `PRAGMA synchronous=NORMAL` + `PRAGMA foreign_keys=ON` set on every connect
- `connect()` returns `sqlite3.Connection` with row_factory configured

Port to: `~/Desktop/content_business/zerino/db/` — already exists. Augment with clipping-specific tables (`source`, `candidate`, `variant`) keeping zerino's existing schema intact. **Add `client_id` column to all clip-related tables.**

### 4c. Top-N selection with diversity rule → `~/Desktop/Clipper_for_clients/clipper/scoring/selector.py`
Pattern: sort by score desc, walk down, cap N kept per 5-minute window. Prevents 8 clips all coming from the same hot streak.

Port to: `~/Desktop/content_business/zerino/scoring/selector.py` (new file).

### 4d. Pipeline orchestrator pattern → `~/Desktop/Clipper_for_clients/clipper/pipeline.py`
The shape:
```
1. resolve source
2. transcribe (Whisper)        ← was create-clips in Reap version
3. score moments (Ollama)      ← was scoring.selector on Reap candidates
4. select top-N with diversity
5. for each kept moment: cut + crop + caption → mp4 to disk
6. (later) publish via zernio
```

Per-variant error isolation pattern: each item in the inner loop has its own try/except so one failure doesn't kill the whole run. Track failures in a list, report at the end.

### 4e. Click CLI structure → `~/Desktop/Clipper_for_clients/clipper/cli/`
- `__main__.py` — registers subcommands
- `doctor.py` — env + ffmpeg + (Ollama instead of Reap auth) health check
- `run.py` — full pipeline on a locator (URL or file or directory)
- `presets.py` — list available caption styles (becomes: list zerino's caption presets)
- `--client <id>` flag and confirmation prompt on directory batch

Port to: `~/Desktop/content_business/zerino/cli/` — augment what's already there.

### 4f. Resilience patterns we built and verified
- HTTP retry with exponential backoff + jitter, 429 `Retry-After` honoring (in `clipper/reap/client.py`) — useful for Ollama HTTP and any external API
- Per-item try/except in batch loops
- `clipper doctor` as a single all-checks-pass gate before any expensive work

---

## 5. What zerino already has — engine pieces ready to use

Already in `~/Desktop/content_business/zerino/`:

### 5a. Production-quality caption rendering — `processors/_captions.py` (212 lines)
**This is gold.** Already does:
- faster-whisper integration with `word_timestamps=True`
- ASS file generation at PlayResX=1080, PlayResY=1920
- TikTok-style 3-word karaoke chunks
- White → yellow per-word highlighting using `{\c&Hxxxxxx&}` inline color codes
- Hard-coded Style: line with Impact 72pt, alignment=8 (top-center), MarginV=780
- ffmpeg `subtitles=` filter integration with cross-platform path escaping (Windows backslash gotcha already handled)
- Lessons documented in module docstring (SRT→ASS conversion drift, force_style override issues)

**Use as-is for caption rendering in the clipping pipeline.** Maybe add styling variants later (different colors, fonts, positions) but the karaoke approach is solid.

### 5b. Vertical processing — `processors/vertical.py` (76 lines)
Does the vertical (1080×1920) crop + caption burn-in via ffmpeg. Center-crop. Good MVP.

For Phase 2: replace center-crop with face-tracked dynamic crop using MediaPipe + YOLOv8 (pattern documented in `mutonby/openshorts` on GitHub).

### 5c. ffmpeg helpers — `ffmpeg/clip_generator.py`, `ffmpeg/export_generator.py`, `ffmpeg/ffmpeg_utils.py`
Already-written wrappers around ffmpeg subprocess calls. Probably need extension for "cut a segment by start/end seconds" but the base is there.

### 5d. Publishing via zernio SDK — `publishing/` directory + `zernio-sdk==1.3.68` in requirements
Already integrated. Reuse for publishing clips after they're rendered.

### 5e. DB layer — `db/` directory
Already exists with migrations. Augment, don't replace.

### 5f. CLI — `cli/` directory
Has 4 commands: `add_account`, `captions`, `cleanup`, `post_manual`. Augment with clipping commands (`run`, `client`, `doctor`).

### 5g. Composition rules — `composition/composition_rules.py` (167 lines)
Probably contains caption styling decisions. Read it to understand the existing styling system before adding variants.

---

## 6. What's missing — the build list

Going from "zerino captures + publishes single clips" to "zerino takes long video → clips it → publishes for clients":

### Phase 1 — Clipping engine MVP (4-6 focused days)

| Task | New file(s) | Estimate |
|---|---|---|
| **6.1** Long video ingest — accept URL or local file path | `zerino/sources/` (port from clipper) | 1 day |
| **6.2** Transcribe entire source video → word-level timestamps + segment text | `zerino/clipping/transcribe.py` (reuses `_captions.py:_get_whisper`) | 0.5 day |
| **6.3** Ollama-based moment scoring | `zerino/clipping/score.py` | 1.5 days |
| **6.4** Window detection (combine score windows into clip boundaries) | `zerino/clipping/windows.py` | 0.5 day |
| **6.5** Top-N selector with diversity | `zerino/clipping/selector.py` (port from clipper) | 0.5 day |
| **6.6** Wire to existing vertical+captions processor | `zerino/clipping/pipeline.py` | 1 day |
| **6.7** `zerino clip <locator>` CLI command | `zerino/cli/clip.py` | 0.5 day |
| **6.8** Smoke test end-to-end on a real source | tests | 0.5 day |

**End-of-Phase-1 deliverable:** drop a 30-min talking-head video, get back 5-8 captioned 1080p vertical clips in `storage/clips/<source_id>/`. All local. $0 per clip. **For self-use.**

### Phase 2 — Multi-client productization (2-3 days)

| Task | Files | Estimate |
|---|---|---|
| **6.9** `client` table + `client_id` foreign keys on sources/clips | `zerino/db/migrate.py` | 0.5 day |
| **6.10** `zerino client add/list/remove` commands | `zerino/cli/client.py` | 1 day |
| **6.11** Per-client config: caption style preference, niche, default top-N | `clients/<id>/config.yaml` | 0.5 day |
| **6.12** Output paths: `storage/clients/<id>/clips/<source>/` | `zerino/clipping/pipeline.py` | 0.5 day |

**End-of-Phase-2 deliverable:** `zerino clip <video> --client alice` puts Alice's clips in her own folder with her preferred caption style. Same command for Bob with different settings.

### Phase 3 — Quality + polish (1-2 weeks)

- Active speaker detection + dynamic crop (MediaPipe + YOLOv8 from openshorts patterns)
- More caption styles beyond the current karaoke (port the 8 styles from `guillaumegay13/youtube-to-viral-clips`: Classic, Bold Yellow, Submagic, Minimal, TikTok, Neon, Ultra Bold, Viral Bold)
- LLM scoring quality improvements (better prompts, examples)
- Doctor command extension (check Ollama, check model pulled, check ffmpeg, check faster-whisper)

### Phase 4 — Delivery (1 week)

- Auto-zip output per client
- Optional: auto-upload to client's Dropbox/Google Drive/S3
- Whop campaign tag templates per client

---

## 7. Open-source projects to study (NOT fork) for patterns

All MIT-licensed. Read for patterns, don't carry their architectural baggage:

- **[SamurAIGPT/AI-Youtube-Shorts-Generator](https://github.com/samuraigpt/ai-youtube-shorts-generator)** — LLM highlight detection prompts (study the criteria they use: hooks, emotional peaks, opinion bombs, revelations, conflict, quotables, story peaks, practical value). Their `VIRALITY_CRITERIA` config is worth studying.
- **[mutonby/openshorts](https://github.com/mutonby/openshorts)** — speaker tracking via MediaPipe + YOLOv8. They have TRACK mode and GENERAL mode. Use as reference for Phase 3 dynamic crop.
- **[guillaumegay13/youtube-to-viral-clips](https://github.com/guillaumegay13/youtube-to-viral-clips)** — 8 named caption styles (Classic, Bold Yellow, Submagic, Minimal, TikTok, Neon, Ultra Bold, Viral Bold). Study their `.ass` style definitions for inspiration.

**Skip Adobe Premiere automation.** Wrong tool for headless server-side automation. ExtendScript is dying September 2026 anyway.

---

## 8. Things to NOT redo

We already burned time figuring these out. Don't repeat:

- **Reap field-name bugs**: documented above (§2). Don't re-add Reap as a fallback.
- **Caption stacking from dirty sources**: video sources with burned-in captions in pixels cannot have those captions removed without expensive OCR+inpainting. **The system should refuse or warn on sources that have burned-in captions.** Test: spot-OCR a few frames; if text density is high, warn. (Phase 3+ feature.)
- **The 2-min minimum**: Reap rejected sub-2-min sources sometimes. Our local pipeline has no such limit. Set a sane minimum like 30s.
- **Hardcoded resolution**: zerino's captions module currently hard-codes 1080×1920. Keep that for v1, but parameterize when adding 4K support (PlayResX/Y need to scale with output resolution OR ASS Style font sizes need scaling).
- **Premature variant fan-out**: clipper started with 5 variants per clip → captions allowance burnout. **Stick with ONE caption style per source for v1.** A/B test by rotating styles ACROSS sources, not per clip.

---

## 9. Day-one starter prompt for the next session

When you cd into `~/Desktop/content_business/` and start a new Claude session, paste this:

> "Read CLIPPING_HANDOFF.md (this file). Then read `zerino/processors/_captions.py`, `zerino/processors/vertical.py`, `zerino/ffmpeg/clip_generator.py`, `zerino/cli/captions.py`, `zerino/router.py`, and `zerino/db/migrate.py`. Map what's there to the Phase 1 build list in §6 of the handoff. Then propose where each Phase 1 file lives in the zerino package layout and what their interfaces should be — before writing any code. We need to agree on the module layout before implementing."

Then approve or modify the layout proposal, and start Phase 1 task 6.1.

---

## 10. Specific code files to copy from clipper

These files have working, tested code that ports cleanly into zerino:

### High priority (port these first)
- `~/Desktop/Clipper_for_clients/clipper/sources/base.py` → `zerino/sources/base.py`
- `~/Desktop/Clipper_for_clients/clipper/sources/link_router.py` → `zerino/sources/link_router.py`
- `~/Desktop/Clipper_for_clients/clipper/sources/upload.py` → `zerino/sources/upload.py`
- `~/Desktop/Clipper_for_clients/clipper/scoring/selector.py` → `zerino/clipping/selector.py`
- `~/Desktop/Clipper_for_clients/clipper/db/migrate.py` → augment `zerino/db/migrate.py` with WAL + foreign_keys patterns

### Medium priority (port after Phase 1 ships)
- `~/Desktop/Clipper_for_clients/clipper/cli/doctor.py` → augment `zerino/cli/healthcheck.py` with Ollama + Whisper model checks
- `~/Desktop/Clipper_for_clients/clipper/cli/run.py` directory-batch logic → `zerino/cli/clip.py`
- `~/Desktop/Clipper_for_clients/clipper/reap/client.py` (the retry/backoff patterns, not Reap-specific) → `zerino/_internal/http.py` for Ollama HTTP

### Don't port
- Anything in `clipper/reap/` (engine layer — replaced by Ollama + local processing)
- `clipper/variants/` (the 5-variant fan-out was a wrong direction)
- `clipper/publishing/` (zerino already has working publishing)

---

## 11. Cost model going forward

| Item | Cost |
|---|---|
| Reap subscription | **$0 (canceled)** |
| Per-clip cost | **$0 (local processing)** |
| Compute | Your Mac. Free. faster-whisper `small` model runs on CPU at ~real-time. |
| Ollama LLM scoring | $0 (local) |
| Disk per source | ~500 MB transient (source download + Whisper temp + N output clips at 1080p) |
| Claude during build | ~$300-500 total over 2-3 weeks (estimating 3 hrs/day of focused pair-coding) |
| Ongoing Claude after MVP | $50-100/mo at light use |

**Operational ceiling:** how many videos per month you can process is now bounded by your time and your disk space, not by an external API budget. Realistic throughput on a Mac: 20-30 sources/day at ~30 min each (Whisper + render time).

---

## 12. Risk register going in

Things that could still bite:

1. **Whisper transcription quality varies by audio.** Crowded audio, accents, music = lower accuracy. Mitigations: model size `medium` or `large-v3` for higher accuracy (more CPU); pre-normalize audio with ffmpeg loudnorm filter.
2. **Ollama LLM scoring may not be as good as Reap's trained model** for picking viral moments. Mitigations: refine prompts iteratively; A/B test against hand-picked clips; eventually fall back to Claude/OpenAI API if local quality plateaus.
3. **Center-crop loses speaker** when subject is off-center. Phase 3 fixes with face tracking, but Phase 1 ships with center crop only. Set source guidelines: "subject should be reasonably centered."
4. **4K throughput**. Rendering 4K vertical with libass captions burns CPU. A 1-min 4K clip might take 2-3 min to render on a MacBook Air. Plan for this in Phase 2 throughput estimates.
5. **Disk fills up.** Multiple sources at 4K + transient Whisper artifacts can fill a small SSD fast. Add eviction policy (auto-delete source after clipping; auto-delete clips after publishing+confirmation).

---

## 13. What clipper got right that should NOT be lost

A short list of design decisions worth preserving:

- **`source_id` and `candidate_id` as first-class identities.** Lets us re-run scoring or re-render variants from kept candidates without re-uploading.
- **Denormalized `virality_score` onto every clip row.** Avoids joins when sorting outputs.
- **WAL mode + foreign_keys ON + synchronous=NORMAL** on every DB connect. Concurrency-safe enough for our use.
- **Versioned migrations from #001 with pre-migration snapshot.** Don't skip this.
- **Per-source-batch confirmation prompts** before spending money/budget on a long-running operation.
- **Locator polymorphism**: URL, local file, directory all go through `clipper run`. Same UX for whatever the user has.
- **`clipper doctor` as a single command to verify a workable environment.** Run it before every batch.

---

## 14. Final note for the next session

The clipper repo is **archived in place**. Don't delete it; reference it as the source-of-patterns for the port. The DB at `~/Desktop/Clipper_for_clients/storage/clipper.db` has the run history of all the failed Reap attempts — feel free to nuke or keep as a historical artifact.

The next session starts in `~/Desktop/content_business/`. First action: read this file + the zerino files listed in §9, propose the module layout for Phase 1, then build.

**No more Reap. No more per-clip billing. No more silent 720p caps.**

End of handoff.

---

## 15. Changelog — in-session production-hardening pass (2026-05-09)

Six fixes landed in one session, on Mac, not yet tested on Windows. Hand-off
state for the next session.

### Fix #1 — libass probe cached at startup (root cause of first-clip-no-captions)
- `zerino/processors/_captions.py` — added `prewarm_subtitles_filter()` with
  30 s timeout; `has_subtitles_filter()` now reads a module-level cache.
- `zerino/healthcheck.py` — added `check_libass()` (non-fatal); both
  `run_capture_healthcheck` and `run_scheduler_healthcheck` call it so the
  cache is warm before any clip is processed.
- Why: the per-clip 10 s probe was timing out behind Windows Defender's
  first-invocation scan, silently disabling caption burn for the first clip
  of every session.

### Fix #2 — one accurate-seek re-encode pass; deleted ClipGenerator
- `zerino/processors/_captions.py` — added `extract_audio_slice` and
  `transcribe_source_slice` (audio-only ffmpeg slice for Whisper).
- `zerino/ffmpeg/export_generator.py` — added `run_export_from_source` with
  two-stage seek (`-ss` before `-i` for speed, `-ss` after `-i` for accuracy).
- `zerino/processors/vertical.py` — `process_from_source` method.
- `zerino/router.py` — `route_from_source` method.
- `zerino/publishing/pipeline.py` — `process_and_queue_from_source` function.
- `zerino/publishing/clip_to_posts.py` — `queue_source_cuts_for_posting`.
- `zerino/capture/services/clip_service.py` — refactored to use the new path.
- `zerino/ffmpeg/clip_generator.py` — **DELETED**.
- Why: the old flow was stream-copy cut → re-encode export. The intermediate
  produced wonky-start motion on Windows. Single-pass eliminates the wonky
  edge entirely.

### Fix #3 — ClipJob dataclass + transcript hook
- `zerino/models.py` — **NEW** module: `ClipJob` dataclass.
- Replaced all `_from_source` functions with `_clip_job` variants:
  - `Router.route_clip_job(job, targets=...)`
  - `VerticalProcessor.process_clip_job(job, platform, output_dir)`
  - `process_and_queue_clip_job(job)`
  - `queue_clip_jobs_for_posting(jobs, ...)`
- `ClipJob.transcript_path` field — if set, processor reuses pre-computed
  .ass; if None, transcribe per cut. Hook for a future whole-source
  pre-transcribe step (deferred: per-cut is faster for the F8-marker flow).

### Fix #4 — Square (1080×1080) layout, per-account preference
- `zerino/db/migrate.py` — adds `accounts.layout` column (default 'vertical').
- `zerino/db/init_db.py` — fresh schema includes the column with CHECK constraint.
- `zerino/db/repositories/accounts_repository.py` — `add_account(..., layout=)`,
  `update_account(..., layout=)`, `VALID_LAYOUTS` tuple.
- `zerino/cli/add_account.py` — `--layout {vertical,square}` flag on add+update,
  layout column in list output.
- `zerino/processors/_captions.py` — ASS header / `transcribe_to_ass` /
  `transcribe_source_slice` / `subtitles_filter` all accept `play_res_x` /
  `play_res_y`; `LAYOUT_MARGIN_V` table sets caption MarginV per canvas.
- `zerino/composition/composition_rules.py` — `get_platform_preset`,
  `get_talking_head_template`, `build_processing_config` all take `layout`.
- `zerino/ffmpeg/export_generator.py` — `run_export_from_source(..., layout=)`.
- `zerino/processors/square.py` — **NEW** SquareProcessor.
- `zerino/router.py` — `_processor_for(platform, layout)`; `route_clip_job`
  now takes `targets: list[(platform, layout)]` and dedupes renders.
- `zerino/publishing/pipeline.py` — layout-aware fan-out: query accounts per
  platform, group by layout, render once per (platform, layout) pair, post
  each account using its layout's render.

### Fix #5 — `zerino clip-file <path>` entry point
- `zerino/cli/clip_file.py` — **NEW** CLI. Builds a ClipJob from a file path
  (with optional `--start`, `--end`, `--platforms`, `--caption`), runs the
  same render+post pipeline used by the F8-marker flow.
- `zerino/models.py` — `ClipJob.clip_id` is now `int | None` so ad-hoc
  hand-offs can use clip_id=None (posts.clip_id is nullable).
- No `clips` table row is created for hand-off files — `posts` rows are the
  only persisted trace.

### Operator note — OBS configuration (no code change)
- The Windows source stutter is partly OBS-side: pressing Start Stream AND
  Start Recording forces OBS to run two encoders that contend for CPU.
- Fix in OBS Settings → Output:
  1. Enable **"Automatically record when streaming"** (one button starts both).
  2. Set Recording → **"Use stream encoder"** (one encode, two outputs;
     no contention).
- After that, Fix #2's accurate-seek re-encode handles the cut-edge cleanly
  even on long sources.

### Files NOT touched
- `zerino/capture/services/export_service.py` — orphan path (its worker is
  not started by `capture/main.py`). Left for a future cleanup pass.
- Legacy `process(clip_path)` / `route(input_path)` / `run_export(file)`
  methods remain on processors / router / generator — unused by the active
  flow but kept for now in case a tool depends on them. Safe to delete in
  a follow-up.

### How to verify on Mac before pushing to Windows
1. `python -m zerino.db.migrate` — applies the accounts.layout column.
2. `python -m zerino.healthcheck` — should show "libass subtitles filter OK".
3. Add a square-layout test account:
   `python -m zerino.cli.add_account add --platform tiktok --handle @test_square --zernio-account-id ... --layout square`
4. Hand it a file:
   `python -m zerino.cli.clip_file --file path/to/test.mp4 --start 5 --end 25`
5. Verify in `renders/tiktok/` you see both `..__tiktok.mp4` (1080×1920) AND
   `..__tiktok__square.mp4` (1080×1080), and that Zernio shows the right one
   posted to each account.

