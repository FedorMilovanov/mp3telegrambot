from __future__ import annotations

from tools.voxcpm2 import professional_audio_qa_v45 as qa
from tools.voxcpm2.direct_timbre_analysis import spectral_similarity


def _pitch(median: float, p90: float, voiced: float = 0.5) -> dict[str, float]:
    return {
        "f0_median": median,
        "f0_p90": p90,
        "voiced_ratio": voiced,
    }


def test_missing_timbre_profile_is_not_perfect_match() -> None:
    assert spectral_similarity({}, {}) == 0.0
    assert spectral_similarity({"bands": [0.5, 0.5]}, {}) == 0.0


def test_missing_reference_pitch_fails_closed() -> None:
    result = qa._voice_evaluation(
        {},
        profile_name="extended",
        reference=None,
        candidate=_pitch(110.0, 165.0),
    )
    assert result["passed"] is False
    assert result["reference_available"] is False
    assert result["failure_reason"] == "missing_reference_profile"


def test_extreme_high_register_is_rejected() -> None:
    result = qa._voice_evaluation(
        {},
        profile_name="extended",
        reference=_pitch(100.0, 145.0),
        candidate=_pitch(190.0, 270.0),
    )
    assert result["passed"] is False
    assert result["failure_reason"] == "pitch_ratio_out_of_range"


def test_compatible_register_is_accepted() -> None:
    result = qa._voice_evaluation(
        {},
        profile_name="extended",
        reference=_pitch(100.0, 145.0),
        candidate=_pitch(108.0, 155.0),
    )
    assert result["passed"] is True
    assert result["failure_reason"] == ""
