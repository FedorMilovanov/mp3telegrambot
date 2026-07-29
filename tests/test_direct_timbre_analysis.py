from __future__ import annotations

import math

import numpy as np

from tools.voxcpm2.direct_timbre_analysis import (
    HARD_SIMILARITY_FLOOR,
    spectral_envelope,
    spectral_similarity,
    timbre_hard_ok,
    timbre_penalty,
)


def _tone(frequency: float, *, seconds: float = 2.0, sample_rate: int = 16_000) -> np.ndarray:
    time = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    # Fundamental plus stable harmonics resembles a voiced spectral envelope
    # better than a pure sine and gives deterministic test data.
    signal = (
        np.sin(2.0 * math.pi * frequency * time)
        + 0.45 * np.sin(2.0 * math.pi * frequency * 2.0 * time)
        + 0.20 * np.sin(2.0 * math.pi * frequency * 3.0 * time)
    )
    return (signal * 0.15).astype(np.float32)


def test_identical_timbre_envelopes_have_unit_similarity() -> None:
    profile = spectral_envelope(_tone(120.0), 16_000)
    assert profile["frames"] > 0
    assert spectral_similarity(profile, profile) == 1.0
    assert timbre_penalty(1.0) == 0.0
    assert timbre_hard_ok(1.0) is True


def test_spectral_metric_is_soft_for_related_voiced_audio() -> None:
    low = spectral_envelope(_tone(120.0), 16_000)
    nearby = spectral_envelope(_tone(135.0), 16_000)
    similarity = spectral_similarity(low, nearby)
    assert 0.0 <= similarity <= 1.0
    assert timbre_penalty(similarity) >= 0.0


def test_only_gross_mismatch_crosses_hard_floor() -> None:
    assert 0.0 < HARD_SIMILARITY_FLOOR < 0.5
    assert timbre_hard_ok(HARD_SIMILARITY_FLOOR) is True
    assert timbre_hard_ok(HARD_SIMILARITY_FLOOR - 0.01) is False


def test_missing_or_invalid_profile_fails_closed() -> None:
    valid = spectral_envelope(_tone(120.0), 16_000)
    assert spectral_similarity({}, {}) == 0.0
    assert spectral_similarity(valid, {}) == 0.0
    assert spectral_similarity({}, valid) == 0.0
    assert spectral_similarity({"bands": [0.5, 0.5]}, {"bands": [1.0]}) == 0.0
    assert spectral_similarity({"bands": [0.0, 0.0]}, {"bands": [0.5, 0.5]}) == 0.0
    assert timbre_hard_ok(spectral_similarity({}, {})) is False
