from __future__ import annotations

import numpy as np

from tools.voxcpm2.direct_source_prosody import (
    candidate_pitch_evidence_ok,
    source_prosody_penalty,
)
from tools.voxcpm2.direct_tail_artifact import detect_late_broadband_tail


def _voice(sample_rate: int, duration: float, frequency: float = 125.0) -> np.ndarray:
    time = np.arange(int(sample_rate * duration), dtype=np.float64) / sample_rate
    carrier = np.sin(2.0 * np.pi * frequency * time)
    harmonic = 0.35 * np.sin(2.0 * np.pi * frequency * 2.0 * time)
    envelope = np.ones_like(time)
    fade = min(len(envelope) // 4, int(sample_rate * 0.05))
    envelope[:fade] *= np.linspace(0.0, 1.0, fade)
    envelope[-fade:] *= np.linspace(1.0, 0.0, fade)
    return (0.22 * (carrier + harmonic) * envelope).astype(np.float32)


def _artifact_candidate() -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    speech = _voice(sample_rate, 1.55)
    quiet = np.zeros(int(sample_rate * 0.06), dtype=np.float32)
    rng = np.random.default_rng(20260730)
    noise = rng.normal(0.0, 0.16, int(sample_rate * 0.12)).astype(np.float32)
    noise *= np.hanning(len(noise)).astype(np.float32)
    tail = np.zeros(int(sample_rate * 0.24), dtype=np.float32)
    return np.concatenate([speech, quiet, noise, tail]), sample_rate


def _candidate(audio: np.ndarray, sample_rate: int) -> dict:
    return {
        "samples": audio,
        "sample_rate": sample_rate,
        "duration": len(audio) / sample_rate,
        "pitch": {"voiced_ratio": 0.72, "f0_median": 125.0, "f0_p90": 170.0},
        "activity": {"active_ratio": 0.70, "max_internal_gap": 0.0},
    }


def _segment() -> dict:
    return {
        "text": "Финальная фраза.",
        "start": 0.0,
        "end": 2.2,
        "tail_guard": 0.18,
        "expression_tier": "earnest",
        "source_prosody": {
            "voiced_ratio": 0.70,
            "f0_median": 125.0,
            "f0_p90": 170.0,
            "active_ratio": 0.70,
            "max_internal_gap": 0.0,
        },
    }


def test_late_broadband_burst_is_detected():
    audio, sample_rate = _artifact_candidate()
    info = detect_late_broadband_tail(audio, sample_rate)

    assert info["suspicious"] is True
    assert info["artifact_type"] == "late_broadband_burst"
    assert info["burst_high_zcr"] >= 0.25
    assert info["trailing_quiet_seconds"] >= 0.08


def test_normal_voiced_fade_is_not_rejected():
    sample_rate = 48_000
    audio = np.concatenate(
        [
            _voice(sample_rate, 1.75),
            np.zeros(int(sample_rate * 0.22), dtype=np.float32),
        ]
    )
    assert detect_late_broadband_tail(audio, sample_rate)["suspicious"] is False


def test_tail_artifact_flows_into_candidate_acceptance_gate():
    audio, sample_rate = _artifact_candidate()
    candidate = _candidate(audio, sample_rate)
    source_prosody_penalty(candidate, _segment())
    evidence = candidate["source_prosody_match"]["cadence"]

    assert evidence["tail_artifact"]["suspicious"] is True
    assert "late_broadband_tail" in evidence["failures"]
    assert candidate["cadence_hard_ok"] is False
    assert candidate_pitch_evidence_ok(candidate) is False
