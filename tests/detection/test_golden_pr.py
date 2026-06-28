"""CP2 (RED): the golden-VOD precision/recall GATE — the objective "does it catch the
play" check (DETECTION_DECISIONS.md §5).

Floor to proceed: recall >= 0.8, precision >= 0.7. Recall is PRIORITIZED and WEIGHTED
toward high-value events (multi-kills). Runs the FortniteAdapter over each hand-labeled
golden segment and matches detected event times to labels within the per-label tolerance.
Reds until CP3 (the stub adapter detects nothing -> recall 0.0 < 0.8 / NotImplementedError).

Golden media is machine-local (gitignored); skips when absent so other machines/CI pass.
"""
import json
from pathlib import Path

import pytest

from zerino.detection.adapters.fortnite import FortniteAdapter
from zerino.detection.media import MediaHandle
from zerino.detection.profile import load_profile

FIXTURES = Path(__file__).parent / "fixtures" / "fortnite_golden"
LABELS = sorted(FIXTURES.glob("*.labels.json"))
HAVE_MEDIA = LABELS and all((FIXTURES / json.loads(p.read_text())["segment_file"]).exists()
                            for p in LABELS)

# HARD GATE (DETECTION_DECISIONS.md §5). Labels are operator-ratified (seg1 38/42/54
# confirmed; seg2@16, seg3@25 watch-confirmed) and unseen-footage validation PASSED
# (2026-06-26 00-08-55: 8 events matched real kills, no code change), so the provisional
# xfail was removed and this now asserts the recall>=0.8 / precision>=0.7 floor.
pytestmark = pytest.mark.skipif(
    not HAVE_MEDIA,
    reason="golden media is machine-local (regenerate via scratchpad/cut_golden.py)")

# value weighting: missing a multi-kill is far worse than missing a routine kill
VALUE_WEIGHT = {"routine": 1.0, "multi": 3.0, "clutch": 4.0}

# The golden labels are ELIMINATIONS. KNOCKs (downs) are detected by design — they bridge
# kills in clustering — but they are not eliminations, and a lone knock scores below
# score_threshold so it never becomes a clip. Denser sampling (OCR_DT=0.333) catches more
# knocks; scoring those real downs against elim-only labels would be a category error (it
# tanked precision to 0.56 while every predicted ELIM still matched a label). So the P/R gate
# evaluates predicted eliminations against the elim labels.
ELIM_TYPES = {"KILL", "MULTI_ELIM", "VICTORY"}


def _precision_recall(pred_ts, labels, tol):
    """Greedy 1:1 match of predicted times to labeled times within `tol`."""
    used = set()
    tp = tp_w = 0.0
    total_w = sum(VALUE_WEIGHT.get(l.get("value", "routine"), 1.0) for l in labels)
    for l in labels:
        w = VALUE_WEIGHT.get(l.get("value", "routine"), 1.0)
        cand = [i for i, t in enumerate(pred_ts)
                if i not in used and abs(t - l["t"]) <= tol]
        if cand:
            used.add(cand[0])
            tp += 1
            tp_w += w
    fp = len(pred_ts) - len(used)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / len(labels) if labels else 0.0
    recall_weighted = tp_w / total_w if total_w else 0.0
    return precision, recall, recall_weighted


def test_golden_precision_recall_meets_floor():
    adapter = FortniteAdapter()
    profile = load_profile("fortnite")
    all_pred, all_labels, tol = [], [], 2.5
    for lp in LABELS:
        meta = json.loads(lp.read_text())
        tol = meta.get("match_tolerance_sec", 2.5)
        media = MediaHandle.open(FIXTURES / meta["segment_file"])
        events = adapter.detect(media, profile)           # CP3 implements
        all_pred.extend(e.t for e in events if e.type in ELIM_TYPES)   # elims vs elim labels
        all_labels.extend(meta["elims"])

    precision, recall, recall_w = _precision_recall(all_pred, all_labels, tol)
    # recall prioritized; weighted recall guards the high-value (multi-kill) events
    assert recall >= 0.8, f"recall {recall:.2f} < 0.8 floor"
    assert recall_w >= 0.8, f"weighted recall {recall_w:.2f} < 0.8 (high-value events missed)"
    assert precision >= 0.7, f"precision {precision:.2f} < 0.7 floor"
