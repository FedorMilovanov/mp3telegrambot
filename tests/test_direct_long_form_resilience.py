from __future__ import annotations

import numpy as np
import pytest

from tools.voxcpm2.direct_max_quality_analysis import (
    FIT_TEMPO_POLICY,
    candidate_hard_ok,
    required_tempo,
)
from tools.voxcpm2.direct_max_quality_io import MAX_TEMPO, PREFERRED_MAX_TEMPO
from tools.voxcpm2.direct_max_quality_render import (
    ADAPTIVE_RETRY_POLICY,
    _generation_profile,
)
from tools.voxcpm2.direct_russian_cadence import evaluate_candidate_cadence


def _valid_candidate(duration: float) -> dict[str, object]:
    return {
        "duration": duration,
        "clipping_ratio": 0.0,
        "activity": {"active_ratio": 0.82, "max_internal_gap": 0.0},
        "pitch": {"voiced_ratio": 0.76, "f0_median": 120.0, "f0_p90": 145.0},
        "voice_match": {
            "f0_median_ratio": 1.0,
            "f0_p90_ratio": 1.0,
            "spectral_similarity": 0.92,
        },
        "tail_info": {"suspicious": False},
    }


def _shaped_chirp(
    start_hz: float,
    end_hz: float,
    *,
    early_peak: bool = False,
    duration: float = 2.0,
) -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    time = np.arange(int(duration * sample_rate), dtype=np.float64) / sample_rate
    slope = (end_hz - start_hz) / duration
    phase = 2.0 * np.pi * (start_hz * time + 0.5 * slope * time**2)
    if early_peak:
        envelope = np.interp(time, [0.0, 0.35, duration], [0.08, 0.30, 0.07])
    else:
        envelope = np.interp(time, [0.0, duration * 0.65, duration], [0.07, 0.11, 0.30])
    audio = envelope * np.sin(phase)
    fade = int(0.03 * sample_rate)
    audio[:fade] *= np.linspace(0.0, 1.0, fade)
    audio[-fade:] *= np.linspace(1.0, 0.0, fade)
    return np.concatenate(
        [audio.astype(np.float32), np.zeros(int(0.18 * sample_rate), dtype=np.float32)]
    ), sample_rate


def _cadence(
    text: str,
    audio: np.ndarray,
    sample_rate: int,
    **metadata: object,
) -> dict[str, object]:
    segment: dict[str, object] = {
        "id": 1,
        "text": text,
        "start": 0.0,
        "end": 2.35,
        "tail_guard": 0.17,
    }
    segment.update(metadata)
    return evaluate_candidate_cadence(
        {
            "samples": audio,
            "sample_rate": sample_rate,
            "duration": len(audio) / sample_rate,
        },
        segment,
    )


def test_candidate_fit_tempo_has_preferred_and_hard_boundaries() -> None:
    slot = 4.0
    preferred = _valid_candidate(slot * PREFERRED_MAX_TEMPO)
    validated_margin = _valid_candidate(slot * 1.358)
    above_hard = _valid_candidate(slot * (MAX_TEMPO + 0.008))

    assert FIT_TEMPO_POLICY == "candidate-fit-tempo-hard-gate-v2"
    assert PREFERRED_MAX_TEMPO == pytest.approx(1.35)
    assert MAX_TEMPO == pytest.approx(1.36)
    assert candidate_hard_ok(preferred, slot) is True
    assert candidate_hard_ok(validated_margin, slot) is True
    assert required_tempo(validated_margin, slot) == pytest.approx(1.358)
    assert candidate_hard_ok(above_hard, slot) is False
    assert required_tempo(above_hard, slot) > MAX_TEMPO


def test_adaptive_profiles_add_two_bounded_rescue_attempts() -> None:
    assert ADAPTIVE_RETRY_POLICY == "direct-candidate-adaptive-retry-v1"
    profiles = [_generation_profile(index, 1.9, 16) for index in range(1, 6)]

    assert len(set(profiles)) == 5
    assert profiles[3][1] > profiles[1][1]
    assert profiles[4][0] < profiles[0][0]
    assert all(1.35 <= cfg <= 2.20 for cfg, _steps in profiles)
    assert all(1 <= steps <= 40 for _cfg, steps in profiles)
    with pytest.raises(ValueError, match="Неподдерживаемая попытка"):
        _generation_profile(6, 1.9, 16)


def test_flat_declarative_without_energy_release_is_rejected() -> None:
    audio, sample_rate = _shaped_chirp(120.0, 120.0, early_peak=False)
    result = _cadence("И не на то, что выйдет замуж.", audio, sample_rate)

    assert result["hard_ok"] is False
    assert "terminal_not_resolved" in result["failures"]


def test_multiword_exclamation_rejects_early_emotional_burst() -> None:
    early, sample_rate = _shaped_chirp(180.0, 90.0, early_peak=True)
    late, _ = _shaped_chirp(180.0, 90.0, early_peak=False)

    rejected = _cadence("Я смеюсь тебе в лицо!", early, sample_rate)
    accepted = _cadence("Я смеюсь тебе в лицо!", late, sample_rate)

    assert rejected["hard_ok"] is False
    assert "emphasis_too_early" in rejected["failures"]
    assert accepted["peak_energy_bin"] in {2, 3, 4}
    assert "emphasis_too_early" not in accepted["failures"]


def test_dominant_late_source_build_rejects_early_russian_burst() -> None:
    early, sample_rate = _shaped_chirp(180.0, 90.0, early_peak=True)
    late, _ = _shaped_chirp(180.0, 90.0, early_peak=False)
    source_prosody = {
        "contour": {
            "peak_energy_bin": 4,
            "energy_contour": [0.08, 0.15, 0.31, 0.55, 1.0],
        }
    }

    rejected = _cadence(
        "Всё, что надвигается на меня, не заставит меня бояться.",
        early,
        sample_rate,
        expression_tier="emphatic",
        source_prosody=source_prosody,
    )
    accepted = _cadence(
        "Всё, что надвигается на меня, не заставит меня бояться.",
        late,
        sample_rate,
        expression_tier="emphatic",
        source_prosody=source_prosody,
    )

    assert rejected["hard_ok"] is False
    assert "source_emphasis_misplaced_early" in rejected["failures"]
    assert rejected["source_peak_dominance"] >= 0.18
    assert accepted["source_late_peak_expected"] is True
    assert "source_emphasis_misplaced_early" not in accepted["failures"]


def test_broad_or_middle_source_peak_does_not_hard_gate_russian_word_order() -> None:
    early, sample_rate = _shaped_chirp(180.0, 90.0, early_peak=True)
    broad_source = {
        "contour": {
            "peak_energy_bin": 3,
            "energy_contour": [0.30, 0.45, 0.86, 1.0, 0.94],
        }
    }
    middle_source = {
        "contour": {
            "peak_energy_bin": 2,
            "energy_contour": [0.20, 0.45, 1.0, 0.62, 0.35],
        }
    }

    broad = _cadence(
        "Всё, что надвигается на меня, не заставит меня бояться.",
        early,
        sample_rate,
        expression_tier="emphatic",
        source_prosody=broad_source,
    )
    middle = _cadence(
        "Всё, что надвигается на меня, не заставит меня бояться.",
        early,
        sample_rate,
        expression_tier="emphatic",
        source_prosody=middle_source,
    )

    assert broad["source_late_peak_expected"] is False
    assert middle["source_late_peak_expected"] is False
    assert "source_emphasis_misplaced_early" not in broad["failures"]
    assert "source_emphasis_misplaced_early" not in middle["failures"]
