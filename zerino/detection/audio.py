"""Audio onset/energy — the cheap STAGE-1 GATE that decides where OCR runs.

numpy-only (no librosa) so deps stay light. Gunshot/elim clusters = loud regions ->
OCR only there (two-stage gating, DETECTION_DECISIONS.md §0 / BUILD_PLAN §9.1).
"""
from __future__ import annotations


def onset_energy(pcm, sr: int, hop_sec: float = 1.0):
    """Short-window RMS energy profile over `pcm`. Returns np.ndarray, one value per
    hop_sec window (trailing partial window dropped)."""
    import numpy as np

    pcm = np.asarray(pcm, dtype=np.float32)
    hop = max(1, int(sr * hop_sec))
    n = len(pcm) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    windows = pcm[: n * hop].reshape(n, hop)
    return np.sqrt(np.mean(windows ** 2, axis=1) + 1e-12).astype(np.float32)


def hot_regions(energy, hop_sec: float, *, z: float = 1.0, pad_sec: float = 5.0,
                min_gap_sec: float = 3.0):
    """Reduce the energy profile to combat windows [(start,end), ...]: windows above
    median + z*std, converted to seconds, padded by pad_sec, and merged when within
    min_gap_sec. These are the regions worth running (expensive) OCR on."""
    import numpy as np

    energy = np.asarray(energy, dtype=np.float32)
    if energy.size == 0:
        return []
    thr = float(np.median(energy) + z * np.std(energy))
    hot = energy > thr

    raw = []
    i, N = 0, len(energy)
    while i < N:
        if hot[i]:
            j = i
            while j < N and hot[j]:
                j += 1
            start = max(0.0, i * hop_sec - pad_sec)
            end = j * hop_sec + pad_sec          # j is exclusive (one past last hot window)
            raw.append([start, end])
            i = j
        else:
            i += 1

    merged: list[list[float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] <= min_gap_sec:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]
