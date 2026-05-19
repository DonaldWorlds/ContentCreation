# Plan — Top-to-bottom fix list for the clipping pipeline

## Context

The user reports that the current clipping pipeline produces output worse than what an earlier version of the project was producing. Specifically: clips are over-compressed, look over-processed (orange skin / crushed shadows / edge halos), the audio is bad (pumpy, breathy, uneven loudness), and **captions sometimes appear in the wrong language entirely** (Korean / Welsh / Maori / etc.) instead of English.

**Framing — quality loss is cumulative.** Most clip systems destroy quality by stacking failures: repeated re-encoding, wrong scaling, bitrate starvation, bad frame extraction, AI overprocessing, platform-side compression, bad OBS settings, bad source ingestion, incorrect color handling, temporal damage. A clip can look fine at every step individually and end up terrible after the whole pipeline runs. This plan was first written as an isolated-bug list; on the user's prompt it has been reframed around the **signal degradation chain** below — so each fix's role in protecting the signal is explicit.

Two independent code-review passes traced every reported symptom to specific, fixable defects. This plan groups every defect by the **pipeline stage where it happens** (capture → render → publish) and ranks each item by perceptual impact:

- **P0** — directly visible/audible in the final clip. Fixing these is what the user will see.
- **P1** — pipeline reliability / robustness. Fixing these prevents silent failure modes.
- **P2** — hygiene / dead-code cleanup. Fixing these prevents future confusion.

---

## Quality degradation chain — where the signal dies

The video and audio bits flow through eight stages. Each stage is named with the **loss category** that happens there and the **fix items** (below) that address it. Where a stage is "OUT OF CODE", the fix is documentation or healthcheck-side guidance — we can't change OBS or TikTok from inside the pipeline, but we can detect bad settings and warn.

```
   [OBS encode]  →  [recording mp4]  →  [capture detect]  →  [Whisper slice]  →  [ffmpeg one-pass: decode → filters → encode]  →  [output mp4]  →  [Zernio upload]  →  [TikTok re-encode]  →  [viewer]
       │                  │                    │                  │                       │                                          │                  │                    │
       │                  │                    │                  │                       │                                          │                  │                    │
   #7 OBS settings   #8 Bad source   (timing only)        #5 AI hallucinate          #2 Wrong scaling                   (no edits)         (no edits)         #6 TikTok recompress
   #3 starvation     ingestion                            (Whisper, not video)       #9 Color mishandle                                                          (we cannot control)
                                                                                     #10 Temporal damage
                                                                                     #1 Re-encode generations
                                                                                     #5 AI overprocess (eq/denoise/unsharp)
                                                                                     #4 Bad frame extraction (pre-roll, fps)
```

**Stage 0 (OUT OF CODE) — OBS encoder.** Source bitrate too low here, EVERY downstream step gets less to work with. *Fix: preflight check in `healthcheck.py` that probes the most recent recording and warns if bitrate < 12 Mbps, GOP > 4 s, or audio < 192 k. Plus a documentation block in `ops/README.md` listing the recommended OBS settings.*

**Stage 1 — Source ingestion.** Today `probe_metadata` only reads width / height / fps / duration. It ignores: color_space, color_primaries, color_range, pix_fmt (8-bit vs 10-bit), audio sample_rate, audio channel layout, audio codec, source bitrate. **If the source is BT.601 we silently mishandle color. If the source is 10-bit HEVC we silently lose dynamic range converting to 8-bit yuv420p. If the source has surround audio we get a default downmix instead of a controlled one.** *Fix: extend `probe_metadata` to capture all of these, then warn or convert downstream.*

**Stage 2 — Capture orchestration.** Mostly timing; no signal touches it.

**Stage 3 — Whisper transcription slice.** Audio is decoded from source AAC → PCM 16 kHz mono → fed to Whisper. **One lossy decode generation here**, then the slice is discarded. Whisper's output is *text* — caption quality lives or dies here (foreign-language bug), not signal quality. *Fix: S4.x set — force English, VAD, kill cascading conditioning.*

**Stage 4 — ffmpeg one-pass re-encode. THIS IS WHERE 80 % OF SIGNAL DEATH HAPPENS.** Every filter in the chain compounds. The chain today is:

```
   decode source video  →  eq=contrast=1.05:saturation=1.1:gamma=0.97  →  crop  →  scale lanczos  →  setsar  →  fps=60  →  subtitles burn  →  encode VideoToolbox @ 15 M ABR
   decode source audio  →  loudnorm I=-14:TP=-1.5:LRA=11               →  afade  →  encode AAC native @ 256 k
```

Every box in that chain is a loss. The eq filter is a tone curve (destroys highlight + shadow data). The crop is lossless. The scale is a 1.78× upscale through a sharp kernel (introduces ringing). The fps=60 on a 30 fps source duplicates frames (wastes bit budget on doubles). The subtitle burn is fine. The encoder at 15 M ABR averages much lower in low-motion shots (= bitrate starvation in disguise). The audio loudnorm in single-pass mode is dynamic compression (destroys microdynamics). The AAC native encoder produces nasal/swooshy artifacts on plosives. **Six independent destruction operations applied to the same content in the same call.** *Fix: every S6.x item below.*

**Stage 5 — Output mp4.** This is the only artifact we own. Quality is now whatever Stage 4 left us with.

**Stage 6 (OUT OF CODE) — Zernio upload + TikTok recompression.** TikTok will re-encode anything over ~10 Mbps to 6–10 Mbps with their VP9 / AV1 + their own color and grain processing. Sending them 15 Mbps wastes bandwidth without improving viewer-side output. **The right strategy is to give them clean source at ~10 Mbps and let them work.** *Fix: S6.9 — bitrate down to 10 M.*

**Stage 7 (OUT OF CODE) — Viewer's device decodes.** Whatever color tags we wrote determine whether playback is tinted. *Fix: S6.8 — bt709 tags.*

**Why this matters: cumulative losses.** The current pipeline takes 8-bit yuv420p source → applies five lossy transformations in series (tone curve + scale + denoise on split + sharpen on split + encode) → quantizes back to 8-bit yuv420p → hands it to TikTok for another re-encode generation. The math is that each step keeps ~95 % of the perceptual signal; five steps × 0.95 = 77 %. The user's "over-processed" complaint is 23 % of the source being thrown away in our own pipeline before TikTok even touches it.

User-confirmed scope decisions:
- Whisper model: stay on `small` (fast, accurate enough once language is forced).
- Capture finish detection: drop stable-time to 10s (matches what the log message claims).
- Orphan code: delete in this PR.
- Color filter: delete entirely.
- Audio: static leveler chain (`highpass → dynaudnorm → alimiter`), not two-pass loudnorm.
- Encoder: prefer `libx264` over VideoToolbox on Mac (NVENC kept for Windows).
- Whisper language: still to be confirmed — defaults below to **hard-code English everywhere** (the fastest path to fixing the foreign-language symptom). Trivial to revisit later if non-English clients arrive.

Reviewer reports archived at:
- [`~/.claude/plans/staged-wandering-aurora-agent-a8a1884481bd09c5e.md`](staged-wandering-aurora-agent-a8a1884481bd09c5e.md) — audio/video filter pass
- [`~/.claude/plans/staged-wandering-aurora-agent-a12b09d1c25326c1e.md`](staged-wandering-aurora-agent-a12b09d1c25326c1e.md) — Whisper + capture pass

---

## Current sub-task — build quality verifier, then execute S0.1

User-confirmed execution order: **stage-by-stage from S0.1**, with one piece of new tooling built first so every later fix lands with measured / inspectable evidence instead of guessing.

### Step A — `zerino/cli/quality_verify.py` (NEW FILE)

A CLI that takes a rendered mp4 and produces a `quality_report/<clip_stem>/` directory the assistant can read to judge whether a fix landed. Without this, fixes in Stages 4 and 6 (Whisper output + visible video changes) can only be code-reviewed, not output-verified.

**Why this is the "make Claude smarter" answer.** The assistant can already read PNGs and JSON; it can't watch video or hear audio. This script converts an mp4 into artifacts the assistant *can* read, so the verification loop tightens from "user previews it and reports back" to "assistant inspects the artifacts and reports findings."

**Outputs in `quality_report/<clip_stem>/`:**

- `frame_10pct.png`, `frame_40pct.png`, `frame_70pct.png`, `frame_95pct.png` — keyframes at 10/40/70/95 % of duration. PNG so the assistant can read them. Used to judge: skin tone, framing, halos, banding, captions burnt at correct position.
- `spectrogram.png` — full-clip audio spectrogram via `ffmpeg -lavfi showspectrumpic=s=1920x540:legend=1`. Used to judge: loudness pumping (visible horizontal banding), compression artifacts, silence handling.
- `loudness.json` — ebur128 integrated loudness, true peak, LRA. Numbers for "is the audio level steady" vs "is loudnorm pumping."
- `ffprobe.json` — full ffprobe `-show_streams -show_format` output. Source of truth for codec, bitrate, color tags, pix_fmt, audio params.
- `summary.md` — one-page assistant-readable report: bitrate, fps, codec, color tags, loudness numbers, file size, notes from the keyframes (filled in by the assistant on inspection, not auto-generated).
- *(optional)* `compare/frame_*pct.png` — when `--reference <path>` is given, side-by-side via `hstack=inputs=2`. Used for regression checks: "does the new render look right next to a known-good one."

**Implementation shape (subprocess wrapper around ffmpeg / ffprobe — no new Python deps):**

```python
# zerino/cli/quality_verify.py
# Usage:
#   python -m zerino.cli.quality_verify <input.mp4> [--reference <ref.mp4>] [--out-dir quality_report/]
#
# All work is ffmpeg / ffprobe subprocess calls. The script is intentionally
# thin — it's a helper for the assistant + human reviewer, not part of the
# render path. No imports from zerino's render code; can be deleted later
# without consequence.
```

**Reuse existing utilities:**
- `_run_ffprobe()` from [`zerino/ffmpeg/ffmpeg_utils.py:12-23`](zerino/ffmpeg/ffmpeg_utils.py#L12) for the ffprobe dump.
- `get_logger()` from [`zerino/config.py:22`](zerino/config.py#L22) for consistent logging.

**No changes to existing files** for Step A — it's purely additive.

**Verification of Step A:**
- Run against an existing render in `renders/tiktok/` or `caption_tests/`. Expect a populated `quality_report/<clip>/` directory.
- Open each generated PNG manually (the user); confirm frames are at sensible timestamps and the spectrogram is readable.
- Assistant reads the summary.md + ffprobe.json + one keyframe PNG to confirm the artifact pipeline works end-to-end on a known-good clip.

### Step B — S0.1: extend `probe_metadata`

Per plan section "STAGE 0 — Source ingestion." Mechanical change in [`zerino/ffmpeg/ffmpeg_utils.py:57-94`](zerino/ffmpeg/ffmpeg_utils.py#L57). Today returns 4 fields; after this step returns ~12.

**New keys returned (all may be `None` if ffprobe doesn't report them):**

```python
return {
    # existing
    "width": int, "height": int, "fps": float, "duration": float | None,
    # new — video color
    "pix_fmt": str | None,              # e.g. "yuv420p", "yuv420p10le"
    "color_space": str | None,          # e.g. "bt709", "bt601"
    "color_primaries": str | None,      # e.g. "bt709"
    "color_transfer": str | None,       # e.g. "bt709"
    "color_range": str | None,          # e.g. "tv", "pc"
    # new — video bitrate (for S9.1 OBS preflight)
    "video_bit_rate": int | None,       # bps, from stream or format
    # new — audio
    "audio_codec": str | None,          # e.g. "aac"
    "audio_sample_rate": int | None,    # Hz, e.g. 48000
    "audio_channels": int | None,       # 1 / 2 / 6
    "audio_channel_layout": str | None, # e.g. "stereo", "5.1"
    "audio_bit_rate": int | None,       # bps
}
```

**Implementation:**
- Add an audio-stream lookup parallel to the existing video-stream `next(...)` pattern at [`ffmpeg_utils.py:67-70`](zerino/ffmpeg/ffmpeg_utils.py#L67).
- All new fields use `.get(...)` with `None` default — ffprobe doesn't always report every field on every file.
- `video_bit_rate` falls back to `data["format"].get("bit_rate")` when the stream-level field is absent (common for OBS recordings).
- Keep the existing fields (`width`, `height`, `fps`, `duration`) at the front of the return dict so callers that access them positionally / by name don't break.

**Callers that might need updating (read-only check first):**
- [`zerino/ffmpeg/export_generator.py:258, 435`](zerino/ffmpeg/export_generator.py#L258) — `probe_metadata(source_path)` and `probe_metadata(input_path)`. Today they only read `width`, `height`, `fps`, `duration`. Adding extra keys is backward-compatible; no caller changes needed in this step.
- [`zerino/cli/clip_file.py:94`](zerino/cli/clip_file.py#L94) — uses `get_video_duration_seconds`, not `probe_metadata`. Unaffected.

**Do not** plumb the new fields into the filter chain in Step B — that's S0.2 / S6.7 / S6.8 / S6.17 territory, sequenced later in the plan. Step B just exposes the data.

**Verification of Step B:**
- Add a `__main__` block at the bottom of `ffmpeg_utils.py` that prints `probe_metadata` JSON for a path argument — usable as `python -m zerino.ffmpeg.ffmpeg_utils <path>`.
- Run on at least one source recording (drop a sample in `recordings/`) and one rendered output (`renders/tiktok/*.mp4`). Expect all new keys populated for both.
- Run `python -m zerino.cli.quality_verify <renders/...>` from Step A on the same file — the ffprobe.json should contain the same raw data the new probe_metadata is parsing.
- Confirm existing callers still work: `python -m zerino.healthcheck` should pass; a `python -m zerino.cli.clip_file` smoke test (if a source is available) should succeed.

### Out of scope for THIS sub-task (deferred)

- S0.2 — filter-chain reorg to consume the new color/audio fields. Comes after S0.1 because it depends on knowing what `probe_metadata` will return.
- All Stage 1, 2, 3 fixes — separate sub-tasks.
- Any S6.x fix — deferred until the verifier is proven on a known-good clip.
- Re-rendering existing clips through the new pipeline — happens stage-by-stage as each fix lands.

### Sub-task verification (both steps together)

After Step A and Step B land, before moving to S0.2:

1. `python -m zerino.healthcheck` — passes, no regression.
2. `python -m zerino.ffmpeg.ffmpeg_utils renders/tiktok/<some>.mp4` — prints the extended metadata dict; all new fields populated.
3. `python -m zerino.cli.quality_verify renders/tiktok/<some>.mp4` — produces `quality_report/<stem>/` with all six artifacts.
4. Assistant reads `quality_report/<stem>/frame_40pct.png` and `summary.md` and reports back what it sees — confirms the visual-inspection loop works.
5. No edits to `export_generator.py`, `_captions.py`, or any processor file. (The filter chain is still the broken one; that's intentional — fixes start next sub-task.)

---

## Operating principles — the doctrine this plan enforces

Quality loss is cumulative. A clip can look "fine" at every step individually and become terrible after the entire pipeline runs. The pipeline today violates several principles that experienced video engineers treat as non-negotiable. This plan re-enforces them.

1. **SOURCE PRESERVATION FIRST.** Do not assume an AI / filter step can "fix it later" — once detail is destroyed by a quantizer or a sharp scaler it does not come back. Every filter in the chain is a one-way decision. The pipeline's job is to *preserve* what the source captured, then add only what cannot be avoided (the crop, the captions, the codec).

2. **EACH FILTER IS A LOSS — MINIMIZE THE CHAIN.** Five lossy transformations in series keep ~77 % of the perceptual signal (0.95⁵). The split path's `crop → denoise → scale → eq → unsharp → setsar` chain is exactly this anti-pattern. The fix is *fewer filters*, not better filters. Default to passing the source through cleanly.

3. **SHARPEN LAST, IF AT ALL.** The correct order is `denoise → upscale → stabilize → color → sharpen`. Today's split chain runs sharpen *immediately after* the upscale (sharpens scaling artifacts) and before the color filter (sharpens artifacts then amplifies them with the contrast bump). The fix is to drop sharpen entirely on a 1.78× upscale — the scaler already produces enough edge definition.

4. **CONTENT-AWARE WHERE POSSIBLE.** Talking-head, gameplay, and IRL motion have different optimal bitrate / denoise / sharpen settings. The pipeline today applies the same chain to all three. Switching to CRF mode (S6.16) is half the answer; per-content tuning is the rest, deferred to Phase 2.

5. **COMPRESSION RESILIENCE FOR THE PLATFORM.** TikTok / YT Shorts / Reels will re-encode anything we send. Clean edges, controlled grain, stable motion, and proper color tags survive that re-encode better than over-sharpened high-bitrate input. Sending 15 Mbps to a platform that caps at ~10 Mbps wastes bandwidth without buying viewer-side quality.

6. **DON'T HALLUCINATE WHAT THE SOURCE DIDN'T CAPTURE.** AI models (Real-ESRGAN, Topaz, frame interpolators) invent detail that wasn't there. The user's brain detects fake skin texture and waxy faces instantly. The fix is to never enhance unless the source is genuinely insufficient (low-light IRL, blurry webcam, old VOD) — and even then, *temporal* models only, never single-frame.

7. **MATCH THE SOURCE — DON'T UPSAMPLE WITHOUT REASON.** Forcing `fps=60` on a 30 fps source duplicates frames; running `eq` on a BT.601 source as if it were BT.709 produces gamma errors; upscaling 720p → 1080p with a sharp kernel adds ringing. When in doubt, do nothing — let the source through.

8. **PROBE BEFORE PROCESSING.** Today `probe_metadata` returns 4 fields; the pipeline silently misbehaves on the 6 it ignores. Every decision in the filter chain needs to be conditioned on what the source actually is, not on what we assume.

These map onto a cross-reference table below — each of the 10 cumulative-loss categories the user identified ties to one or more Sx.y fix items.

---

## Cross-reference — cumulative loss categories → fix items

| # | Loss category | Where it bites our pipeline | Addressed by |
|---|---|---|---|
| 1 | **Generational loss** | One-pass design already minimizes this. Remaining damage is the 5-filter chain inside the one pass. | S6.4 (drop eq), S6.5 (drop unsharp, soften hqdn3d), S6.16 (CRF), S6.15 (scaler), S0.2 (start-of-chain format normalization) |
| 2 | **Bad source quality** | We can't fix a starved OBS stream. Detect and warn. | S9.1 (preflight healthcheck), S9.2 (OBS doc), S0.1 (extended probe) |
| 3 | **Wrong bitrate** | ABR=15M starves face-cam frames to ~4 Mbps in practice. | S6.16 (CRF=18 with cap), S6.9 (drop overall target) |
| 4 | **Upscaling the wrong way** | Lanczos at 1.78× rings on edges. | S6.15 (bicubic on upscale, lanczos on downscale) |
| 5 | **Too many filters** | eq + hqdn3d + lanczos + eq + unsharp in series on split. | S6.4 (delete eq), S6.5 (drop unsharp, soften denoise) |
| 6 | **Frame interpolation damage** | `fps=60` on 30 fps source = duplicated frames, wasted bit budget. | S6.7 (match source fps) |
| 7 | **Wrong color pipeline** | No bt709 tags. No detection of BT.601 source. Eq runs on un-normalized luma. | S6.8 (write color tags), S0.1 (read source color), S6.17 (convert when source ≠ BT.709) |
| 8 | **Social platform recompression** | We send 15 Mbps; TikTok caps ~10. | S6.9 (drop to 10/13/20), S6.16 (CRF + maxrate cap), and the principles above (clean edges, controlled grain) |
| 9 | **AI enhancement overkill** | Unsharp on the face quadrant + hqdn3d at 1.5/6 = plastic skin + halos. | S6.5 (drop unsharp, soften hqdn3d), S6.4 (drop eq amplifier) |
| 10 | **Bad cropping for shorts** | Talking-head intends golden_zone crop but the code path is dead — every clip is dead-center. | **S6.18 (new — wire or delete golden_zone)**. Long-term: face-tracked dynamic crop (Phase 2 / handoff doc §6.3) |

Items 1–10 are all addressed by P0 / P1 fixes in this PR. Items in **Phase 2** (out of this PR's scope): face-tracked dynamic crop, content-aware per-shot encoding, mezzanine-format intermediates if the pipeline ever grows past one-pass, and temporal AI models if quality plateaus with the conservative chain.

---

## STAGE 0 — Source ingestion (the upstream bits we work from)

### S0.1 [P0] `probe_metadata` is half-blind to the source — fix what we don't know about silently destroys quality

[`zerino/ffmpeg/ffmpeg_utils.py:57-94`](zerino/ffmpeg/ffmpeg_utils.py#L57) reads only width / height / fps / duration. It does **not** read:

- `color_space`, `color_primaries`, `color_transfer`, `color_range` — needed to know whether source is BT.709 (most common) or BT.601 (some legacy capture cards / old OBS configs). If BT.601 and we don't convert, viewer-side playback is green-shifted.
- `pix_fmt` — needed to know whether source is 8-bit yuv420p (assumed) or 10-bit (HEVC OBS) — silent precision loss in the encode if we don't tell ffmpeg explicitly.
- Audio: `sample_rate`, `channel_layout`, `codec_name`, `bit_rate`. Probe of an existing render showed audio at 96 kHz — likely OBS recorded at 96 k, our pipeline ran loudnorm at 96 k (wasted CPU), then encoded back to AAC at 96 k (TikTok will downsample to 48 k anyway). Surround audio gets defaulted-downmixed in unpredictable ways.
- Video `bit_rate` — needed by S9.1 (OBS preflight warning) to detect a starved source.

**Fix:** Extend `probe_metadata` to return all of the above. Then:
- If `color_space != "bt709"` or `color_primaries != "bt709"`, log a WARN and apply a `colorspace=all=bt709:iall=bt601:ispace=bt601` filter at the start of the video chain to convert (not just tag).
- If `pix_fmt == "yuv420p10le"` or other 10-bit, log a WARN and let ffmpeg convert with explicit `format=yuv420p`.
- If audio `channel_layout != "stereo"`, apply a controlled downmix (`-ac 2` and a proper `pan=stereo|c0=...c1=...` filter for surround sources) instead of letting ffmpeg guess.
- If audio `sample_rate != 48000`, resample to 48 kHz before loudnorm to save CPU and match output.
- Persist source `bit_rate` on the ClipJob metadata so S9.1's healthcheck can read it.

### S0.2 [P1] Source resampling and color conversion at the START of the filter chain

Closely related to S0.1. Once we know the source format, the FIRST steps in the filter chain should be format-normalization, not creative filters. Today the chain goes `eq → crop → scale` — if the source is BT.601 the eq is applied to BT.601-encoded luma which has a different gamma curve than BT.709, so the contrast bump means slightly different things on different sources.

**Fix:** Reorganize `build_filter` to always start with `format=yuv420p,colorspace=...` (when source isn't already bt709), then proceed with the creative chain. Audio chain: start with `aresample=48000` (if needed), then the leveler.

---

## STAGE 1 — Capture: F8 / F9 hotkey → marker row

### S1.1 [P0] Marker timestamps lose sub-second precision

[`zerino/capture/services/marker_service.py:38`](zerino/capture/services/marker_service.py#L38) uses `int(time.time() - start_time)`. F8 pressed at 12.7s into a recording produces marker.timestamp=12. The clip window then runs 2s–32s instead of 2.7s–32.7s — **viewers see content the streamer didn't intend, miss the actual reaction moment by up to a second.**

**Fix:**
- Drop `int(...)` — store as float seconds.
- [`zerino/db/init_db.py:41, 58-59`](zerino/db/init_db.py#L41) — change `markers.timestamp` and `clips.clip_start`/`clip_end` columns from `INTEGER` to `REAL`. Add a migration in [`zerino/db/migrate.py`](zerino/db/migrate.py) that does the `ALTER TABLE ... ADD COLUMN ... REAL` pattern (existing rows keep integer values, new rows get float precision).
- [`zerino/capture/services/clip_service.py:33-34`](zerino/capture/services/clip_service.py#L33) — already uses `max(0, marker_time - PRE_BUFFER)`; that's fine as long as marker_time is float.

### S1.2 [P1] `markers_temp` flush is dead code

[`zerino/capture/services/marker_service.py:18-49`](zerino/capture/services/marker_service.py#L18) — `create_marker()` early-returns (line 24) when there is no `current_recording`, so the `state.setdefault("markers_temp", []).append(timestamp)` at line 48 is unreachable. The flush loop in [`zerino/capture/services/recording_service.py:127-136`](zerino/capture/services/recording_service.py#L127) is flushing a queue that can never be populated.

**Fix:** Either delete the dead branch *or* restructure `create_marker` so it queues to `markers_temp` (without a recording_id) when no recording is active, and the flush at recording start writes them to the DB. The "or" path is mildly useful (early-arriving F8 presses survive); the simpler path is to delete.

### S1.3 [P1] Hotkey listener catches and swallows all exceptions

[`zerino/capture/workers/marker_worker.py:47-52`](zerino/capture/workers/marker_worker.py#L47) wraps `marker_service.create_marker(...)` in `except Exception`. Fine as a safety net, but the listener thread has no health check — if `pynput` ever silently stops delivering hotkey events (a known macOS Accessibility regression on permission changes), the user wouldn't know. Not blocking; mention in the plan as a future safety follow-up.

---

## STAGE 2 — Recording finish detection: file stable → trigger clip flow

### S2.1 [P0] Recordings under 10 MB never finish

[`zerino/capture/services/recording_service.py:165-168`](zerino/capture/services/recording_service.py#L165):

```python
if current_size < 10 * 1024 * 1024:
    recording["stable_count"] = 0
    recording["last_size"] = current_size
    return False
```

This branches **before** the size-stability counter increments. Recordings that stop under 10 MB (test recordings, short streams, accidental short stops) reset their stability counter on every poll and never trigger `finish_recording`. The user-visible symptom: "the system didn't render my clip" — but it's not the renderer, the recording was never even forwarded to the queue.

**Fix:** Remove the size gate entirely, or apply it only during the first ~5 seconds (use a `started_at` field in the recording dict).

### S2.2 [P0] "Stable for 10s" lies — code waits 30s

[`zerino/capture/services/recording_service.py:178-180`](zerino/capture/services/recording_service.py#L178): `stable_count >= 60` with `time.sleep(0.5)` between checks = **30 seconds** of size-stable file before considering the recording finished. The log message at line 179 says `🛑 Recording stopped (stable for 10s)`. Both are wrong as a pair, and 30s is excessive — when the user ends a stream they want their clip rendering started within ~10s, not half a minute.

**Fix:** Drop `stable_count >= 60` → `stable_count >= 20` (10s at 0.5s polls). Fix the log string.

### S2.3 [P1] Emoji prints will crash Windows cp1252

[`zerino/capture/services/recording_service.py:172, 175, 179, 206`](zerino/capture/services/recording_service.py#L172) — `📈 Growing`, `⏸️ Stable`, `🛑 Recording stopped`, `📦 Queued recording for pipeline:`. Commit `dd117cc` was specifically about stripping unicode from runtime log/print strings to fix a Windows cp1252 crash — this file was missed.

**Fix:** Replace each emoji with an ASCII token in brackets (e.g. `[grow]`, `[stable]`, `[end]`, `[queue]`).

### S2.4 [P2] `startup_scan_handler.py` has its own orphan `main()`

[`zerino/capture/handlers/startup_scan_handler.py:70-98`](zerino/capture/handlers/startup_scan_handler.py#L70) has a fully-formed daemon `main()` that competes with [`zerino/capture/main.py`](zerino/capture/main.py) — different `observer.schedule(handler, path="recordings", ...)` than the canonical one. Confusing dead code; nothing imports `startup_scan_handler.main`. Delete the `main()` block; keep the `StartupScanHandler` class only if `capture/main.py` actually starts it (it doesn't today, so consider deleting the whole file as part of S11).

---

## STAGE 3 — Clip window math

### S3.1 [P1] Clip near start of recording is shorter than configured

[`zerino/capture/services/clip_service.py:33-34`](zerino/capture/services/clip_service.py#L33):

```python
start = max(0, marker_time - self.PRE_BUFFER)
end = marker_time + (self.CLIP_DURATION - self.PRE_BUFFER)
```

If `marker_time=5` and `PRE_BUFFER=10`, the clamped `start=0` and `end=15` — only a 15s clip, not the intended 30. Sample math from the review showed 25s, which assumed CLIP_DURATION=30; verifying with current constants: CLIP_DURATION=30, PRE_BUFFER=10 → end=5+20=25 ✓. Either way the clip is short.

**Fix:** Anchor `end` off the clamped `start`:
```python
start = max(0.0, marker_time - self.PRE_BUFFER)
end = start + self.CLIP_DURATION
```

### S3.2 [P2] `ClipJob.metadata` shared mutation across fan-out

[`zerino/models.py:58`](zerino/models.py#L58) declares `metadata: dict[str, Any] = field(default_factory=dict)`. Router writes `karaoke_segments` into it at [`zerino/router.py:159`](zerino/router.py#L159) and processors read it. Today only one Router runs per job so this is safe, but the dict is now shared mutable state across every target. Document as read-only-after-Router or pass `.copy()` per target. Low priority; flag for future-proofing.

---

## STAGE 4 — Whisper transcription (the foreign-language root cause)

### S4.1 [P0] `language=None` → Whisper auto-detect → Korean / Welsh / Maori captions

The single biggest defect in the entire pipeline. Three call sites all rely on Whisper's auto-detect:

- [`zerino/processors/_captions.py:288-313`](zerino/processors/_captions.py#L288) — `transcribe_source_to_segments(..., language=None)`, called from [`zerino/router.py:155-159`](zerino/router.py#L155).
- [`zerino/processors/_captions.py:397-442`](zerino/processors/_captions.py#L397) — `transcribe_to_ass(...)`, **does not even accept a `language` parameter**. Called from [`zerino/processors/vertical.py:57`](zerino/processors/vertical.py#L57) (legacy `process()` path) and as a downstream of `transcribe_source_slice`.
- [`zerino/processors/_captions.py:367-394`](zerino/processors/_captions.py#L367) — `transcribe_source_slice(...)` calls `transcribe_to_ass` without a language argument. Called from all three processors (`vertical.py:134`, `square.py:88`, `split.py:120`) as the fallback when Router-cached segments aren't available.

Whisper auto-detect on a 10-30 second slice of gameplay audio + voice + music is **famously** unreliable. The well-documented LID failure modes include Welsh, Maori, Hawaiian, Vietnamese, and Korean. Once the model picks the wrong language, every word in that segment is transcribed in that language's script.

The docstring at [`_captions.py:302-306`](zerino/processors/_captions.py#L302) claims auto-detect happens "per segment" — **factually wrong**; faster-whisper detects once on the opening audio and applies the result to the whole transcription.

**Fix (concrete changes):**

1. Default `language="en"` in `transcribe_source_to_segments` and in `_transcribe_audio_to_segments` ([`_captions.py:288, 316`](zerino/processors/_captions.py#L288)).
2. Add a `language: str | None = "en"` parameter to `transcribe_to_ass` and to `transcribe_source_slice`; pass it through to `model.transcribe(..., language=...)` at [`_captions.py:415-419`](zerino/processors/_captions.py#L415).
3. Update the three processor call sites (`vertical.py:134`, `square.py:88`, `split.py:120`) to pass `language="en"` to `transcribe_source_slice`.
4. Update [`zerino/router.py:155-159`](zerino/router.py#L155) to pass `language="en"` to `transcribe_source_to_segments`.
5. Fix the docstring claim at [`_captions.py:302-306`](zerino/processors/_captions.py#L302).

### S4.2 [P0] `condition_on_previous_text=True` (default) cascades language errors

If S4.1 isn't fully sealed (e.g. someone forgets `language="en"` at a future call site), the next-best protection is to stop Whisper from anchoring future segments to the language of earlier segments. faster-whisper defaults `condition_on_previous_text=True`; for 30-second standalone clips there is no narrative continuity to preserve.

**Fix:** Pass `condition_on_previous_text=False` in both transcription call sites in [`_captions.py:323-328, 415-419`](zerino/processors/_captions.py#L323).

### S4.3 [P0] No VAD — music, silence, breath transcribed as speech

Whisper without VAD will produce hallucinations on non-speech audio. The classic hallucinations are `♪ thank you for watching ♪`, single ASCII glyphs, and (sometimes) random foreign-language phrases. faster-whisper ships `vad_filter=True` as an option but the code doesn't enable it.

**Fix:** Pass `vad_filter=True, vad_parameters={"min_silence_duration_ms": 500}` in both transcription call sites in [`_captions.py:323-328, 415-419`](zerino/processors/_captions.py#L323). The VAD model is bundled with faster-whisper; no extra download.

### S4.4 [P1] No language guard on returned segments

Belt-and-suspenders: even with all three above changes, log + drop if `info.language != "en"` after `model.transcribe(...)`. This catches future regressions instead of silently shipping foreign-script captions.

**Fix:** After the segment iteration in `_transcribe_audio_to_segments` ([`_captions.py:329-339`](zerino/processors/_captions.py#L329)):
```python
if info.language != "en":
    _log.warning("Whisper detected non-English (lang=%s) on %s — dropping segments",
                 info.language, audio_path.name)
    return []
```
Same guard in `transcribe_to_ass` at [`_captions.py:421-441`](zerino/processors/_captions.py#L421).

### S4.5 [P1] No language guard on segment-text fallback

[`_captions.py:332-333, 426`](zerino/processors/_captions.py#L332) — when Whisper returns a segment without word timestamps (happens on very short or borderline segments), the code falls back to using `segment.text.strip()` directly. If that text was hallucinated in another language, the .ass file gets foreign characters with no further check.

**Fix:** S4.4's `info.language != "en"` check at the function entry covers this once it lands.

### S4.6 [P1] `_get_whisper()` is not thread-safe

[`zerino/processors/_captions.py:32, 157-182`](zerino/processors/_captions.py#L32) — `_whisper_model` is a module-level global, initialized lazily in `_get_whisper()` with a simple `if _whisper_model is None` guard. Two threads racing through that branch will each construct a `WhisperModel` (each ~500 MB resident). Today's daemons are effectively single-threaded for transcription, but the scheduler runner could add a worker pool tomorrow.

**Fix:** Wrap with a `threading.Lock()`:
```python
_whisper_lock = threading.Lock()

def _get_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            # ... existing init ...
    return _whisper_model
```

### S4.7 [P2] `beam_size=5` is overkill on 30s clips

[`_captions.py:325, 417`](zerino/processors/_captions.py#L325) — beam=5 is ~3–5× slower than greedy with marginal accuracy gain on short standalone windows. Drop to `beam_size=1`.

### S4.8 [P2] Temp slice path collision risk

[`_captions.py:308`](zerino/processors/_captions.py#L308) — `f".__transcribe_slice_{int(start)}_{int(end)}.wav"` collides if two jobs share an integer second range. Add `uuid.uuid4().hex[:8]` to the filename.

### S4.9 [P2] Docstring lies fixed

Already covered in S4.1 — update [`_captions.py:302-306, 322-323, 397-413`](zerino/processors/_captions.py#L302) to remove the "auto-detect per segment" claim and explain the new English-default behavior.

---

## STAGE 5 — ASS caption file generation

### S5.1 [P2] Docstring says "2-word chunks", code does 3

[`_captions.py:7`](zerino/processors/_captions.py#L7) vs [`_captions.py:23`](zerino/processors/_captions.py#L23) — `WORDS_PER_CHUNK = 3`. Update the docstring. Trivial.

### S5.2 [P2] ffmpeg subtitles filter path escape misses chars

[`_captions.py:501-506`](zerino/processors/_captions.py#L501) — escapes `\`, `:`, `,`, `'` but not `]`, `;`, `[`. Brittle on usernames or stream titles that contain those chars (unusual but possible in file path components).

**Fix:** Add the missing escapes; or use Python's `shlex.quote`-equivalent for ffmpeg filter args (there isn't a stdlib helper, so manual escapes).

---

## STAGE 6 — ffmpeg one-pass render (the over-processed bugs)

All changes in this section land in [`zerino/ffmpeg/export_generator.py`](zerino/ffmpeg/export_generator.py).

### S6.1 [P0] Single-pass `loudnorm` pumps speech

[`zerino/ffmpeg/export_generator.py:17, 212-219`](zerino/ffmpeg/export_generator.py#L17) — `loudnorm=I=-14:TP=-1.5:LRA=11` in single-pass mode is a dynamic envelope follower. The I/TP/LRA targets are chased, not achieved; the actual behavior is per-window compression that pumps on speech onsets and breathes through quiet passages. This is **the** "audio is ass" cause.

**Fix:** Replace `LOUDNORM_FILTER` constant with:
```python
SPEECH_LEVELER = "highpass=f=80,dynaudnorm=f=200:g=15:p=0.7,alimiter=limit=0.95"
```

### S6.2 [P0] Audio fade ordering produces double-onset

[`zerino/ffmpeg/export_generator.py:212-219`](zerino/ffmpeg/export_generator.py#L212) — `loudnorm,afade=t=in:st=0:d=0.15` runs the fade AFTER the normalizer. Once S6.1 lands the issue partially goes away (dynaudnorm is less stateful), but order still matters.

**Fix:** Rewrite `build_audio_filter`:
```python
def build_audio_filter(self, duration: float | None) -> str:
    parts = ["afade=t=in:st=0:d=0.05", SPEECH_LEVELER]
    if duration is not None and duration > AUDIO_FADE_OUT_SEC + 0.1:
        fade_out_st = max(0.0, duration - AUDIO_FADE_OUT_SEC)
        parts.append(f"afade=t=out:st={fade_out_st:.3f}:d={AUDIO_FADE_OUT_SEC}")
    return ",".join(parts)
```
Drop the `AUDIO_FADE_IN_SEC` constant.

### S6.3 [P0] Native AAC encoder is the worst of the four

[`zerino/ffmpeg/export_generator.py:291, 415, 459`](zerino/ffmpeg/export_generator.py#L291) — `-c:a aac` resolves to ffmpeg's built-in encoder, well-documented as producing nasal/swooshy artifacts on plosives and sibilants. On macOS, `aac_at` (AudioToolbox) ships in every macOS ffmpeg build and is materially cleaner.

**Fix:** Add platform-aware audio encoder pick, parallel to the existing video encoder pick:
```python
def _pick_audio_encoder() -> tuple[str, list[str]]:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, timeout=10,
    ).stdout or ""
    if "aac_at" in out:
        return ("aac_at", ["-q:a", "9"])  # VBR
    return ("aac", ["-b:a", AUDIO_BITRATE])

AUDIO_ENCODER, AUDIO_ENCODER_ARGS = _pick_audio_encoder()
```
Replace `"-c:a", "aac", "-b:a", AUDIO_BITRATE` with `"-c:a", AUDIO_ENCODER, *AUDIO_ENCODER_ARGS` in all three command lists.

### S6.4 [P0] `eq=contrast=1.05:saturation=1.1:gamma=0.97` over-processes every clip

[`zerino/ffmpeg/export_generator.py:13, 188, 196, 203, 360, 368`](zerino/ffmpeg/export_generator.py#L13) — applied universally. `gamma=0.97` crushes shadows that OBS already crushed. `saturation=1.1` pushes skin orange. `contrast=1.05` pre-emphasizes what TikTok then re-emphasizes during platform-side re-encode.

**Fix:** Delete `COLOR_FILTER` and remove the `{COLOR_FILTER},` prefix from all six locations in `build_filter` (crop/pad/fallback branches) and in `run_split_export_from_source` (face_chain/game_chain).

### S6.5 [P0] Split chain stacks 5 perceptual filters → plastic skin + halos

[`zerino/ffmpeg/export_generator.py:55, 62, 355-363, 73-82`](zerino/ffmpeg/export_generator.py#L55) — face chain is `crop → hqdn3d=1.5:1.5:6:6 → scale lanczos → crop → eq → unsharp=5:5:0.5:5:5:0.0 → setsar`. The temporal denoise smooths skin across frames (Snapchat look); the lanczos upscale at 1.78× reintroduces edge ringing; `eq` amplifies the ringing's contrast; `unsharp` then sharpens those amplified ring edges into visible halos. Then `-tune film` tells x264 to *preserve* the grain that hqdn3d just removed (the two settings fight).

**Fix:**
- Drop `SPLIT_FACE_UNSHARP` constant. Remove the `{SPLIT_FACE_UNSHARP}` term from `face_chain`.
- Soften denoise: `SPLIT_FACE_DENOISE = "hqdn3d=1.0:1.0:3:3"`.
- Drop `-tune film` from `SPLIT_VIDEO_ENCODER_ARGS` — keep `-preset slow`, `-profile:v high`, `-level 4.2`, and the `-x264-params` AQ tuning.
- The `eq` removal from S6.4 already deletes the contrast amplifier.

### S6.6 [P0] VideoToolbox auto-pick on Mac is muddier than libx264

[`zerino/ffmpeg/export_generator.py:117-149`](zerino/ffmpeg/export_generator.py#L117) — `h264_videotoolbox` produces visible banding on dark gradients + smearing on skin compared to `libx264 -preset slow` at the same bitrate. On Apple Silicon, libx264-slow runs at 4–6× realtime for 30s clips — the speed argument doesn't apply for short-form. The split path already overrides to libx264 for this exact reason; the inline comment claiming "imperceptible at 15 Mbps" is wrong.

**Fix:** Rewrite `_pick_video_encoder`:
```python
def _pick_video_encoder() -> tuple[str, list[str]]:
    available = _probe_available_encoders()
    if "h264_nvenc" in available:
        return ("h264_nvenc", [
            "-preset", "p5", "-tune", "hq", "-rc", "vbr",
            "-multipass", "fullres", "-spatial-aq", "1",
        ])
    return ("libx264", ["-preset", ENCODE_PRESET])
```
Removes the VideoToolbox branch entirely.

### S6.7 [P1] `fps=60` forced — duplicates frames on 30 fps OBS

[`zerino/ffmpeg/export_generator.py:162, 191, 199, 205, 345, 362, 369`](zerino/ffmpeg/export_generator.py#L162) — `fps={fps}` is hard-coded into every filter chain with `fps=60` default. OBS often records at 30 fps; that gets upsampled by frame duplication. Half the bit budget is then spent on duplicate frames instead of motion.

**Fix:** In `build_filter` and in `run_split_export_from_source`:
```python
source_fps = float(metadata.get("fps") or 30.0)
target_fps = min(60.0, source_fps)
fps_clause = f",fps={target_fps:g}" if abs(source_fps - target_fps) > 0.1 else ""
```
Apply `fps_clause` only when needed.

### S6.8 [P1] No bt709 color tags on output

[`zerino/ffmpeg/export_generator.py:290, 414, 458`](zerino/ffmpeg/export_generator.py#L290) — `-pix_fmt yuv420p` is forced but no `-color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv` written. Some Android/web players guess BT.601 and tint playback green. The May-5 sample renders in `caption_tests/` happen to have these tags because of an earlier code path; the current code does not write them.

**Fix:** Add four flags after `-pix_fmt yuv420p` in all three ffmpeg command lists.

### S6.9 [P1] Bitrate target overshoots platform thresholds

[`zerino/ffmpeg/export_generator.py:31-33, 88-90`](zerino/ffmpeg/export_generator.py#L31) — 15 Mbps vertical / 20 Mbps split. TikTok re-encodes anything over ~10 Mbps; YouTube Shorts re-encodes to VP9/AV1. Above ~12 Mbps libx264-slow input, the platform-side encoder can't tell the difference. Not a "looks worse" issue — but real upload latency cost.

**Fix:** Drop `TARGET_BITRATE = "10M"`, `MAX_BITRATE = "13M"`, `BUFFER_SIZE = "20M"`. Drop `SPLIT_TARGET_BITRATE = "14M"`, `SPLIT_MAX_BITRATE = "18M"`, `SPLIT_BUFFER_SIZE = "28M"`. Or switch libx264 to CRF mode (`-crf 18 -maxrate 12M -bufsize 24M`) — quality-targeted, not bitrate-targeted.

### S6.10 [P1] Pre-roll seek leaks discarded audio into the filter graph

[`zerino/ffmpeg/export_generator.py:254, 279-282, 338, 396-399`](zerino/ffmpeg/export_generator.py#L254) — `-ss before -i` then `-ss after -i pre_roll` is correct for video, but the audio filter `loudnorm` (or in the new chain, `dynaudnorm`) sees the pre-roll audio and builds envelope state from it. `dynaudnorm` is far less sensitive than `loudnorm` (S6.1 already mitigates this), but a clean fix is to use `atrim` in the audio filter instead of relying on the output-side `-ss`.

**Fix (optional):** Add `,atrim=start=0,asetpts=PTS-STARTPTS` to the start of the audio chain so the leveler only ever sees the kept window. Mostly cosmetic once S6.1 lands.

### S6.11 [P1] Missing `-nostdin` in ffmpeg invocations

[`zerino/ffmpeg/export_generator.py:276-297, 393-421, 447-465`](zerino/ffmpeg/export_generator.py#L276) and [`zerino/processors/_captions.py:260-273`](zerino/processors/_captions.py#L260) — daemon-mode ffmpeg can hang if it ever wants to prompt (rare but not impossible on permission prompts). Add `"-nostdin"` immediately after `"ffmpeg"` in every command list.

### S6.12 [P2] `subprocess.run(..., capture_output=True)` buffers all stderr in memory

[`zerino/ffmpeg/export_generator.py:297, 421, 465`](zerino/ffmpeg/export_generator.py#L297) — for long renders with verbose ffmpeg output, this can use significant RAM. Use `stderr=subprocess.PIPE, stdout=subprocess.DEVNULL` and truncate stderr if it exceeds N KB.

### S6.13 [P2] `raise Exception(result.stderr)` is unbounded

[`zerino/ffmpeg/export_generator.py:300, 423, 467-468`](zerino/ffmpeg/export_generator.py#L300) — bare `Exception` with full stderr in the message. Use `RuntimeError` and truncate to 1 KB.

### S6.14 [P2] `_pick_video_encoder()` runs at import time

[`zerino/ffmpeg/export_generator.py:152`](zerino/ffmpeg/export_generator.py#L152) — import-time subprocess call. Surprising side effect. Lazy-init on first use instead.

### S6.15 [P0] Wrong scaler kernel for the direction of scaling

[`zerino/ffmpeg/export_generator.py:163, 190, 197, 204, 344, 358, 366`](zerino/ffmpeg/export_generator.py#L163) — `scaler = "lanczos"` everywhere. Lanczos is a **sharp** sinc-windowed kernel. It's excellent for *downscale* (preserves detail) but introduces visible **ringing halos** on edges during *upscale*. The vertical pipeline does a 1.78× upscale from a horizontal crop (608×1080 → 1080×1920); the split path's face quadrant does a 1.78× upscale (960×540 → 1080×960). In both cases lanczos is the wrong choice — it amplifies edge contrast and (combined with subsequent eq + unsharp from S6.4 / S6.5) produces the halo / ringing the user calls "over-processed."

**Fix:** Choose scaler dynamically based on whether the operation is up or downscale:
```python
def _pick_scaler(src_w, src_h, dst_w, dst_h) -> str:
    src_area = src_w * src_h
    dst_area = dst_w * dst_h
    return "bicubic" if dst_area > src_area else "lanczos"
```
Apply in both `build_filter` (after crop is computed) and in the split chain (each half independently). Bicubic on upscale is softer and does not ring. Alternative: `lanczos` with `param0=2` (smaller window) is also softer but adds complexity.

### S6.16 [P0] ABR rate control starves low-motion shots

[`zerino/ffmpeg/export_generator.py:288-289`](zerino/ffmpeg/export_generator.py#L288) — `-b:v 15M -maxrate 18M -bufsize 30M` is average-bitrate mode. x264 will *average* 15 Mbps over the clip but will dip much lower on easy scenes (face-cam, low motion) and use those saved bits later. On a 30-second face-cam clip, the average might land at 8–10 Mbps with peaks of 18 Mbps — **the user's "over-compressed" complaint matches exactly the symptom of ABR starving the face-cam frames.** The probe of existing `caption_tests/` and `renders/tiktok/` files confirms this: they're at 3.8–4.1 Mbps, way below the 15 Mbps target the comment claims.

**Fix:** Switch to **CRF with a maxrate cap**. This is what every quality-focused encode guide recommends:
```python
# libx264:
"-crf", "18", "-maxrate", "12M", "-bufsize", "24M"
# h264_nvenc:
"-rc", "vbr", "-cq", "20", "-maxrate", "12M", "-bufsize", "24M"
```
CRF=18 is "visually lossless"; with maxrate=12M cap and bufsize=24M (2× maxrate, ffmpeg's recommended ratio) the encoder runs as high as it needs but won't dip below efficient for the content. Same approach on the split path with CRF=18, maxrate=14M, bufsize=28M. The bitrate-target / maxrate / bufsize constants (`TARGET_BITRATE`, `MAX_BITRATE`, `BUFFER_SIZE`, and the split equivalents) become unused — delete them.

### S6.17 [P1] Color-space conversion (not just tagging) when source is BT.601

This is the corollary to S0.1 / S0.2. If `probe_metadata` reports the source is not BT.709, prepend a `colorspace=all=bt709:iall=bt601:ispace=bt601` filter to the video chain so the actual *pixel values* are converted, not just the tags. Without this, an OBS recording in BT.601 (some Elgato capture cards default this) gets tagged BT.709 by S6.8 and viewers see slightly green-shifted skin tones on the platform's side.

### S6.18 [P0] `golden_zone` crop is dead code — every clip dead-center

[`zerino/composition/composition_rules.py:130-147, 162-190`](zerino/composition/composition_rules.py#L130) — `get_talking_head_template` writes `"composition_type": "golden_zone"` and `"center_bias": 0.42` into the template; `build_processing_config` copies these into the config dict at keys `template`, `center_bias`, and `crop_anchor`.

[`zerino/ffmpeg/export_generator.py:168-186`](zerino/ffmpeg/export_generator.py#L168) — `build_filter` reads `config.get("crop_mode", "center")` and only applies the 0.42 bias when `crop_mode == "golden_zone"`. **But `build_processing_config` never writes the key `crop_mode`** — it writes `mode` and `crop_anchor`. The `if crop_mode == "golden_zone":` branch is unreachable. Every clip falls into the `else` branch and gets a dead-center crop.

This is the user's pasted loss category #10 ("BAD CROPPING FOR SHORTS"). A streamer whose webcam sits in a corner of the source frame gets cropped to the middle of the source — **excluding the face entirely**. The face-cam-isolated `square` and `split` paths don't have this problem (square fills the whole canvas; split uses explicit `FACE_BOX` / `GAME_BOX`). It's the `vertical` path's talking-head clips that suffer.

**Fix:** Two acceptable resolutions, pick at edit time:

- **Wire the golden_zone path properly** (preferred — preserves design intent):
  - In [`composition_rules.py:175-190`](zerino/composition/composition_rules.py#L175), add `"crop_mode": "golden_zone" if template.get("composition_type") == "golden_zone" else "center"` to the returned config dict.
  - Optionally pass `center_bias` through (today it's hard-coded as `0.42` in `build_filter` — make `build_filter` consume `config.get("center_bias", 0.5)` instead).
  - End-state: talking-head vertical clips bias 42 % left/top — slightly off-center toward the rule-of-thirds-style anchor.

- **Delete the dead code path** (alternative — cuts complexity):
  - Strip the `if crop_mode == "golden_zone":` branch from [`export_generator.py:180-185`](zerino/ffmpeg/export_generator.py#L180).
  - Strip `composition_type`, `center_bias`, and `crop_anchor` from `composition_rules.py`.
  - Document that all crops are dead-center until face-tracked dynamic crop ships (Phase 2).

The face-tracked dynamic crop in HANDOFF_TO_ZERINO.md §6.3 is the long-term answer (MediaPipe + YOLOv8); both options above are stopgaps. Recommended: **wire it** (one-line config change), because for most webcam scenes the 0.42 bias is closer to the actual face position than dead-center.

---

## STAGE 7 — Output → post row → Zernio

Not in the user's "clipping" scope per the original request. Two minor concerns flagged but out of scope:

- [`zerino/publishing/pipeline.py:148-149`](zerino/publishing/pipeline.py#L148) doesn't verify `result.output_path` exists on disk before creating the post row.
- `dispatch_post_ids` atomic claim is correct (already fixed in commit `6f47ea7`).

---

## STAGE 8 — Orphan code (project hygiene — delete)

User confirmed: delete orphans in this PR.

### S8.1 [P2] Orphan ExportService / ExportWorker / ExportValidator

- [`zerino/capture/services/export_service.py`](zerino/capture/services/export_service.py) — never invoked. Uses pre-Router `clip["output_path"]` schema. References platforms `instagram`/`youtube` not the current set.
- [`zerino/capture/workers/export_worker.py`](zerino/capture/workers/export_worker.py) — never started by `capture/main.py`.
- [`zerino/validators/export_validator.py`](zerino/validators/export_validator.py) — only imported by orphan ExportService.

**Fix:** Delete all three files. The `_old_clip_engine/` directory ([`_old_clip_engine/app/ffmpeg/__pycache__/`](_old_clip_engine/) contains only a pyc) should also go.

### S8.2 [P2] Orphan `exports` DB table + repository

[`zerino/db/init_db.py:73-87, 126-139`](zerino/db/init_db.py#L73) creates an `exports` table with `CHECK(platform IN ('tiktok','instagram','youtube'))` — the rest of the codebase uses `tiktok | youtube_shorts | facebook_reels | twitter`. The table and its indexes are orphaned by the move to the `posts` table.

[`zerino/db/repositories/export_repository.py`](zerino/db/repositories/export_repository.py) — only used by orphan ExportService.

**Fix:** Delete the `exports` table CREATE and its three indexes. Delete `export_repository.py`. Note: existing DBs will keep the table; not worth a destructive migration. Add a comment to `init_db.py` explaining the historical reason. (Or — preferred — add a `DROP TABLE IF EXISTS exports` line as a one-shot migration.)

### S8.3 [P2] Legacy `run_export()` non-source path

[`zerino/ffmpeg/export_generator.py:427-469`](zerino/ffmpeg/export_generator.py#L427) — `run_export()` (the non-source variant) is only invoked by the orphan ExportService. Plus by `VerticalProcessor.process()` which is also legacy. Plus by the `if __name__ == '__main__':` test harness at the bottom.

**Fix:** After S8.1 lands, this method has no callers. Delete it. Delete the bottom `if __name__ == '__main__':` test harness too, or wire it to `run_export_from_source` if useful for ad-hoc testing.

### S8.4 [P2] `VerticalProcessor.process()` legacy path

[`zerino/processors/vertical.py:40-80`](zerino/processors/vertical.py#L40) — the non-`process_clip_job` path. Today's flow uses `process_clip_job`; the only thing left calling `process()` is the (legacy) `Router.route()` method at [`zerino/router.py:82-105`](zerino/router.py#L82). Likewise the `if __name__ == '__main__':` test harness at the bottom of `export_generator.py`.

**Fix:** Confirm no caller of `Router.route()` remains (grep across `zerino/` and ops). If clean, delete both `Router.route()` and `VerticalProcessor.process()`. Square and Split processors never had a non-job `process()` method.

### S8.5 [P2] `startup_scan_handler.py` orphan `main()`

Already noted in S2.4. Either delete the `main()` block or delete the whole file (capture/main.py doesn't use it).

---

## STAGE 9 — OBS preflight (the source is the ceiling)

### S9.1 [P0] Healthcheck probe of the most recent recording — warn on bad OBS settings

The cleanest pipeline cannot rescue a starved source. If OBS records at 8 Mbps, no amount of downstream quality tuning makes a 12 Mbps output look like 12 Mbps; we'd be upscaling pixel-level noise. Today the user has no signal that their OBS settings are leaving quality on the table.

**Fix:** Extend `healthcheck.py` with a `check_recent_recording()` call that:
1. Reads the most recent file in `recordings/` if any exists.
2. Uses the extended `probe_metadata` (S0.1) to read source video bitrate, audio bitrate, GOP/keyframe interval, color space, pix_fmt.
3. Warns (non-fatal) if any of:
   - Video bitrate < 12 Mbps (recommend 16–20 Mbps CBR)
   - Audio bitrate < 192 kbps (recommend 320 kbps AAC)
   - Keyframe interval > 4 seconds (recommend 2 s — matches our pre-roll)
   - Color space is BT.601 (the pipeline can convert but warn the user the source is non-standard)
   - pix_fmt is 10-bit (will be converted to 8-bit; warn about dynamic range loss)
4. Prints a one-line "RECOMMENDED OBS SETTINGS" reference (or points at `ops/README.md`).

### S9.2 [P2] Document the recommended OBS configuration

[`ops/README.md`](ops/README.md) — append a section titled "Recommended OBS settings for clip quality":

```
Output mode: Advanced
Output → Recording → Type: Standard
Output → Recording → Encoder: Use Stream Encoder  (single encode, no contention — see HANDOFF doc)
Stream → Encoder: x264 (CPU) or NVENC (NVIDIA GPU)
Stream → Bitrate: 16000 Kbps CBR
Stream → Keyframe interval: 2 s
Stream → Preset: medium (x264) or P5 (NVENC)
Stream → Profile: high
Stream → Tune: (none — we'll re-encode anyway, don't bake in a tune)
Output → Recording → Audio Track 1
Audio → Audio Bitrate (Track 1): 320 Kbps
Audio → Sample Rate: 48000 Hz
Video → Base (Canvas) Resolution: 1920×1080
Video → Output (Scaled) Resolution: 1920×1080
Video → FPS: 60 fps  (set to whatever you stream at — pipeline matches source now)
Advanced → Color Format: NV12 (8-bit 4:2:0)
Advanced → Color Space: 709
Advanced → Color Range: Partial / TV
```

---

## Ranked summary — fix order (top to bottom by perceptual impact)

The order below is the order the code should be touched. P0s first because they're what the user will see/hear; P1s because they prevent silent failures; P2s last because they're hygiene.

| # | Item | Impact | File(s) |
|---|------|--------|---------|
| 1 | S6.1 — replace single-pass loudnorm with `highpass → dynaudnorm → alimiter` | P0 audio | `export_generator.py` |
| 2 | S6.3 — `aac_at` on Mac (native AAC elsewhere) | P0 audio | `export_generator.py` |
| 3 | S6.2 — fade-in before leveler, kill double-onset | P0 audio | `export_generator.py` |
| 4 | S4.1 — `language="en"` everywhere in Whisper calls | P0 captions | `_captions.py`, `router.py`, `vertical.py`, `square.py`, `split.py` |
| 5 | S4.2 — `condition_on_previous_text=False` | P0 captions | `_captions.py` |
| 6 | S4.3 — `vad_filter=True` | P0 captions | `_captions.py` |
| 7 | S6.4 — delete the `eq=contrast/saturation/gamma` filter | P0 video | `export_generator.py` |
| 8 | S6.5 — drop `unsharp` from split, soften `hqdn3d`, drop `-tune film` | P0 video | `export_generator.py` |
| 9 | S6.6 — prefer libx264 over VideoToolbox on Mac | P0 video | `export_generator.py` |
| 9a | S6.15 — bicubic on upscale instead of lanczos | P0 video | `export_generator.py` |
| 9b | S6.16 — CRF mode with maxrate cap instead of ABR | P0 video | `export_generator.py` |
| 9c | S6.18 — wire `golden_zone` crop (or delete dead path) | P0 framing | `composition_rules.py`, `export_generator.py` |
| 10 | S1.1 — float marker timestamps + schema migration | P0 sync | `marker_service.py`, `init_db.py`, `migrate.py`, `clip_service.py` |
| 11 | S2.1 — remove 10 MB never-finishes gate | P0 reliability | `recording_service.py` |
| 12 | S2.2 — stable count → 20 (10s) + fix log string | P0 reliability | `recording_service.py` |
| 13 | S2.3 — strip emojis from `recording_service.py` | P0 Windows | `recording_service.py` |
| 14 | S6.7 — match source fps instead of forcing 60 | P1 video | `export_generator.py` |
| 15 | S6.8 — add bt709 color tags | P1 video | `export_generator.py` |
| 16 | S6.9 — drop bitrate target to 10/13/20 (vert) / 14/18/28 (split) | P1 video | `export_generator.py` |
| 17 | S4.4 + S4.5 — language guard on Whisper output | P1 captions | `_captions.py` |
| 18 | S4.6 — thread-safe `_get_whisper()` | P1 reliability | `_captions.py` |
| 19 | S3.1 — fix clip window math near recording start | P1 sync | `clip_service.py` |
| 20 | S6.11 — `-nostdin` on all ffmpeg invocations | P1 daemon | `export_generator.py`, `_captions.py` |
| 21 | S1.2 — delete `markers_temp` dead code | P1 hygiene | `marker_service.py`, `recording_service.py` |
| 22 | S6.10 — `atrim` audio (optional cleanup once S6.1 lands) | P1 audio | `export_generator.py` |
| 23 | S4.7 — `beam_size=1` (greedy decode) | P2 speed | `_captions.py` |
| 24 | S4.8 — uuid in temp slice filename | P2 collision | `_captions.py` |
| 25 | S4.9 / S5.1 — fix lying docstrings | P2 hygiene | `_captions.py` |
| 26 | S8.1 + S8.2 + S8.3 + S8.4 + S8.5 — delete orphan code | P2 hygiene | many |
| 27 | S6.12 + S6.13 — bounded stderr; `RuntimeError` not `Exception` | P2 hygiene | `export_generator.py` |
| 28 | S6.14 — lazy init `_pick_video_encoder()` | P2 hygiene | `export_generator.py` |
| 29 | S5.2 — ffmpeg path escape misses chars | P2 robustness | `_captions.py` |
| 30 | S3.2 — document `ClipJob.metadata` mutation | P2 hygiene | `models.py` |

---

## Files modified — single inventory

**Heavy edits:**
- [`zerino/ffmpeg/export_generator.py`](zerino/ffmpeg/export_generator.py) — audio, video, encoder, color tags, fps, hygiene
- [`zerino/processors/_captions.py`](zerino/processors/_captions.py) — Whisper language, VAD, conditioning, thread-safety
- [`zerino/capture/services/recording_service.py`](zerino/capture/services/recording_service.py) — 10 MB gate, stable count, emojis

**Light edits:**
- [`zerino/capture/services/marker_service.py`](zerino/capture/services/marker_service.py) — float timestamp
- [`zerino/capture/services/clip_service.py`](zerino/capture/services/clip_service.py) — window math + plumb language
- [`zerino/composition/composition_rules.py`](zerino/composition/composition_rules.py) — wire `crop_mode` for golden_zone (S6.18)
- [`zerino/processors/vertical.py`](zerino/processors/vertical.py) — pass `language="en"`
- [`zerino/processors/square.py`](zerino/processors/square.py) — pass `language="en"`
- [`zerino/processors/split.py`](zerino/processors/split.py) — pass `language="en"`
- [`zerino/router.py`](zerino/router.py) — pass `language="en"`
- [`zerino/db/init_db.py`](zerino/db/init_db.py) — REAL columns; remove exports table
- [`zerino/db/migrate.py`](zerino/db/migrate.py) — REAL column migration; drop exports

**Deletes (orphan):**
- [`zerino/capture/services/export_service.py`](zerino/capture/services/export_service.py)
- [`zerino/capture/workers/export_worker.py`](zerino/capture/workers/export_worker.py)
- [`zerino/validators/export_validator.py`](zerino/validators/export_validator.py)
- [`zerino/db/repositories/export_repository.py`](zerino/db/repositories/export_repository.py)
- [`zerino/capture/handlers/startup_scan_handler.py`](zerino/capture/handlers/startup_scan_handler.py) (or just its `main()`)
- [`_old_clip_engine/`](_old_clip_engine/) directory
- `ExportGenerator.run_export()` (legacy method)
- `Router.route()` + `VerticalProcessor.process()` (legacy paths, if grep confirms no callers)

---

## Verification

Single-clip smoke test (covers everything end-to-end without needing a live stream):

```bash
# 1. Apply schema migration (REAL timestamps)
python -m zerino.db.migrate

# 2. Healthcheck — confirms ffmpeg + libass + (new) detects aac_at on Mac
python -m zerino.healthcheck

# 3. Render a known sample
python -m zerino.cli.clip_file \
    --file recordings/<existing_recording>.mp4 \
    --start 30 --end 60 \
    --platforms tiktok
```

Confirm the output via `ffprobe`:

| Check | Expected (Mac) | Expected (Windows w/ NVENC) |
|-------|---------------|------------------------------|
| `stream=codec_name,bit_rate` (video) | `h264`, ~10 Mbps | `h264`, ~10 Mbps |
| `format_tags=encoder` | `Lavf...` + libx264 | `Lavf...` + NVENC |
| `stream=codec_name,sample_rate,bit_rate` (audio) | `aac` ~192-220k | `aac` ~256k native |
| `stream=color_primaries,color_transfer,color_space,color_range` | `bt709/bt709/bt709/tv` | same |
| `stream=avg_frame_rate` | matches source (30 → 30 fps) | matches source |

Listen + look pass on the rendered file:
- Speech onset is not pumped (steady gain from word 1)
- No "underwater" plosives or sibilants
- Loudness is steady through the clip
- Captions are in English (every word is in Latin script)
- Skin is not orange-shifted
- No edge halos around eyes/hair
- (Split layout) face half is visibly less plastic

Verify capture pipeline:
- Record a 30-second test mp4 in OBS → watch `logs/zerino.log` → confirm `[end] Recording stopped (stable for 10s)` appears ~10s after recording ends (not 30s)
- Hit F8 mid-recording → watch the marker get inserted with a float timestamp (`SELECT timestamp FROM markers ORDER BY id DESC LIMIT 1`)
- Hit F8 at the very start (within 5s of recording start) → confirm the resulting clip is the full configured duration (30s), not truncated to 25s

Verify orphan deletion:
- `python -m zerino.capture.main` still starts cleanly (no import errors from deleted modules)
- `python -m zerino.publishing.batch.scheduler_runner` still starts cleanly
- `grep -rn "from zerino.capture.services.export_service\|from zerino.capture.workers.export_worker\|from zerino.validators.export_validator\|from zerino.db.repositories.export_repository" zerino/` returns nothing

---

## Notes for execution

- **One PR is fine** — the changes are coherent ("fix clipping quality") and most diff is in `export_generator.py` + `_captions.py`. Two commits inside the PR is reasonable: commit 1 = P0 fixes (1–13), commit 2 = P1+P2 (14–30 plus orphan deletion).
- **No behavior change for callers** of public APIs in this plan — `Router.route_clip_job`, processors' `process_clip_job`, and `queue_clip_jobs_for_posting` keep their signatures.
- **DB migration is forward-only** — existing `markers.timestamp` rows keep integer-precision values; new rows get float precision. No backfill needed.
- **The `caption_tests/` and `renders/` directories contain pre-fix artifacts.** Before the user judges results, render fresh clips into a clean output dir (e.g. `renders/_verify/`) so the comparison is current code vs current code.
- **No work on the `zerino` side / publishing side** per the original request scope.
