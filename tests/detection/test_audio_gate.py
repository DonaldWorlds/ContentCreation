"""CP2 (RED): the cheap STAGE-1 audio gate (synthetic PCM, no media needed).

A quiet signal with one loud burst -> onset_energy peaks there -> hot_regions returns a
window covering the burst (and not the quiet stretches). Reds on NotImplementedError.
"""
import numpy as np

from zerino.detection.audio import onset_energy, hot_regions

SR = 16000


def _signal_with_burst():
    """30s of quiet noise with a loud 2s burst centered at t=20s."""
    n = 30 * SR
    rng = np.random.RandomState(0)
    pcm = (rng.randn(n) * 0.02).astype(np.float32)
    burst = slice(19 * SR, 21 * SR)
    pcm[burst] += (rng.randn(2 * SR) * 0.5).astype(np.float32)
    return pcm


def test_onset_energy_peaks_at_burst():
    energy = onset_energy(_signal_with_burst(), SR, hop_sec=1.0)
    assert len(energy) == 30
    assert int(np.argmax(energy)) in (19, 20)


def test_hot_regions_covers_burst_only():
    energy = onset_energy(_signal_with_burst(), SR, hop_sec=1.0)
    regions = hot_regions(energy, hop_sec=1.0, z=1.0, pad_sec=2.0)
    assert any(s <= 20.0 <= e for (s, e) in regions)
    assert not any(s <= 5.0 <= e for (s, e) in regions)  # quiet stretch excluded
