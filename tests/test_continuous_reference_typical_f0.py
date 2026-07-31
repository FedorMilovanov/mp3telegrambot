from __future__ import annotations

import numpy as np

from tools.voxcpm2 import continuous_reference_policy


def _fixture(rate: int = 1_000) -> tuple[np.ndarray, list[tuple[float, float]]]:
    audio = np.zeros(rate * 18, dtype=np.float32)
    audio[0 : 5 * rate] = 0.10
    audio[int(5.6 * rate) : int(10.6 * rate)] = 0.20
    audio[int(11.2 * rate) : int(16.2 * rate)] = 0.30
    return audio, [(0.0, 5.0), (5.6, 10.6), (11.2, 16.2)]


def _amplitude(samples: np.ndarray) -> float:
    return float(np.median(np.abs(np.asarray(samples, dtype=np.float64))))


def test_equal_quality_windows_choose_typical_pitch_not_lowest_pitch(monkeypatch) -> None:
    audio, intervals = _fixture()

    def pitch(samples, sample_rate):
        amplitude = _amplitude(samples)
        f0 = 90.0 if amplitude < 0.15 else 170.0 if amplitude < 0.25 else 250.0
        return {"voiced_ratio": 0.58, "f0_median": f0, "f0_p90": f0 * 1.30}

    def activity(samples, sample_rate):
        return {"active_ratio": 0.74, "max_internal_gap": 0.08}

    monkeypatch.setattr(
        continuous_reference_policy._legacy.professional_audio_v45,
        "pitch_profile",
        pitch,
    )
    monkeypatch.setattr(
        continuous_reference_policy._legacy.professional_audio_v45,
        "activity_stats",
        activity,
    )

    candidates = continuous_reference_policy._candidate_windows(
        audio,
        1_000,
        intervals,
        target_seconds=5.0,
    )
    selected = min(candidates, key=lambda item: float(item["score"]))

    assert selected["selection_policy"] == (
        "robust-typical-f0-continuous-window-v1"
    )
    assert selected["stats"]["f0_median"] == 170.0
    assert selected["robust_f0_median"] == 170.0
    lowest = [item for item in candidates if item["stats"]["f0_median"] == 90.0]
    assert lowest
    assert selected["score"] < min(item["score"] for item in lowest)


def test_quality_metrics_remain_part_of_reference_ranking(monkeypatch) -> None:
    audio, intervals = _fixture()

    def pitch(samples, sample_rate):
        amplitude = _amplitude(samples)
        f0 = 90.0 if amplitude < 0.15 else 170.0 if amplitude < 0.25 else 250.0
        return {"voiced_ratio": 0.58, "f0_median": f0, "f0_p90": f0 * 1.30}

    def activity(samples, sample_rate):
        amplitude = _amplitude(samples)
        if 0.15 <= amplitude < 0.25:
            return {"active_ratio": 0.30, "max_internal_gap": 0.82}
        return {"active_ratio": 0.74, "max_internal_gap": 0.08}

    monkeypatch.setattr(
        continuous_reference_policy._legacy.professional_audio_v45,
        "pitch_profile",
        pitch,
    )
    monkeypatch.setattr(
        continuous_reference_policy._legacy.professional_audio_v45,
        "activity_stats",
        activity,
    )

    candidates = continuous_reference_policy._candidate_windows(
        audio,
        1_000,
        intervals,
        target_seconds=5.0,
    )
    selected = min(candidates, key=lambda item: float(item["score"]))

    # The central-pitch window is still usable, but a severe gap/activity defect
    # must prevent it from winning merely because its F0 is typical.
    assert selected["stats"]["f0_median"] in {90.0, 250.0}
    assert selected["stats"]["max_internal_gap"] == 0.08
