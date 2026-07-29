from __future__ import annotations

from tools.voxcpm2.professional_audio_qa_v45 import _voice_limits


def test_job12_natural_expression_is_not_rejected() -> None:
    # Exact ratios reported by job #12. Both are normal voiced expressive
    # variation and should not be rejected by the old flat 1.25 median ceiling.
    for median_ratio, p90_ratio in ((1.324324, 1.181818), (1.306667, 1.322034)):
        item = {
            "reference_profile": "composite",
            "expression_tier": "emphatic",
            "expression_score": 0.82,
            "source_prosody": {"f0_median": 128.0, "f0_p90": 170.0},
        }
        limits = _voice_limits(
            item,
            profile_name="composite",
            reference_median=105.0,
            reference_p90=145.0,
        )
        assert limits["min_median_ratio"] <= median_ratio <= limits["max_median_ratio"]
        assert limits["min_p90_ratio"] <= p90_ratio <= limits["max_p90_ratio"]


def test_old_arabic_like_badcase_still_fails_voiced_gate() -> None:
    item = {
        "reference_profile": "composite",
        "expression_tier": "passionate",
        "expression_score": 1.20,
        "source_prosody": {"f0_median": 130.0, "f0_p90": 175.0},
    }
    limits = _voice_limits(
        item,
        profile_name="composite",
        reference_median=105.0,
        reference_p90=145.0,
    )
    median_ratio = 1.477273
    p90_ratio = 1.230634
    voiced_ratio = 0.074074
    voice_passed = bool(
        voiced_ratio >= 0.12
        and limits["min_median_ratio"] <= median_ratio <= limits["max_median_ratio"]
        and limits["min_p90_ratio"] <= p90_ratio <= limits["max_p90_ratio"]
    )
    assert not voice_passed


def test_calm_profile_remains_tighter_than_expressive_profile() -> None:
    calm = _voice_limits(
        {"expression_tier": "warm", "expression_score": -0.2, "source_prosody": {}},
        profile_name="extended",
        reference_median=105.0,
        reference_p90=145.0,
    )
    expressive = _voice_limits(
        {"expression_tier": "emphatic", "expression_score": 0.8, "source_prosody": {}},
        profile_name="composite",
        reference_median=105.0,
        reference_p90=145.0,
    )
    assert calm["max_median_ratio"] < expressive["max_median_ratio"]
    assert calm["max_p90_ratio"] < expressive["max_p90_ratio"]
