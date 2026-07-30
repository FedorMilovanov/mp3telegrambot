from __future__ import annotations

import numpy as np

from tools.voxcpm2.direct_source_prosody import (
    candidate_pitch_evidence_ok,
    source_prosody_penalty,
)


def _chirp(start_hz: float, end_hz: float):
    sample_rate = 48_000
    duration = 2.0
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (end_hz - start_hz) / duration
    phase = 2.0 * np.pi * (start_hz * time + 0.5 * slope * time**2)
    audio = (0.23 * np.sin(phase)).astype(np.float32)
    fade = int(0.05 * sample_rate)
    audio[:fade] *= np.linspace(0.0, 1.0, fade)
    audio[-fade:] *= np.linspace(1.0, 0.0, fade)
    audio = np.concatenate(
        [audio, np.zeros(int(0.18 * sample_rate), dtype=np.float32)]
    )
    return audio, sample_rate


def _candidate(start_hz: float, end_hz: float):
    audio, sample_rate = _chirp(start_hz, end_hz)
    return {
        "samples": audio,
        "sample_rate": sample_rate,
        "duration": len(audio) / sample_rate,
        "pitch": {
            "voiced_ratio": 0.72,
            "f0_median": (start_hz + end_hz) / 2.0,
            "f0_p90": max(start_hz, end_hz),
        },
        "activity": {
            "active_ratio": 0.74,
            "max_internal_gap": 0.0,
        },
    }


def _segment(text: str):
    return {
        "text": text,
        "start": 0.0,
        "end": 2.35,
        "tail_guard": 0.17,
        "expression_tier": "earnest",
        "style_instruction": "",
        "source_prosody": {
            "voiced_ratio": 0.70,
            "f0_median": 125.0,
            "f0_p90": 170.0,
            "active_ratio": 0.72,
            "max_internal_gap": 0.0,
        },
    }


def test_cadence_evidence_controls_candidate_acceptance():
    rising = _candidate(80.0, 200.0)
    falling = _candidate(200.0, 80.0)

    rising_penalty = source_prosody_penalty(rising, _segment("Завершение."))
    falling_penalty = source_prosody_penalty(falling, _segment("Завершение."))

    assert rising["cadence_hard_ok"] is False
    assert rising["source_prosody_match"]["cadence"]["cadence"] == "terminal"
    assert rising_penalty > falling_penalty
    assert candidate_pitch_evidence_ok(rising) is False
    assert falling["cadence_hard_ok"] is True
    assert candidate_pitch_evidence_ok(falling) is True


def test_linked_phrase_underfill_is_fail_closed():
    candidate = _candidate(100.0, 150.0)
    candidate["duration"] = 0.95
    result = source_prosody_penalty(
        candidate,
        {
            **_segment("Помните мой любимый стих"),
            "end": 2.50,
            "tail_guard": 0.20,
        },
    )

    cadence = candidate["source_prosody_match"]["cadence"]
    assert cadence["cadence"] == "linked"
    assert cadence["duration_ratio"] < 0.50
    assert "continuation_too_short" in cadence["failures"]
    assert candidate["cadence_hard_ok"] is False
    assert candidate_pitch_evidence_ok(candidate) is False
    assert result >= cadence["penalty"]
