"""Golden-VOD precision/recall harness (DETECTION_DECISIONS.md §5).

The P/R gate: floor recall >= 0.8 / precision >= 0.7; recall PRIORITIZED and WEIGHTED
toward high-value events (multi-kills). Matching uses a per-label time tolerance.
"""
from __future__ import annotations

import json
from pathlib import Path

VALUE_WEIGHT = {"routine": 1.0, "multi": 3.0, "clutch": 4.0}


def precision_recall(predicted_ts, labels, *, tol_sec: float = 2.5, value_weights=None) -> dict:
    """Greedy 1:1 match of predicted event times to labeled elim times within tol_sec.
    Returns precision, recall, recall_weighted (high-value events weighted up), and counts."""
    vw = value_weights or VALUE_WEIGHT
    pred = list(predicted_ts)
    used: set[int] = set()
    tp = 0
    tp_w = 0.0
    total_w = sum(vw.get(l.get("value", "routine"), 1.0) for l in labels)
    for l in labels:
        w = vw.get(l.get("value", "routine"), 1.0)
        match = next((i for i, t in enumerate(pred)
                      if i not in used and abs(t - l["t"]) <= tol_sec), None)
        if match is not None:
            used.add(match)
            tp += 1
            tp_w += w
    fp = len(pred) - len(used)
    fn = len(labels) - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / len(labels) if labels else 0.0
    recall_weighted = tp_w / total_w if total_w else 0.0
    return {"precision": precision, "recall": recall, "recall_weighted": recall_weighted,
            "tp": tp, "fp": fp, "fn": fn, "n_pred": len(pred), "n_labels": len(labels)}


def run_golden_eval(adapter, profile, fixtures_dir, *, media_opener=None) -> dict:
    """Run `adapter` over every *.labels.json segment in fixtures_dir; aggregate P/R.
    media_opener defaults to MediaHandle.open (injectable for tests)."""
    if media_opener is None:
        from zerino.detection.media import MediaHandle
        media_opener = MediaHandle.open

    fixtures_dir = Path(fixtures_dir)
    all_pred: list[float] = []
    all_labels: list[dict] = []
    per_segment = []
    tol = 2.5
    for lp in sorted(fixtures_dir.glob("*.labels.json")):
        meta = json.loads(lp.read_text())
        tol = meta.get("match_tolerance_sec", tol)
        seg = fixtures_dir / meta["segment_file"]
        if not seg.exists():
            continue
        media = media_opener(seg)
        events = adapter.detect(media, profile)
        pred_ts = [e.t for e in events]
        seg_pr = precision_recall(pred_ts, meta["elims"], tol_sec=tol)
        per_segment.append({"segment": meta["segment_file"], **seg_pr,
                            "pred_ts": [round(t, 1) for t in pred_ts]})
        all_pred.extend(pred_ts)
        all_labels.extend(meta["elims"])

    agg = precision_recall(all_pred, all_labels, tol_sec=tol)
    agg["per_segment"] = per_segment
    agg["tol_sec"] = tol
    return agg
