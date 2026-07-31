from __future__ import annotations

import numpy as np

from tools.voxcpm2 import russian_pronunciation


def _tone(rate: int, seconds: float, frequency: float, amplitude: float) -> np.ndarray:
    time = np.arange(int(rate * seconds), dtype=np.float64) / rate
    return (np.sin(2.0 * np.pi * frequency * time) * amplitude).astype(np.float32)


def _segment() -> dict[str, object]:
    segment: dict[str, object] = {
        "id": 1,
        "text": "Это то, что грядёт.",
        "expression_tier": "earnest",
    }
    segment["pronunciation"] = russian_pronunciation.prepare_segment(segment)
    return segment


def test_final_stress_uses_duration_energy_and_pitch() -> None:
    rate = 16_000
    audio = np.zeros(rate, dtype=np.float32)
    audio[int(0.30 * rate):int(0.42 * rate)] = _tone(rate, 0.12, 165.0, 0.18)
    audio[int(0.55 * rate):int(0.80 * rate)] = _tone(rate, 0.25, 175.0, 0.25)

    evidence = russian_pronunciation.stress_evidence(audio, rate, _segment())

    assert evidence["policy"] == "final-stressed-syllable-duration-energy-pitch-v2"
    assert evidence["passed"] is True
    assert evidence["strong_cue_count"] >= 2
    assert evidence["previous_f0_hz"] > 0.0
    assert evidence["final_f0_hz"] > 0.0
    assert evidence["manual_review_required"] is True


def test_phrase_final_lengthening_alone_does_not_prove_stress() -> None:
    rate = 16_000
    audio = np.zeros(rate, dtype=np.float32)
    audio[int(0.30 * rate):int(0.43 * rate)] = _tone(rate, 0.13, 175.0, 0.24)
    # Long, but much quieter and sharply falling: duration alone must not pass.
    audio[int(0.55 * rate):int(0.78 * rate)] = _tone(rate, 0.23, 130.0, 0.07)

    evidence = russian_pronunciation.stress_evidence(audio, rate, _segment())

    assert evidence["passed"] is False
    assert evidence["reason"] == "final_stressed_nucleus_not_supported"
    assert evidence["manual_review_required"] is True
