"""Fortnite detector adapter — two-stage audio-gated OCR (CP3).

Emits events ALREADY identity-filtered to the operator (Decision 1); the game-agnostic
core never sees enemy/teammate elims. Pipeline (BUILD_PLAN §9.1, refined by calibration):
  1. STAGE-1 GATE  — media.audio_pcm -> audio.hot_regions (cheap; gunshot/elim clusters).
  2. STAGE-2 OCR   — only in hot regions: sample frames, OCR the center elim-banner
     (own-elim + multi-kill-count signal) + the left elim-feed (own lines via fuzzy/alias
     gamertag match — OCR mangles the stylized font, so matching is fuzzy by design).
  3. Cluster hits (feed lines persist on-screen for seconds) into Events with t from
     media.timebase (PTS-seconds), weight from profile.event_weights.

All heavy imports are lazy (Mac/live-daemon import nothing). Render path untouched.
"""
from __future__ import annotations

from zerino.detection.adapters.base import DetectorAdapter
from zerino.detection.events import Event
from zerino.detection.profile import GameProfile

OCR_DT = 1.0          # seconds between sampled frames inside a hot region
SUPPRESS_GAP = 3.5    # refractory window: one event per this many seconds (feed persists
                      # on-screen for ~5s, so the SAME elim is OCR'd across several frames)
_TYPE_RANK = {"MULTI_ELIM": 3, "VICTORY": 2, "KILL": 1, "KNOCK": 0}


def _frange(start: float, stop: float, step: float):
    t = start
    while t <= stop + 1e-9:
        yield round(t, 3)
        t += step


class FortniteAdapter(DetectorAdapter):
    game_id = "fortnite"
    detector_version = "fortnite-0.1.0"

    def detect(self, media, profile: GameProfile) -> list[Event]:
        if media is None:
            return []

        from zerino.detection import audio, ocr

        identity = profile.player_identity
        feed_r = profile.hud_regions.get("elim_feed")
        banner_r = profile.hud_regions.get("elim_banner")
        duration = media.timebase.duration if media.timebase else 0.0

        # STAGE 1 — cheap audio gate (combat = loud). Fall back to whole file if quiet.
        try:
            pcm, sr = media.audio_pcm()
            regions = audio.hot_regions(audio.onset_energy(pcm, sr, 1.0), 1.0,
                                        z=0.3, pad_sec=6.0)
        except Exception:
            regions = []
        if not regions:
            regions = [(0.0, duration)]
        times = sorted({t for (s, e) in regions
                        for t in _frange(max(0.0, s), min(duration, e) if duration else e, OCR_DT)})

        # STAGE 2 — gated OCR. hits: (t, type, confidence, source, count)
        hits: list[tuple] = []

        if banner_r:  # center banner = the operator's OWN elim (no gamertag needed)
            for t, img in media.frames_at(times, region=banner_r):
                bk = ocr.banner_kind(ocr.read_region(img))
                if bk:
                    kind, count = bk
                    typ = "MULTI_ELIM" if count >= 2 else ("KNOCK" if kind == "KNOCK" else "KILL")
                    hits.append((t, typ, 0.9, "ocr_banner", count))

        if feed_r:  # left feed — keep only the operator's own lines (fuzzy/alias)
            for t, img in media.frames_at(times, region=feed_r):
                for row in ocr.parse_feed_lines(ocr.read_region(img)):
                    if ocr.is_own_event(row["eliminator"], highlighted=False, identity=identity):
                        typ = "KNOCK" if "knock" in row["verb"] else "KILL"
                        hits.append((t, typ, 0.6, "ocr_killfeed", 1))

        return self._cluster(hits, profile)

    @staticmethod
    def _cluster(hits: list[tuple], profile: GameProfile) -> list[Event]:
        """Refractory suppression: emit one Event per SUPPRESS_GAP window so the same
        elim (OCR'd across several persistent frames) isn't double-counted, while distinct
        elims further apart survive. A higher-rank type (e.g. MULTI_ELIM banner) within the
        window upgrades the event. The game-agnostic core does the cross-event clustering
        into windows (Decision 2); the adapter just emits clean per-elim events.
        """
        if not hits:
            return []
        weights = profile.event_weights
        hits.sort(key=lambda h: h[0])

        events: list[Event] = []
        for t, typ, conf, src, count in hits:
            if events and t - events[-1].t < SUPPRESS_GAP:
                prev = events[-1]
                if _TYPE_RANK.get(typ, 0) > _TYPE_RANK.get(prev.type, 0):
                    events[-1] = Event(t=prev.t, type=typ, source=src, confidence=max(conf, prev.confidence),
                                       weight=float(weights.get(typ, 1.0)),
                                       meta={**prev.meta, "multi_count": max(count, prev.meta.get("multi_count", 1))})
                continue
            events.append(Event(t=t, type=typ, source=src, confidence=conf,
                                weight=float(weights.get(typ, 1.0)),
                                meta={"multi_count": count}))
        return events
