from __future__ import annotations

import numpy as np

from tools.voxcpm2.final_encoded_delivery_qa import evaluate_encoded_segment


def _segment(duration: float) -> dict[str, object]:
    return {
        "id": 19,
        "start": 0.0,
        "end": duration,
        "tail_guard": 0.18,
        "start_delay_ms": 0,
        "text": "Она знает, что грядёт, — и смеётся.",
    }


def _falling_speech(duration: float = 1.55) -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (105.0 - 155.0) / duration
    phase = 2.0 * np.pi * (155.0 * time + 0.5 * slope * time**2)
    envelope = np.linspace(0.24, 0.07, len(time), dtype=np.float64)
    speech = envelope * np.sin(phase)
    fade = int(0.04 * sample_rate)
    speech[:fade] *= np.linspace(0.0, 1.0, fade)
    speech[-fade:] *= np.linspace(1.0, 0.0, fade)
    return speech.astype(np.float32), sample_rate


def test_post_aac_gate_accepts_clean_resolved_final_segment() -> None:
    speech, sample_rate = _falling_speech()
    tail = np.zeros(int(0.35 * sample_rate), dtype=np.float32)
    audio = np.concatenate([speech, tail])

    report = evaluate_encoded_segment(
        audio,
        sample_rate,
        _segment(len(audio) / sample_rate),
    )

    assert report["passed"] is True
    assert report["late_tail"]["suspicious"] is False


def test_post_aac_gate_rejects_late_broadband_synthesis_burst() -> None:
    speech, sample_rate = _falling_speech()
    valley = np.zeros(int(0.06 * sample_rate), dtype=np.float32)
    rng = np.random.default_rng(20260730)
    burst = rng.normal(0.0, 0.16, int(0.12 * sample_rate)).astype(np.float32)
    burst *= np.hanning(len(burst)).astype(np.float32)
    silence = np.zeros(int(0.27 * sample_rate), dtype=np.float32)
    audio = np.concatenate([speech, valley, burst, silence])

    report = evaluate_encoded_segment(
        audio,
        sample_rate,
        _segment(len(audio) / sample_rate),
    )

    assert report["passed"] is False
    assert report["late_tail"]["suspicious"] is True
    assert "late_broadband_burst" in report["failures"]
