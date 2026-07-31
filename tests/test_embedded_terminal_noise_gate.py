from __future__ import annotations

import numpy as np

from tools.voxcpm2 import direct_tail_artifact


def _tone(rate: int, seconds: float, frequency: float, amplitude: float) -> np.ndarray:
    time = np.arange(int(rate * seconds), dtype=np.float64) / rate
    return (np.sin(2.0 * np.pi * frequency * time) * amplitude).astype(np.float32)


def test_quiet_dip_noise_island_and_voice_residue_is_rejected() -> None:
    rate = 16_000
    audio = np.zeros(int(rate * 1.60), dtype=np.float32)
    audio[: int(rate * 1.10)] = _tone(rate, 1.10, 155.0, 0.19)
    # 60 ms quiet dip, 60 ms broadband island, 170 ms harmonic residue.
    rng = np.random.default_rng(20260731)
    audio[int(rate * 1.16): int(rate * 1.22)] = (
        rng.standard_normal(int(rate * 0.06)).astype(np.float32) * 0.17
    )
    audio[int(rate * 1.22): int(rate * 1.39)] = _tone(
        rate, 0.17, 150.0, 0.14
    )

    report = direct_tail_artifact.detect_late_broadband_tail(audio, rate)

    assert report["suspicious"] is True, report
    assert report["artifact_type"] == "embedded_terminal_broadband_island"
    assert report["repairable"] is False
    assert report["burst_high_frequency_ratio"] >= 0.50
    assert report["valley_rebound_db"] >= 7.0


def test_continuous_terminal_fricative_without_quiet_dip_is_not_embedded_artifact() -> None:
    rate = 16_000
    audio = np.zeros(int(rate * 1.60), dtype=np.float32)
    audio[: int(rate * 1.16)] = _tone(rate, 1.16, 155.0, 0.19)
    rng = np.random.default_rng(17)
    # No closure/dip before the broadband section; this models a connected final
    # fricative rather than a detached synthesis island.
    audio[int(rate * 1.16): int(rate * 1.22)] = (
        rng.standard_normal(int(rate * 0.06)).astype(np.float32) * 0.08
    )
    audio[int(rate * 1.22): int(rate * 1.39)] = _tone(
        rate, 0.17, 150.0, 0.14
    )

    embedded = direct_tail_artifact._embedded_terminal_island(audio, rate)

    assert embedded["suspicious"] is False, embedded
